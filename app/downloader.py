"""yt-dlp 를 이용한 영상 정보 조회 및 다운로드 (진행률·취소 지원)."""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .logging_setup import get_logger
from .utils import sanitize_filename, unique_path

log = get_logger()


class CancelledError(Exception):
    pass


class DownloadFailed(Exception):
    """사용자에게 보여줄 한국어 메시지(message)와 상세 로그(detail)를 담는 예외."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message, self.detail = message, detail


# ------------------------------------------------------------------ 오류 메시지 변환

SSL_HINT = ("보안 연결(SSL 인증서) 확인에 실패했습니다.\n"
            "학교·회사 등의 보안 장비가 인터넷 연결을 검사하고 있을 때 주로 생깁니다.\n"
            "[설정 > 네트워크 호환 모드]를 켜고 다시 시도하세요.")

_ERROR_MAP = [
    (("certificate verify failed", "ssl: ", "sslerror", "ee certificate key too weak",
      "unable to get local issuer certificate", "self-signed certificate", "self signed certificate"),
     SSL_HINT),
    (("private video",), "비공개 영상입니다. 소유자가 공개한 영상만 받을 수 있습니다."),
    (("video unavailable", "this video is unavailable", "has been removed",
      "no longer available", "account associated with this video has been terminated"),
     "삭제되었거나 볼 수 없는 영상입니다."),
    (("members-only", "join this channel", "members only"),
     "채널 멤버십 전용 영상이라 받을 수 없습니다."),
    (("sign in to confirm your age", "age-restricted", "inappropriate for some users"),
     "연령 제한 영상이라 로그인 없이 받을 수 없습니다."),
    (("not a bot", "sign in to confirm"),
     "유튜브가 자동화 요청으로 판단해 차단했습니다. 잠시 후 다시 시도하거나 yt-dlp를 최신 버전으로 업데이트하세요."),
    (("not available in your country", "geo restrict", "blocked it in your country"),
     "현재 지역에서 볼 수 없는 영상입니다."),
    (("premieres in", "live event will begin", "this live event"),
     "아직 시작하지 않은 실시간/최초 공개 영상입니다."),
    (("drm", "protected"), "DRM으로 보호된 콘텐츠는 지원하지 않습니다."),
    (("requested format is not available",),
     "선택한 화질을 받을 수 없습니다. 다른 화질을 선택하세요."),
    (("http error 403", "403: forbidden"),
     "서버가 접근을 거부했습니다(403). yt-dlp를 업데이트하거나 잠시 후 다시 시도하세요."),
    (("http error 429", "too many requests"),
     "요청이 너무 많아 일시적으로 차단되었습니다. 잠시 후 다시 시도하세요."),
    (("getaddrinfo", "name or service not known", "unable to download webpage",
      "timed out", "connection reset", "network is unreachable", "urlopen error",
      "temporary failure in name resolution", "connectionerror", "remote end closed",
      "unable to download api page", "unable to connect to proxy", "connection refused",
      "failed to establish a new connection"),
     "네트워크에 연결할 수 없습니다. 인터넷 연결을 확인하세요."),
    (("ffmpeg", "ffprobe"),
     "FFmpeg 관련 오류입니다. FFmpeg 설치 상태를 확인하세요."),
    (("no space left", "disk full"), "디스크 공간이 부족합니다."),
    (("permission denied", "access is denied"),
     "저장 폴더에 쓸 권한이 없습니다. 다른 폴더를 선택하세요."),
]


def friendly_error(raw: str) -> str:
    low = (raw or "").lower()
    for keys, msg in _ERROR_MAP:
        if any(k in low for k in keys):
            return msg
    return "처리 중 오류가 발생했습니다. 상세 로그를 확인하세요."


class _YdlLogger:
    """yt-dlp 로그를 앱 로그로 전달."""

    def __init__(self, on_log: Callable[[str], None] | None = None):
        self.on_log = on_log
        self.errors: list[str] = []

    def debug(self, msg):
        if not msg.startswith("[debug]"):
            log.debug("yt-dlp: %s", msg)

    def info(self, msg):
        log.info("yt-dlp: %s", msg)

    def warning(self, msg):
        log.warning("yt-dlp: %s", msg)
        if self.on_log:
            self.on_log("경고: " + msg)

    def error(self, msg):
        log.error("yt-dlp: %s", msg)
        self.errors.append(msg)


def _js_runtimes() -> dict:
    """프로그램 폴더에 deno.exe 가 있으면 그것을, 없으면 PATH 의 deno/node 를 사용."""
    from .utils import app_base_dir
    exe = "deno.exe" if os.name == "nt" else "deno"
    for p in (app_base_dir() / exe, app_base_dir() / "deno" / exe):
        if p.is_file():
            return {"deno": {"path": str(p)}, "node": {}}
    return {"deno": {}, "node": {}}


_compat_patched = False
_compat_enabled = False


def set_network_compat(enabled: bool) -> None:
    """학교·회사 보안망 호환 모드.

    보안 장비가 HTTPS 를 검사하며 약한 키(예: RSA 1024비트)의 인증서로 바꿔 끼우면
    OpenSSL 기본 보안 수준(SECLEVEL=2)에서 'EE certificate key too weak' 오류가 난다.
    이 모드에서는
      - 인증서 신뢰 목록으로 certifi 대신 Windows 인증서 저장소를 사용하고
        (기관이 PC에 설치해 둔 보안 장비 인증서를 신뢰하기 위함)
      - OpenSSL 보안 수준을 낮춰 약한 키의 인증서를 허용한다.
    인증서 검증 자체(신뢰할 수 있는 발급자인지, 도메인이 맞는지)는 끄지 않는다.
    """
    global _compat_patched, _compat_enabled
    _compat_enabled = enabled
    if not enabled or _compat_patched:
        return
    try:
        from yt_dlp.networking import common as ydl_common
    except ImportError:
        return
    original = ydl_common.make_ssl_context

    def patched(*args, **kwargs):
        ctx = original(*args, **kwargs)
        if _compat_enabled:
            try:
                ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
            except Exception:  # noqa: BLE001
                log.warning("SSL 보안 수준 조정 실패")
        return ctx

    ydl_common.make_ssl_context = patched
    _compat_patched = True


def _base_opts(ffmpeg_folder: str | None, logger: _YdlLogger) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "noplaylist": True,          # watch?v=..&list=.. → 그 영상 하나만
        "playlist_items": "1",       # 재생목록 전용 링크 → 첫 영상만
        "logger": logger,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "windowsfilenames": True,
        # 최신 yt-dlp 는 유튜브 처리에 JS 런타임(deno 권장)이 필요할 수 있음
        "js_runtimes": _js_runtimes(),
    }
    if ffmpeg_folder:
        opts["ffmpeg_location"] = ffmpeg_folder
    if _compat_enabled:
        opts["compat_opts"] = {"no-certifi"}   # 운영체제(Windows) 인증서 저장소 사용
    return opts


# ------------------------------------------------------------------ 정보 조회

@dataclass
class VideoInfo:
    id: str
    title: str
    channel: str
    duration: float | None
    thumbnail: str | None
    webpage_url: str
    heights: list[int] = field(default_factory=list)
    note: str = ""  # 재생목록에서 첫 영상만 선택한 경우 안내


def _pick_thumbnail(info: dict) -> str | None:
    thumbs = info.get("thumbnails") or []
    jpgs = [t for t in thumbs if str(t.get("url", "")).split("?")[0].endswith(".jpg")]
    for pool in (jpgs, thumbs):
        best = sorted(pool, key=lambda t: (t.get("preference") or 0, t.get("width") or 0))
        if best:
            return best[-1].get("url")
    return info.get("thumbnail")


def fetch_info(url: str, ffmpeg_folder: str | None = None) -> VideoInfo:
    import yt_dlp

    logger = _YdlLogger()
    opts = _base_opts(ffmpeg_folder, logger) | {"skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # yt_dlp.utils.DownloadError 등
        raw = str(e)
        raise DownloadFailed(friendly_error(raw), raw) from e
    if info is None:
        raise DownloadFailed("영상 정보를 가져오지 못했습니다.", "\n".join(logger.errors))

    note = ""
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise DownloadFailed("재생목록에서 받을 수 있는 영상을 찾지 못했습니다.")
        info = entries[0]
        note = "재생목록 링크입니다. 첫 번째 영상만 처리합니다."

    if info.get("is_live"):
        raise DownloadFailed("실시간 방송 중인 영상은 지원하지 않습니다. 방송 종료 후 다시 시도하세요.")
    if info.get("_has_drm") or info.get("has_drm"):
        raise DownloadFailed("DRM으로 보호된 콘텐츠는 지원하지 않습니다.")

    heights = sorted({f["height"] for f in info.get("formats") or []
                      if f.get("height") and f.get("vcodec") not in (None, "none")},
                     reverse=True)
    return VideoInfo(
        id=info.get("id", ""),
        title=info.get("title") or "제목 없음",
        channel=info.get("channel") or info.get("uploader") or "-",
        duration=info.get("duration"),
        thumbnail=_pick_thumbnail(info),
        webpage_url=info.get("webpage_url") or url,
        heights=heights,
        note=note,
    )


# ------------------------------------------------------------------ 다운로드

def build_format(height: int | None, prefer_h264: bool) -> str:
    """yt-dlp 포맷 선택 문자열. 영상+음성을 따로 받아 병합하고, 없으면 단일 파일."""
    h = f"[height<={height}]" if height else ""
    parts = []
    if prefer_h264:
        parts.append(f"bv*{h}[vcodec^=avc1]+ba[ext=m4a]")
    parts += [f"bv*{h}+ba", f"b{h}", "bv*+ba", "b"]
    return "/".join(parts)


@dataclass
class Progress:
    phase: str             # "download" | "merge" | "done"
    percent: float         # 0~100 (전체 기준)
    speed: float | None    # bytes/s
    eta: float | None      # s
    downloaded: float
    total: float | None
    label: str             # "영상 1/2" 등


def _make_record_pp():
    from yt_dlp.postprocessor.common import PostProcessor

    class RecordFormats(PostProcessor):
        """다운로드 직전에 선택된 포맷(영상/음성) 목록을 기록한다."""

        def __init__(self, owner: "Downloader"):
            super().__init__()
            self.owner = owner

        def run(self, info):
            self.owner._requested = list(info.get("requested_formats") or [info])
            return [], info

    return RecordFormats


def _RecordFormats(owner):  # noqa: N802 - yt-dlp 는 필요할 때만 import
    return _make_record_pp()(owner)


class Downloader:
    def __init__(self, url: str, out_dir: str, height: int | None, prefer_h264: bool,
                 ffmpeg_folder: str, title_hint: str = "",
                 on_progress: Callable[[Progress], None] | None = None,
                 on_log: Callable[[str], None] | None = None):
        self.url, self.out_dir, self.height = url, out_dir, height
        self.prefer_h264, self.ffmpeg_folder, self.title_hint = prefer_h264, ffmpeg_folder, title_hint
        self.on_progress = on_progress or (lambda p: None)
        self.on_log = on_log or (lambda s: None)
        self._cancel = threading.Event()
        self._streams: list[str] = []   # 받는 순서대로 format_id
        self._last_emit = 0.0
        self._announced = False
        self._requested: list[dict] = []   # 병합할 스트림 목록 (before_dl 단계에서 기록)

    def cancel(self) -> None:
        self._cancel.set()

    # yt-dlp 가 다운로드 중 수시로 호출
    def _hook(self, d: dict) -> None:
        if self._cancel.is_set():
            raise CancelledError()
        if d.get("status") != "downloading":
            return
        fid = (d.get("info_dict") or {}).get("format_id", "") or "?"
        new_stream = fid not in self._streams
        if new_stream:
            self._streams.append(fid)
        info = d.get("info_dict") or {}
        req = self._requested
        n_req = max(len(req), 1)
        if len(self._streams) == 1 and not self._announced:
            self._announced = True
            desc = " + ".join(f"{f.get('format_id')}({f.get('vcodec')}/{f.get('acodec')})"
                              for f in req) or f"{fid}({info.get('vcodec')}/{info.get('acodec')})"
            self.on_log(f"선택된 포맷: {desc}")
        idx = self._streams.index(fid)
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        done = d.get("downloaded_bytes") or 0
        frac = (done / total) if total else 0.0
        overall = (idx + min(frac, 1.0)) / n_req * 100
        now = time.monotonic()
        if not new_stream and now - self._last_emit < 0.2:   # 화면 갱신은 0.2초 간격으로
            return
        self._last_emit = now
        label = f"스트림 {idx + 1}/{n_req}" if n_req > 1 else "다운로드"
        self.on_progress(Progress("download", overall, d.get("speed"), d.get("eta"),
                                  done, total, label))

    def _pp_hook(self, d: dict) -> None:
        if self._cancel.is_set():
            raise CancelledError()
        if d.get("status") == "started" and d.get("postprocessor") in ("Merger", "FFmpegMerger"):
            self.on_progress(Progress("merge", 100, None, None, 0, None, "영상·음성 병합 중"))
            self.on_log("영상과 음성을 병합하는 중...")

    def run(self) -> str:
        import yt_dlp

        out_dir = Path(self.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / f".ytcutter_tmp_{uuid.uuid4().hex[:8]}"
        tmp.mkdir()
        logger = _YdlLogger(self.on_log)
        opts = _base_opts(self.ffmpeg_folder, logger) | {
            "format": build_format(self.height, self.prefer_h264),
            "merge_output_format": "mp4" if self.prefer_h264 else "mp4/mkv",
            "outtmpl": {"default": str(tmp / "%(id)s.%(ext)s")},
            "progress_hooks": [self._hook],
            "postprocessor_hooks": [self._pp_hook],
            "continuedl": False,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.add_post_processor(_RecordFormats(self), when="before_dl")
                info = ydl.extract_info(self.url, download=True)
            if info and info.get("_type") == "playlist":
                info = next((e for e in info.get("entries") or [] if e), None)
            if not info:
                raise DownloadFailed("영상을 다운로드하지 못했습니다.", "\n".join(logger.errors))
            if self._cancel.is_set():
                raise CancelledError()
            downloads = info.get("requested_downloads") or []
            src = Path(downloads[0]["filepath"]) if downloads else None
            if not src or not src.exists():
                files = [p for p in tmp.iterdir() if p.is_file() and not p.name.endswith(".part")]
                if len(files) != 1:
                    raise DownloadFailed("다운로드한 파일을 찾을 수 없습니다.", str(files))
                src = files[0]
            title = sanitize_filename(info.get("title") or self.title_hint or info.get("id", "video"))
            final = unique_path(out_dir / f"{title}{src.suffix}")
            shutil.move(str(src), str(final))
            self.on_progress(Progress("done", 100, None, None, 0, None, "완료"))
            return str(final)
        except CancelledError:
            raise
        except DownloadFailed:
            raise
        except Exception as e:
            if self._cancel.is_set():
                raise CancelledError() from e
            raw = str(e) + ("\n" + "\n".join(logger.errors) if logger.errors else "")
            log.exception("다운로드 실패")
            raise DownloadFailed(friendly_error(raw), raw) from e
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
