"""FFmpeg 로 구간 자르기 실행 (진행률·취소 지원). GUI 와 독립적인 로직."""
from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ffmpeg_tools import (OUTPUT_BY_KEY, FFmpegPaths, MediaInfo, audio_output_plan,
                           build_audio_command, build_cut_command, check_encoder,
                           fast_cut_container)
from .logging_setup import get_logger
from .utils import format_time, sanitize_filename, subprocess_flags, unique_path

log = get_logger()


class CancelledError(Exception):
    pass


@dataclass
class Segment:
    start: float
    end: float
    name: str = ""

    @property
    def length(self) -> float:
        return self.end - self.start


def output_path_for(src: str, out_dir: str, seg: Segment, index: int, ext: str,
                    whole: bool = False) -> Path:
    """'원본이름_01_000130.000-000245.500.mp4' 형태의 겹치지 않는 경로.

    whole=True (영상 전체를 한 덩어리로 저장할 때)면 '원본이름.mp3' 처럼 단순하게 짓는다.
    """
    base = sanitize_filename(Path(src).stem, 80)
    if whole:
        name = f"{base}{ext}" if not seg.name else f"{base}_{sanitize_filename(seg.name, 40)}{ext}"
        return unique_path(Path(out_dir) / name)
    label = sanitize_filename(seg.name, 40) if seg.name else \
        f"{format_time(seg.start).replace(':', '')}-{format_time(seg.end).replace(':', '')}"
    name = f"{base}_{index:02d}_{label}{ext}"
    return unique_path(Path(out_dir) / name)


class CutRunner:
    """여러 구간을 순서대로 잘라 각각 별도 파일로 저장한다."""

    def __init__(self, ff: FFmpegPaths, src: str, info: MediaInfo, segments: list[Segment],
                 out_dir: str, mode: str, crf: int = 20, preset: str = "veryfast",
                 output: str = "video",
                 on_progress: Callable[[int, int, float], None] | None = None,
                 on_log: Callable[[str], None] | None = None):
        self.ff, self.src, self.info = ff, src, info
        self.segments, self.out_dir, self.mode = segments, out_dir, mode
        self.crf, self.preset = crf, preset
        self.spec = OUTPUT_BY_KEY.get(output, OUTPUT_BY_KEY["video"])
        self.on_progress = on_progress or (lambda i, n, p: None)
        self.on_log = on_log or (lambda s: None)
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._audio_args: list[str] | None = None

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.kill()
                except OSError:
                    pass

    def run(self) -> list[str]:
        Path(self.out_dir).mkdir(parents=True, exist_ok=True)
        self._audio_args: list[str] | None = None
        if self.spec.kind == "audio":
            ext, self._audio_args, note = audio_output_plan(self.spec, self.info)
            check_encoder(self.ff.ffmpeg, self._audio_args)
            if note:
                self.on_log("안내: " + note)
        elif self.mode == "fast":
            ext, note = fast_cut_container(self.info)
            if note:
                self.on_log("안내: " + note)
        else:
            ext = ".mp4"
        results: list[str] = []
        n = len(self.segments)
        for i, seg in enumerate(self.segments, 1):
            if self._cancel.is_set():
                raise CancelledError()
            whole = n == 1 and seg.start <= 0.05 and seg.end >= self.info.duration - 0.05
            final = output_path_for(self.src, self.out_dir, seg, i, ext, whole)
            # 임시 이름으로 인코딩 후 완료되면 최종 이름으로 변경 (중간 파일이 남지 않도록)
            tmp = unique_path(final.with_name(final.stem + ".part" + ext))
            self._run_one(seg, str(tmp), i, n)
            os.replace(tmp, unique_path(final))
            results.append(str(final))
            self.on_log(f"저장 완료: {final}")
        return results

    def _run_one(self, seg: Segment, dst: str, i: int, n: int) -> None:
        if self._audio_args is not None:
            cmd = build_audio_command(self.ff.ffmpeg, self.src, dst,
                                      seg.start, seg.end, self._audio_args)
            work = "음원 추출 중"
        else:
            cmd = build_cut_command(
                self.ff.ffmpeg, self.src, dst, seg.start, seg.end, self.mode,
                self.crf, self.preset,
                has_video=self.info.vcodec is not None,
                has_audio=self.info.acodec is not None)
            work = "자르는 중"
        log.info("ffmpeg 실행: %s", cmd)
        self.on_log(f"[{i}/{n}] {format_time(seg.start)} ~ {format_time(seg.end)} {work}...")
        stderr_lines: list[str] = []
        with self._lock:
            if self._cancel.is_set():
                raise CancelledError()
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, **subprocess_flags())
        proc = self._proc

        def read_err():
            for raw in proc.stderr:  # type: ignore[union-attr]
                stderr_lines.append(raw.decode("utf-8", "replace").rstrip())
                del stderr_lines[:-200]
        t = threading.Thread(target=read_err, daemon=True)
        t.start()

        total = max(seg.length, 0.001)
        for raw in proc.stdout:  # type: ignore[union-attr]
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                self.on_progress(i, n, min(1.0, max(0.0, us / 1_000_000 / total)))
        proc.wait()
        t.join(timeout=2)
        with self._lock:
            self._proc = None

        if self._cancel.is_set():
            _safe_remove(dst)
            raise CancelledError()
        if proc.returncode != 0:
            _safe_remove(dst)
            detail = "\n".join(stderr_lines[-30:])
            log.error("ffmpeg 실패(%s):\n%s", proc.returncode, detail)
            raise RuntimeError(f"FFmpeg 오류(코드 {proc.returncode})\n{detail}")
        if not Path(dst).exists() or Path(dst).stat().st_size == 0:
            _safe_remove(dst)
            raise RuntimeError("출력 파일이 만들어지지 않았습니다. 구간을 확인하세요.")
        self.on_progress(i, n, 1.0)


def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        log.warning("임시 파일 삭제 실패: %s", path)
