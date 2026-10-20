"""yt-dlp 업데이트.

유튜브가 자주 바뀌어서 yt-dlp 가 오래되면 다운로드가 막히는 경우가 많다.
- 소스로 실행할 때(python main.py): 가상환경에 pip install -U "yt-dlp[default]"
- exe 로 실행할 때: exe 안에 묶인 yt-dlp 는 바꿀 수 없으므로 PyPI 에서 최신 휠(yt-dlp, yt-dlp-ejs)을
  받아 %APPDATA%\\YTCutter\\yt-dlp\\<버전>\\ 에 풀어 두고, 다음 실행부터 그 폴더를 sys.path 맨 앞에
  넣어 묶인 것 대신 사용한다. (PyInstaller 6 은 sys.path 순서대로 모듈을 찾으므로 앞의 폴더가 우선)
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import traceback
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import VERSION
from .config import data_dir
from .logging_setup import get_logger
from .utils import subprocess_flags

log = get_logger()

_STATE = "current.json"
active_version: str | None = None   # activate() 로 등록한 업데이트 버전 (없으면 None)


class UpdateFailed(Exception):
    """사용자에게 보여줄 한국어 메시지(message)와 상세 로그(detail)를 담는 예외."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message, self.detail = message, detail


@dataclass
class UpdateResult:
    before: str
    after: str

    @property
    def updated(self) -> bool:
        return version_tuple(self.after) > version_tuple(self.before)


def version_tuple(v: str) -> tuple[int, ...]:
    """'2026.08.19' / '2026.8.19' 처럼 표기가 달라도 비교할 수 있게 숫자 튜플로 변환."""
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def current_version() -> str:
    """지금 실행 중인 yt-dlp 버전."""
    from yt_dlp.version import __version__
    return __version__


# ------------------------------------------------------------------ exe: 받아 둔 업데이트 사용

def update_root() -> Path:
    return data_dir() / "yt-dlp"


