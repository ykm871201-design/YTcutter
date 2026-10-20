"""시간 형식 변환, 파일 이름 처리, URL 검사 등 순수 함수 모음 (GUI 의존성 없음)."""
from __future__ import annotations

import os
import re
import sys
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------- 시간 형식

_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(?:(\d{1,2}):)?(\d{1,2}(?:[.,]\d{1,3})?)\s*$")


class TimeFormatError(ValueError):
    pass


def parse_time(text: str) -> float:
    """'HH:MM:SS.mmm' / 'MM:SS' / 'SS.mmm' 을 초(float)로 변환한다.

    잘못된 형식이면 TimeFormatError 를 발생시킨다.
    """
    if text is None:
        raise TimeFormatError("시간이 비어 있습니다.")
    t = text.strip()
    if not t:
        raise TimeFormatError("시간이 비어 있습니다.")
    parts = t.replace(",", ".").split(":")
    if len(parts) > 3:
        raise TimeFormatError(f"시간 형식이 올바르지 않습니다: {text}")
    try:
        sec_part = parts[-1]
        if not re.fullmatch(r"\d+(\.\d{1,3})?", sec_part):
            raise ValueError
        seconds = float(sec_part)
        minutes = 0
        hours = 0
        if len(parts) >= 2:
            if not re.fullmatch(r"\d+", parts[-2]):
                raise ValueError
            minutes = int(parts[-2])
            if seconds >= 60:
                raise ValueError
        if len(parts) == 3:
            if not re.fullmatch(r"\d+", parts[0]):
                raise ValueError
            hours = int(parts[0])
            if minutes >= 60:
                raise ValueError
    except ValueError:
        raise TimeFormatError(
            f"시간 형식이 올바르지 않습니다: '{text}' (예: 00:01:23.500)"
        ) from None
    return hours * 3600 + minutes * 60 + seconds


def format_time(seconds: float | None, with_ms: bool = True) -> str:
    """초를 'HH:MM:SS.mmm' 으로 변환한다."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    if with_ms:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def validate_range(start: float, end: float, duration: float | None) -> str | None:
    """구간이 올바르면 None, 아니면 한국어 오류 메시지를 반환한다."""
    if start < 0:
        return "시작 시간은 0 이상이어야 합니다."
    if end <= start:
        return "종료 시간은 시작 시간보다 커야 합니다."
    if duration is not None and duration > 0 and end > duration + 0.0005:
        return f"종료 시간이 영상 길이({format_time(duration)})를 넘습니다."
    if end - start < 0.1:
        return "구간 길이가 너무 짧습니다(최소 0.1초)."
    return None


# ---------------------------------------------------------------- 파일 이름

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """Windows 에서 사용할 수 없는 문자를 제거한 안전한 파일 이름 (확장자 제외)."""
    name = _INVALID_CHARS.sub("_", name or "")
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if not name:
        name = "video"
    if name.upper() in _RESERVED:
        name = f"_{name}"
    return name[:max_len].rstrip(". ") or "video"


def unique_path(path: str | os.PathLike) -> Path:
    """이미 존재하면 '이름 (1).ext', '이름 (2).ext' ... 형태로 새 경로를 제안한다."""
    p = Path(path)
    if not p.exists():
        return p
    stem, suffix, parent = p.stem, p.suffix, p.parent
    i = 1
    while True:
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


# ---------------------------------------------------------------- URL

_YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def normalize_youtube_url(url: str) -> tuple[str | None, str | None]:
    """유튜브 URL을 검사한다.

    반환: (정규화된 URL, 오류 메시지). 성공이면 오류는 None.
    재생목록이 섞인 링크(watch?v=..&list=..)는 해당 영상 하나만 가리키도록 정리한다.
    """
    u = (url or "").strip()
    if not u:
        return None, "URL을 입력하세요."
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    try:
        pr = urlparse(u)
    except ValueError:
        return None, "올바른 URL 형식이 아닙니다."
    host = (pr.hostname or "").lower()
    if host not in _YT_HOSTS:
        return None, "유튜브 링크(youtube.com 또는 youtu.be)만 지원합니다."
    qs = parse_qs(pr.query)
    path = pr.path or "/"

    vid = None
    if host.endswith("youtu.be"):
        vid = path.strip("/").split("/")[0]
    elif path == "/watch":
        vid = (qs.get("v") or [None])[0]
    else:
        m = re.match(r"^/(shorts|live|embed|v)/([^/?#]+)", path)
        if m:
            vid = m.group(2)
        elif path == "/playlist" and qs.get("list"):
            # 재생목록 전용 링크: 첫 번째 영상만 처리 (downloader 에서 처리)
            return f"https://www.youtube.com/playlist?list={qs['list'][0]}", None

    if not vid or not _VIDEO_ID_RE.match(vid):
        return None, "영상 링크를 찾을 수 없습니다. 영상 페이지의 주소를 입력하세요."
    return f"https://www.youtube.com/watch?v={vid}", None


# ---------------------------------------------------------------- 기타

def app_base_dir() -> Path:
    """실행 파일(또는 main.py) 이 있는 폴더."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def subprocess_flags() -> dict:
    """Windows 에서 외부 프로세스 실행 시 콘솔 창이 뜨지 않도록 하는 옵션."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def open_path(path: str | os.PathLike, select: bool = False) -> None:
    """파일/폴더를 운영체제 기본 프로그램으로 연다. select=True 면 탐색기에서 선택."""
    p = str(Path(path))
    if os.name == "nt":
        if select:
            subprocess.Popen(["explorer", "/select,", p])
        else:
            os.startfile(p)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", p] if select else ["open", p])
    else:
        target = str(Path(p).parent) if select else p
        subprocess.Popen(["xdg-open", target])


def human_bytes(n: float | None) -> str:
    if not n:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_eta(sec: float | None) -> str:
    if sec is None:
        return "-"
    sec = int(sec)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
