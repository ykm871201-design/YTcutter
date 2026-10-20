"""사용자 설정 저장/불러오기 (JSON). 위치: %APPDATA%\\YTCutter\\settings.json"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

from . import APP_ID


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    d = Path(base) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_save_dir() -> str:
    vids = Path.home() / "Videos"
    if not vids.exists():
        vids = Path.home() / "Downloads"
    return str(vids / "YTCutter")


@dataclass
class Settings:
    save_dir: str = field(default_factory=default_save_dir)
    ffmpeg_path: str = ""          # 사용자가 직접 지정한 ffmpeg.exe (비우면 자동 탐색)
    cut_mode: str = "accurate"     # accurate | fast
    output: str = "video"          # video | mp3 | m4a | wav | acopy (ffmpeg_tools.OUTPUTS)
    prefer_h264: bool = True       # 다운로드 시 H.264(MP4) 호환 포맷 우선
    crf: int = 20                  # 정확한 컷 화질 (낮을수록 고화질, 18~23 권장)
    preset: str = "veryfast"       # x264 인코딩 속도
    network_compat: bool = False   # 학교·회사 보안망 호환 모드 (SSL 인증서 오류 대응)

    @classmethod
    def load(cls) -> "Settings":
        f = data_dir() / "settings.json"
        s = cls()
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        f = data_dir() / "settings.json"
        try:
            f.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                         encoding="utf-8")
        except OSError:
            pass