def activate(root: Path | None = None) -> str | None:
    """받아 둔 yt-dlp 를 묶인 것보다 먼저 쓰도록 sys.path 맨 앞에 넣는다.

    yt_dlp 를 import 하기 전에(main.py 맨 앞에서) 불러야 한다. 등록한 버전을 돌려준다.
    """
    global active_version
    root = root or update_root()
    try:
        state = json.loads((root / _STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if state.get("app_version") != VERSION:
        # 프로그램을 새로 받았으면 그 안에 든 yt-dlp 가 더 새것일 수 있으므로 이전 업데이트는 버린다.
        shutil.rmtree(root, ignore_errors=True)
        return None
    ver = str(state.get("version", ""))
    site = root / ver
    if not re.fullmatch(r"\d[\d.]*", ver) or not (site / "yt_dlp").is_dir():
        return None
    for old in root.iterdir():   # 이전 업데이트·실패한 설치 정리
        if old.is_dir() and old != site:
            shutil.rmtree(old, ignore_errors=True)
    sys.path.insert(0, str(site))
    active_version = ver
    return ver


def install_wheels(root: Path, version: str, wheels: list[tuple[str, bytes, str]]) -> Path:
    """받은 휠(파일 이름, 내용, sha256)들을 root/<version>/ 에 풀고 다음 실행부터 쓰도록 기록한다."""
    if not re.fullmatch(r"\d[\d.]*", version):
        raise UpdateFailed("yt-dlp 버전 정보가 올바르지 않습니다.", version)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f"{version}.part"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        for name, data, sha256 in wheels:
            if hashlib.sha256(data).hexdigest() != sha256:
                raise UpdateFailed("받은 파일이 손상되었습니다. 다시 시도하세요.", f"{name}: sha256 불일치")
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for m in zf.namelist():
                    if m.startswith(("/", "\\")) or ".." in Path(m).parts:
                        raise UpdateFailed("받은 파일이 올바르지 않습니다.", f"{name}: {m}")
                zf.extractall(tmp)
        site = root / version
        shutil.rmtree(site, ignore_errors=True)
        tmp.rename(site)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    state = root / f"{_STATE}.tmp"
    state.write_text(json.dumps({"version": version, "app_version": VERSION}), encoding="utf-8")
    os.replace(state, root / _STATE)
    return site


# ------------------------------------------------------------------ PyPI

def _get(url: str, compat: bool, timeout: float = 30) -> bytes:
    ctx = ssl.create_default_context()   # Windows 에서는 Windows 인증서 저장소를 사용
    if compat:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")   # 네트워크 호환 모드와 동일하게 약한 키 허용
    req = urllib.request.Request(url, headers={"User-Agent": f"YTCutter/{VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def _pypi(name: str, version: str, compat: bool) -> dict:
    path = f"{name}/{version}/json" if version else f"{name}/json"
    return json.loads(_get(f"https://pypi.org/pypi/{path}", compat))


def _wheel(meta: dict) -> dict:
    for u in meta.get("urls") or []:
        if u.get("packagetype") == "bdist_wheel" and u.get("filename", "").endswith("-py3-none-any.whl"):
            return u
    raise UpdateFailed("PyPI 에서 설치 파일을 찾지 못했습니다. 잠시 후 다시 시도하세요.",
                       json.dumps(meta.get("info", {}).get("version")))


def ejs_requirement(requires_dist: list[str] | None) -> str | None:
    """yt-dlp[default] 가 요구하는 yt-dlp-ejs 버전. 고정 버전이 없으면 '' (최신), 요구하지 않으면 None."""
    for r in requires_dist or []:
        m = re.match(r"\s*yt-dlp-ejs\s*(?:==\s*([\w.]+))?[^;]*;.*extra\s*==\s*['\"]default['\"]", r)
        if m:
            return m.group(1) or ""
    return None


def _python_ok(requires_python: str | None) -> bool:
    m = re.search(r">=\s*(\d+)\.(\d+)", requires_python or "")
    return not m or sys.version_info[:2] >= (int(m.group(1)), int(m.group(2)))


def _update_frozen(compat: bool, on_log: Callable[[str], None], root: Path | None) -> UpdateResult:
    before = current_version()
    on_log("yt-dlp 최신 버전을 확인하는 중...")
    meta = _pypi("yt-dlp", "", compat)
    info = meta.get("info") or {}
    latest = str(info.get("version", ""))
    if version_tuple(latest) <= version_tuple(before):
        return UpdateResult(before, before)
    if not _python_ok(info.get("requires_python")):
        raise UpdateFailed("최신 yt-dlp 는 이 프로그램에 들어 있는 Python 으로 실행할 수 없습니다.\n"
                           "YT Cutter 새 버전을 받아 주세요.", str(info.get("requires_python")))

    metas = [meta]
    ejs = ejs_requirement(info.get("requires_dist"))
    if ejs is not None:
        metas.append(_pypi("yt-dlp-ejs", ejs, compat))
    wheels = []
    for m in metas:
        w = _wheel(m)
        on_log(f"{w['filename']} 받는 중...")
        wheels.append((w["filename"], _get(w["url"], compat, timeout=120), w["digests"]["sha256"]))
    install_wheels(root or update_root(), latest, wheels)
    return UpdateResult(before, latest)


# ------------------------------------------------------------------ 소스 실행: pip

def _run_python(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **subprocess_flags())


def _update_pip(on_log: Callable[[str], None]) -> UpdateResult:
    before = current_version()
    on_log('pip install -U "yt-dlp[default]" 실행 중...')
    r = _run_python(["-m", "pip", "install", "-U", "yt-dlp[default]"], timeout=900)
    log.info("pip 출력:\n%s%s", r.stdout, r.stderr)
    if r.returncode != 0:
        raise UpdateFailed(_friendly(r.stdout + r.stderr), r.stdout + r.stderr)
    r = _run_python(["-c", "from yt_dlp.version import __version__ as v; print(v)"], timeout=120)
    return UpdateResult(before, r.stdout.strip() or before)


# ------------------------------------------------------------------ 진입점

def _friendly(raw: str) -> str:
    from .downloader import friendly_error
    msg = friendly_error(raw)
    if msg == friendly_error(""):   # 알려진 원인이 아니면
        return "yt-dlp 업데이트에 실패했습니다. 상세 로그를 확인하세요."
    return msg


def run_update(compat: bool = False, on_log: Callable[[str], None] | None = None,
               root: Path | None = None) -> UpdateResult:
    """yt-dlp 를 최신 버전으로 업데이트한다. 적용하려면 프로그램을 다시 시작해야 한다."""
    on_log = on_log or (lambda s: None)
    try:
        if getattr(sys, "frozen", False):
            return _update_frozen(compat, on_log, root)
        return _update_pip(on_log)
    except UpdateFailed:
        raise
    except Exception as e:  # noqa: BLE001 - 네트워크·디스크 오류 등
        log.exception("yt-dlp 업데이트 실패")
        raise UpdateFailed(_friendly(str(e)), f"{e}\n{traceback.format_exc()}") from e
