"""실제 FFmpeg 로 컷을 수행하는 통합 테스트 (한글·공백 경로 포함)."""
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from app.cutter import CancelledError, CutRunner, Segment
from app.ffmpeg_tools import build_cut_command, fast_cut_container, find_ffmpeg, probe

FF = find_ffmpeg()
pytestmark = pytest.mark.skipif(FF is None, reason="ffmpeg 없음")


def make_video(path: Path, seconds=10, vcodec="libx264", acodec="aac", gop=120, fps=30):
    """GOP(키프레임 간격)를 길게 해서 빠른 컷의 키프레임 영향이 드러나게 한다."""
    extra = ["-g", str(gop)] if vcodec == "libx264" else []
    cmd = [FF.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate={fps}:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
           "-c:v", vcodec, *extra, "-pix_fmt", "yuv420p", "-c:a", acodec, "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    d = tmp_path_factory.mktemp("원본 폴더")
    return make_video(d / "테스트 영상 (원본).mp4")


def first_pts(path):
    r = subprocess.run([FF.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "frame=pts_time", "-read_intervals", "%+#1", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip().splitlines()[0])


def test_accurate_cut(src, tmp_path):
    out = tmp_path / "출력 폴더"
    info = probe(FF.ffprobe, str(src))
    before = src.stat().st_size
    progress = []
    r = CutRunner(FF, str(src), info, [Segment(2.5, 5.0), Segment(6.0, 8.0, "두번째 구간")],
                  str(out), "accurate", on_progress=lambda i, n, p: progress.append((i, p)))
    files = r.run()
    assert len(files) == 2 and all(Path(f).exists() for f in files)
    assert "두번째 구간" in Path(files[1]).name
    i1 = probe(FF.ffprobe, files[0])
    assert i1.vcodec == "h264" and i1.acodec == "aac"
    assert abs(i1.duration - 2.5) < 0.1
    assert src.stat().st_size == before           # 원본 보존
    assert progress and progress[-1][1] == 1.0
    assert not list(out.glob("*.part*"))           # 임시 파일 없음


def test_no_overwrite(src, tmp_path):
    info = probe(FF.ffprobe, str(src))
    segs = [Segment(1, 2)]
    a = CutRunner(FF, str(src), info, segs, str(tmp_path), "accurate").run()[0]
    b = CutRunner(FF, str(src), info, segs, str(tmp_path), "accurate").run()[0]
    assert a != b and Path(a).exists() and Path(b).exists()
    assert Path(b).stem.endswith("(1)")


def test_fast_cut_mp4(src, tmp_path):
    info = probe(FF.ffprobe, str(src))
    assert fast_cut_container(info) == (".mp4", None)
    f = CutRunner(FF, str(src), info, [Segment(5.0, 9.0)], str(tmp_path), "fast").run()[0]
    i = probe(FF.ffprobe, f)
    assert f.endswith(".mp4") and i.vcodec == "h264"
    # 스트림 복사이므로 원본 키프레임(0s, 4s, 8s)에 맞춰짐 → 길이가 요청보다 길 수 있음
    assert i.duration >= 3.9


def test_fast_cut_webm_container(tmp_path):
    src = make_video(tmp_path / "vp9 원본.webm", seconds=4, vcodec="libvpx-vp9", acodec="libopus")
    info = probe(FF.ffprobe, str(src))
    ext, note = fast_cut_container(info)
    assert ext == ".webm" and "MP4" in note
    logs = []
    f = CutRunner(FF, str(src), info, [Segment(1, 3)], str(tmp_path / "out"), "fast",
                  on_log=logs.append).run()[0]
    assert f.endswith(".webm") and Path(f).exists()
    assert any("WEBM" in m for m in logs)
    # 정확한 컷은 VP9 원본도 MP4(H.264+AAC)로 변환
    g = CutRunner(FF, str(src), info, [Segment(1, 3)], str(tmp_path / "out"), "accurate").run()[0]
    gi = probe(FF.ffprobe, g)
    assert g.endswith(".mp4") and gi.vcodec == "h264" and gi.acodec == "aac"


def test_cancel_cleans_up(tmp_path):
    src = make_video(tmp_path / "긴 영상.mp4", seconds=60)
    info = probe(FF.ffprobe, str(src))
    out = tmp_path / "취소 테스트"
    r = CutRunner(FF, str(src), info, [Segment(0, 60)], str(out), "accurate", preset="slow")
    started = threading.Event()
    r.on_progress = lambda i, n, p: started.set()
    t = threading.Timer(0, lambda: (started.wait(10), r.cancel()))
    t.start()
    with pytest.raises(CancelledError):
        r.run()
    t.join()
    assert list(out.iterdir()) == []   # 작성 중이던 파일 삭제됨


def test_ffmpeg_error_reported(src, tmp_path):
    bad = tmp_path / "깨진 파일.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(RuntimeError):
        probe(FF.ffprobe, str(bad))


def test_command_is_list_no_shell(src):
    cmd = build_cut_command("ffmpeg", "a b; rm -rf ~.mp4", "출력 & 파일.mp4", 1, 2, "accurate")
    assert isinstance(cmd, list)
    assert "a b; rm -rf ~.mp4" in cmd and "출력 & 파일.mp4" in cmd
