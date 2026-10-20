"""다운로더 통합 테스트.

유튜브 대신 로컬 HTTP 서버의 DASH 스트림(영상/음성 분리)을 yt-dlp 로 받아
진행률 · 영상+음성 병합 · 취소 · 임시 파일 정리 · 한글 경로를 검증한다.
"""
import functools
import http.server
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.downloader import (CancelledError, DownloadFailed, Downloader, build_format,
                            friendly_error)
from app.ffmpeg_tools import find_ffmpeg, probe

FF = find_ffmpeg()
pytestmark = pytest.mark.skipif(FF is None, reason="ffmpeg 없음")


class SlowHandler(http.server.SimpleHTTPRequestHandler):
    delay = 0.0

    def log_message(self, *a):
        pass

    def copyfile(self, source, outputfile):
        while chunk := source.read(64 * 1024):
            outputfile.write(chunk)
            if self.delay:
                time.sleep(self.delay)


@pytest.fixture(scope="module")
def dash_server(tmp_path_factory):
    d = tmp_path_factory.mktemp("dash")
    subprocess.run([FF.ffmpeg, "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=20",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-g", "60", "-b:v", "3M",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "dash",
                    "-adaptation_sets", "id=0,streams=v id=1,streams=a",
                    str(d / "manifest.mpd")], check=True)
    handler = functools.partial(SlowHandler, directory=str(d))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    yield f"http://127.0.0.1:{srv.server_address[1]}/manifest.mpd"
    srv.shutdown()


def test_download_and_merge(dash_server, tmp_path):
    SlowHandler.delay = 0.003   # 진행률이 여러 번 보고되도록 약간 느리게
    out = tmp_path / "다운로드 폴더"
    progress, logs = [], []
    dl = Downloader(dash_server, str(out), None, True, FF.folder, title_hint="테스트",
                    on_progress=progress.append, on_log=logs.append)
    try:
        path = dl.run()
    finally:
        SlowHandler.delay = 0
    info = probe(FF.ffprobe, path)
    assert path.endswith(".mp4")
    assert info.vcodec == "h264" and info.acodec == "aac"      # 병합됨
    assert abs(info.duration - 20) < 0.5
    phases = {p.phase for p in progress}
    assert "download" in phases and "done" in phases
    assert any(p.label.startswith("스트림 2/2") for p in progress)
    assert [p.name for p in out.iterdir()] == [Path(path).name]   # 임시 폴더 정리됨
    # 같은 영상을 다시 받으면 새 이름
    path2 = Downloader(dash_server, str(out), None, True, FF.folder).run()
    assert path2 != path and "(1)" in path2


def test_download_cancel(dash_server, tmp_path):
    SlowHandler.delay = 0.02
    out = tmp_path / "취소"
    dl = Downloader(dash_server, str(out), None, True, FF.folder)
    got = threading.Event()
    dl.on_progress = lambda p: got.set()
    threading.Thread(target=lambda: (got.wait(20), dl.cancel()), daemon=True).start()
    try:
        with pytest.raises(CancelledError):
            dl.run()
    finally:
        SlowHandler.delay = 0
    assert list(out.iterdir()) == []


def test_download_network_error(tmp_path):
    with pytest.raises(DownloadFailed) as ei:
        Downloader("http://127.0.0.1:9/none.mpd", str(tmp_path), None, True, FF.folder).run()
    assert "네트워크" in ei.value.message
    assert list(tmp_path.iterdir()) == []


def test_build_format():
    assert build_format(720, True).startswith("bv*[height<=720][vcodec^=avc1]+ba[ext=m4a]/")
    assert "avc1" not in build_format(None, False)


@pytest.mark.parametrize("raw,key", [
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "비공개"),
    ("ERROR: [youtube] x: Video unavailable. This video has been removed", "삭제"),
    ("ERROR: Sign in to confirm your age", "연령"),
    ("ERROR: Sign in to confirm you're not a bot", "차단"),
    ("Unable to download API page: Tunnel connection failed", "네트워크"),
    ("ERROR: Join this channel to get access to members-only content", "멤버십"),
    ("ERROR: unable to download video data: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify "
     "failed: EE certificate key too weak (_ssl.c:1082)", "네트워크 호환 모드"),
    ("something weird", "상세 로그"),
])
def test_friendly_error(raw, key):
    assert key in friendly_error(raw)
