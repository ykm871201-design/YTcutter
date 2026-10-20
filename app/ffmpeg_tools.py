"""FFmpeg/FFprobe 탐색, 미디어 정보 조회, 컷 명령 생성."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .utils import app_base_dir, subprocess_flags

EXE = ".exe" if os.name == "nt" else ""

FFMPEG_HELP = (
    "FFmpeg를 찾을 수 없습니다.\n\n"
    "다음 중 한 가지 방법으로 준비하세요.\n"
    "1) 명령 프롬프트에서:  winget install Gyan.FFmpeg  실행 후 프로그램 재시작\n"
    "2) https://www.gyan.dev/ffmpeg/builds/ 에서 'ffmpeg-release-essentials.zip'을 받아\n"
    "   압축을 풀고, bin 폴더의 ffmpeg.exe·ffprobe.exe 를 이 프로그램 폴더의\n"
    "   'ffmpeg' 폴더(없으면 만들기)에 넣기\n"
    "3) [설정 > FFmpeg 위치 지정]에서 ffmpeg.exe 를 직접 선택\n\n"
    "※ ffmpeg.exe 와 ffprobe.exe 가 같은 폴더에 있어야 합니다."
)


@dataclass
class FFmpegPaths:
    ffmpeg: str
    ffprobe: str

    @property
    def folder(self) -> str:
        return str(Path(self.ffmpeg).parent)


def find_ffmpeg(custom_path: str = "") -> FFmpegPaths | None:
    """ffmpeg/ffprobe 를 찾는다. 순서: 사용자 지정 → 프로그램 폴더 → PATH."""
    candidates: list[Path] = []
    if custom_path:
        cp = Path(custom_path)
        candidates.append(cp.parent if cp.is_file() else cp)
    base = app_base_dir()
    candidates += [base / "ffmpeg" / "bin", base / "ffmpeg", base]
    for folder in candidates:
        ff, fp = folder / f"ffmpeg{EXE}", folder / f"ffprobe{EXE}"
        if ff.is_file() and fp.is_file():
            return FFmpegPaths(str(ff), str(fp))
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ff and fp:
        return FFmpegPaths(ff, fp)
    return None


@dataclass
class MediaInfo:
    duration: float
    vcodec: str | None
    acodec: str | None
    width: int | None = None
    height: int | None = None
    container: str = ""


def probe(ffprobe: str, path: str) -> MediaInfo:
    """ffprobe 로 길이·코덱을 조회한다. 인수는 리스트로 전달(셸 미사용)."""
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=60,
                       **subprocess_flags())
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"영상 정보를 읽을 수 없습니다: {msg or '알 수 없는 오류'}")
    data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    fmt = data.get("format", {})
    vcodec = acodec = None
    w = h = None
    v_dur = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and vcodec is None \
                and not s.get("disposition", {}).get("attached_pic"):
            vcodec, w, h = s.get("codec_name"), s.get("width"), s.get("height")
            v_dur = s.get("duration")
        elif s.get("codec_type") == "audio" and acodec is None:
            acodec = s.get("codec_name")
    dur = float(fmt.get("duration") or v_dur or 0)
    return MediaInfo(dur, vcodec, acodec, w, h, fmt.get("format_name", ""))


# ------------------------------------------------------------ 빠른 컷 컨테이너 선택

MP4_VIDEO = {"h264", "hevc", "mpeg4", "av1"}
MP4_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac"}
WEBM_VIDEO = {"vp8", "vp9", "av1"}
WEBM_AUDIO = {"opus", "vorbis"}


def fast_cut_container(info: MediaInfo) -> tuple[str, str | None]:
    """빠른 컷(스트림 복사)에 사용할 확장자와 안내 메시지를 반환한다."""
    v, a = info.vcodec, info.acodec
    if (v is None or v in MP4_VIDEO) and (a is None or a in MP4_AUDIO):
        return ".mp4", None
    if (v is None or v in WEBM_VIDEO) and (a is None or a in WEBM_AUDIO):
        return ".webm", (f"원본 코덱({v or '-'}/{a or '-'})은 MP4와 호환성이 낮아 "
                         "빠른 컷은 WEBM으로 저장합니다. MP4가 필요하면 '정확한 컷'을 사용하세요.")
    return ".mkv", (f"원본 코덱({v or '-'}/{a or '-'})은 MP4에 넣을 수 없어 "
                    "빠른 컷은 MKV로 저장합니다. MP4가 필요하면 '정확한 컷'을 사용하세요.")


# ------------------------------------------------------------ 출력 형식 (영상 / 음원)

@dataclass(frozen=True)
class OutputSpec:
    key: str          # 설정 파일에 저장되는 값
    label: str        # 화면에 보이는 이름
    kind: str         # "video" | "audio"
    note: str = ""    # 화면 아래 설명


OUTPUTS: list[OutputSpec] = [
    OutputSpec("video", "영상 (MP4)", "video",
               "영상과 소리를 함께 저장합니다."),
    OutputSpec("mp3", "음원 MP3", "audio",
               "어디서나 재생되는 가장 무난한 음원 형식입니다."),
    OutputSpec("m4a", "음원 M4A (AAC)", "audio",
               "MP3보다 같은 용량에서 음질이 좋습니다."),
    OutputSpec("wav", "음원 WAV (무손실)", "audio",
               "음질 손실이 없지만 용량이 매우 큽니다. 음악 편집용."),
    OutputSpec("acopy", "음원 원본 그대로 (빠름)", "audio",
               "변환하지 않고 소리만 그대로 빼냅니다. 가장 빠르고 음질 손실이 없습니다."),
]

OUTPUT_BY_KEY = {o.key: o for o in OUTPUTS}

# '원본 그대로' 추출 시 코덱별 확장자
_COPY_EXT = {
    "aac": ".m4a", "alac": ".m4a", "mp3": ".mp3", "opus": ".opus", "vorbis": ".ogg",
    "flac": ".flac", "ac3": ".ac3", "eac3": ".eac3", "pcm_s16le": ".wav",
    "pcm_s24le": ".wav", "pcm_f32le": ".wav", "wmav2": ".wma",
}


class NoAudioError(RuntimeError):
    """영상에 소리가 없을 때."""


class MissingEncoderError(RuntimeError):
    """FFmpeg 빌드에 필요한 인코더가 없을 때."""


_encoder_cache: dict[str, set[str]] = {}


def available_encoders(ffmpeg: str) -> set[str]:
    """이 FFmpeg 빌드가 가진 인코더 이름 목록 (한 번만 조회해 기억)."""
    if ffmpeg in _encoder_cache:
        return _encoder_cache[ffmpeg]
    names: set[str] = set()
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=30, **subprocess_flags())
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 6 and not line.startswith(" ---"):
                names.add(parts[1])
    except (OSError, subprocess.SubprocessError):
        return set()   # 조회 실패 시에는 검사를 건너뛴다
    _encoder_cache[ffmpeg] = names
    return names


def check_encoder(ffmpeg: str, args: list[str]) -> None:
    """명령에 쓰일 인코더가 없으면 한국어 안내와 함께 예외를 낸다."""
    if "-c:a" not in args:
        return
    enc = args[args.index("-c:a") + 1]
    if enc == "copy":
        return
    names = available_encoders(ffmpeg)
    if names and enc not in names:
        raise MissingEncoderError(
            f"지금 설치된 FFmpeg에는 '{enc}' 변환 기능이 없습니다.\n"
            "다른 저장 형식(예: 음원 원본 그대로)을 선택하거나, "
            "FFmpeg를 full/essentials 빌드로 다시 설치하세요.")


def audio_output_plan(spec: OutputSpec, info: MediaInfo) -> tuple[str, list[str], str | None]:
    """음원 추출용 (확장자, ffmpeg 인수, 안내 메시지)를 돌려준다."""
    if info.acodec is None:
        raise NoAudioError("이 영상에는 소리가 없어 음원을 추출할 수 없습니다.")
    if spec.key == "mp3":
        return ".mp3", ["-c:a", "libmp3lame", "-q:a", "2"], None
    if spec.key == "m4a":
        return ".m4a", ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"], None
    if spec.key == "wav":
        return ".wav", ["-c:a", "pcm_s16le"], None
    # acopy: 원본 코덱 그대로 복사
    ext = _COPY_EXT.get(info.acodec)
    if ext:
        return ext, ["-c:a", "copy"], None
    return ".m4a", ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"], (
        f"원본 소리 형식({info.acodec})은 그대로 빼낼 수 없어 M4A(AAC)로 변환합니다.")


def build_audio_command(ffmpeg: str, src: str, dst: str, start: float, end: float,
                        args: list[str]) -> list[str]:
    """지정한 구간의 소리만 추출하는 ffmpeg 명령.

    -vn 으로 영상(앨범아트 포함)을 빼고 첫 번째 오디오 트랙만 담는다.
    오디오는 프레임이 짧아 복사(copy)해도 시작 지점이 거의 어긋나지 않는다.
    """
    dur = max(0.0, end - start)
    return [ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
            "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-map_metadata", "0", "-map_chapters", "-1",
            *args, "-progress", "pipe:1", "-nostats", dst]


# ------------------------------------------------------------ 명령 생성

def build_cut_command(ffmpeg: str, src: str, dst: str, start: float, end: float,
                      mode: str, crf: int = 20, preset: str = "veryfast",
                      has_video: bool = True, has_audio: bool = True) -> list[str]:
    """ffmpeg 컷 명령을 리스트로 만든다.

    - accurate: -ss 를 입력 앞에 두고 재인코딩 → FFmpeg 가 정확한 프레임부터 출력
    - fast    : 스트림 복사(-c copy). 시작점이 이전 키프레임으로 당겨질 수 있음
    """
    dur = max(0.0, end - start)
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y",
           "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
           "-map", "0:v:0?", "-map", "0:a:0?", "-sn", "-dn",
           "-map_metadata", "0", "-map_chapters", "-1"]
    if mode == "accurate":
        if has_video:
            cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                    "-pix_fmt", "yuv420p",
                    # 홀수 해상도는 yuv420p 인코딩이 실패하므로 짝수로 보정
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
        if has_audio:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        cmd += ["-movflags", "+faststart"]
    else:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
        if dst.lower().endswith(".mp4"):
            cmd += ["-movflags", "+faststart"]
    cmd += ["-progress", "pipe:1", "-nostats", dst]
    return cmd
