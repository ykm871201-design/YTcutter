"""음원 추출 기능 테스트 (실제 FFmpeg 사용)."""
import subprocess
from pathlib import Path

import pytest

from app.cutter import CutRunner, Segment
from app.ffmpeg_tools import (OUTPUT_BY_KEY, MissingEncoderError, NoAudioError,
                              audio_output_plan, build_audio_command, check_encoder,
                              find_ffmpeg, probe)

FF = find_ffmpeg()
pytestmark = pytest.mark.skipif(FF is None, reason="ffmpeg 없음")


def make(path: Path, *args: str) -> Path:
    subprocess.run([FF.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args, str(path)],
                   check=True)
    return path


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    """440Hz(0~3초) → 1760Hz(3~6초) 로 소리가 바뀌는 영상. 시작 지점 검증용."""
    d = tmp_path_factory.mktemp("음원 원본")
    return make(d / "소리 있는 영상.mp4",
                "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25:duration=6",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=1760:duration=3",
                "-filter_complex", "[1:a][2:a]concat=n=2:v=0:a=1[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest")


def dominant_hz(path: str, seconds: float = 0.5) -> int:
    """파일 앞부분의 주된 주파수(Hz). numpy 가 없으면 테스트를 건너뛴다."""
    np = pytest.importorskip("numpy")
    raw = subprocess.run([FF.ffmpeg, "-v", "error", "-i", str(path), "-t", str(seconds),
                          "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    assert x.size > 1000, "오디오를 읽지 못했습니다"
    return round(abs(np.fft.rfftfreq(len(x), 1 / 16000)[np.argmax(np.abs(np.fft.rfft(x)))]))


@pytest.mark.parametrize("key,ext,acodec", [
    ("mp3", ".mp3", "mp3"),
    ("m4a", ".m4a", "aac"),
    ("wav", ".wav", "pcm_s16le"),
    ("acopy", ".m4a", "aac"),
])
def test_extract_whole(src, tmp_path, key, ext, acodec):
    """영상 전체 음원 추출: 형식·길이가 맞고 영상 트랙이 없어야 한다."""
    info = probe(FF.ffprobe, str(src))
    before = src.stat().st_size
    files = CutRunner(FF, str(src), info, [Segment(0, info.duration)], str(tmp_path),
                      "accurate", output=key).run()
    out = Path(files[0])
    got = probe(FF.ffprobe, str(out))
    assert out.suffix == ext
    assert out.name == f"{src.stem}{ext}"      # 전체 저장이면 이름이 단순해짐
    assert got.vcodec is None                  # 영상·앨범아트 없음
    assert got.acodec == acodec
    assert abs(got.duration - info.duration) < 0.2
    assert src.stat().st_size == before        # 원본 보존
    assert not list(tmp_path.glob("*.part*"))


@pytest.mark.parametrize("key", ["mp3", "m4a", "wav", "acopy"])
def test_extract_segment_start_is_accurate(src, tmp_path, key):
    """3초부터 추출하면 바뀐 소리(1760Hz)로 시작해야 한다."""
    info = probe(FF.ffprobe, str(src))
    assert dominant_hz(str(src)) == pytest.approx(440, abs=30)
    out = CutRunner(FF, str(src), info, [Segment(3.0, 5.0)], str(tmp_path),
                    "accurate", output=key).run()[0]
    assert dominant_hz(out) == pytest.approx(1760, abs=60)
    assert abs(probe(FF.ffprobe, out).duration - 2.0) < 0.15


def test_multiple_segments_and_names(src, tmp_path):
    info = probe(FF.ffprobe, str(src))
    files = CutRunner(FF, str(src), info, [Segment(0, 2), Segment(3, 5, "하이라이트")],
                      str(tmp_path), "accurate", output="mp3").run()
    assert len(files) == 2
    assert all(f.endswith(".mp3") and Path(f).exists() for f in files)
    assert "하이라이트" in Path(files[1]).name
    assert Path(files[0]).name.startswith(src.stem)   # 일부 구간은 번호·시간이 붙음
    assert "_01_" in Path(files[0]).name


def test_no_audio_video(tmp_path):
    """소리가 없는 영상은 한국어 안내와 함께 거부하고 파일을 남기지 않는다."""
    src = make(tmp_path / "소리없는 영상.mp4", "-f", "lavfi", "-i",
               "testsrc2=size=160x120:rate=25:duration=3", "-c:v", "libx264",
               "-pix_fmt", "yuv420p")
    info = probe(FF.ffprobe, str(src))
    assert info.acodec is None
    out = tmp_path / "출력"
    with pytest.raises(NoAudioError) as ei:
        CutRunner(FF, str(src), info, [Segment(0, 3)], str(out), "accurate", output="mp3").run()
    assert "소리가 없어" in str(ei.value)
    assert not out.exists() or list(out.iterdir()) == []


def test_copy_container_by_codec(tmp_path):
    """'원본 그대로'는 코덱에 맞는 확장자를 고른다 (opus → .opus)."""
    src = make(tmp_path / "vp9 영상.webm",
               "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25:duration=4",
               "-f", "lavfi", "-i", "sine=duration=4",
               "-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus", "-shortest")
    info = probe(FF.ffprobe, str(src))
    assert audio_output_plan(OUTPUT_BY_KEY["acopy"], info)[0] == ".opus"
    out = CutRunner(FF, str(src), info, [Segment(1, 3)], str(tmp_path / "out"),
                    "accurate", output="acopy").run()[0]
    assert out.endswith(".opus") and probe(FF.ffprobe, out).acodec == "opus"
    # 알 수 없는 코덱은 M4A 로 변환하고 안내 문구를 준다
    from app.ffmpeg_tools import MediaInfo
    ext, args, note = audio_output_plan(OUTPUT_BY_KEY["acopy"],
                                        MediaInfo(5, "h264", "truehd"))
    assert ext == ".m4a" and "-c:a" in args and note and "M4A" in note


def test_missing_encoder_message():
    with pytest.raises(MissingEncoderError) as ei:
        check_encoder(FF.ffmpeg, ["-c:a", "존재하지않는인코더"])
    assert "FFmpeg" in str(ei.value)
    check_encoder(FF.ffmpeg, ["-c:a", "copy"])          # 복사는 검사 대상 아님
    check_encoder(FF.ffmpeg, ["-c:a", "libmp3lame"])    # 설치된 인코더는 통과


def test_command_is_list_no_shell():
    cmd = build_audio_command("ffmpeg", "a b; rm -rf ~.mp4", "출력 & 음원.mp3", 1, 3,
                              ["-c:a", "libmp3lame"])
    assert isinstance(cmd, list)
    assert "a b; rm -rf ~.mp4" in cmd and "출력 & 음원.mp3" in cmd
    assert "-vn" in cmd            # 영상·앨범아트 제외
