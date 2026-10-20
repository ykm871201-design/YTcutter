"""무거운 작업(정보 조회·다운로드·컷)을 QThread 에서 실행해 UI 가 멈추지 않도록 한다."""
from __future__ import annotations

import traceback

from PySide6.QtCore import QThread, Signal

from . import cutter, downloader
from .ffmpeg_tools import FFmpegPaths, MissingEncoderError, NoAudioError, probe
from .logging_setup import get_logger

log = get_logger()


class InfoWorker(QThread):
    succeeded = Signal(object)        # VideoInfo
    failed = Signal(str, str)         # 사용자 메시지, 상세

    def __init__(self, url: str, ffmpeg_folder: str | None, parent=None):
        super().__init__(parent)
        self.url, self.ffmpeg_folder = url, ffmpeg_folder

    def run(self):
        try:
            self.succeeded.emit(downloader.fetch_info(self.url, self.ffmpeg_folder))
        except downloader.DownloadFailed as e:
            self.failed.emit(e.message, e.detail)
        except Exception as e:  # noqa: BLE001
            self.failed.emit("영상 정보를 가져오는 중 오류가 발생했습니다.",
                             f"{e}\n{traceback.format_exc()}")


class DownloadWorker(QThread):
    progress = Signal(object)         # downloader.Progress
    message = Signal(str)
    succeeded = Signal(str)           # 저장된 파일 경로
    failed = Signal(str, str)
    cancelled = Signal()

    def __init__(self, dl: downloader.Downloader, parent=None):
        super().__init__(parent)
        self.dl = dl
        dl.on_progress = self.progress.emit
        dl.on_log = self.message.emit

    def cancel(self):
        self.dl.cancel()

    def run(self):
        try:
            self.succeeded.emit(self.dl.run())
        except downloader.CancelledError:
            self.cancelled.emit()
        except downloader.DownloadFailed as e:
            self.failed.emit(e.message, e.detail)
        except Exception as e:  # noqa: BLE001
            log.exception("다운로드 작업 오류")
            self.failed.emit("다운로드 중 오류가 발생했습니다.", f"{e}\n{traceback.format_exc()}")


class ProbeWorker(QThread):
    succeeded = Signal(str, object)   # path, MediaInfo
    failed = Signal(str, str)

    def __init__(self, ffprobe: str, path: str, parent=None):
        super().__init__(parent)
        self.ffprobe, self.path = ffprobe, path

    def run(self):
        try:
            self.succeeded.emit(self.path, probe(self.ffprobe, self.path))
        except Exception as e:  # noqa: BLE001
            self.failed.emit("영상 파일을 분석할 수 없습니다. 손상되었거나 지원하지 않는 파일일 수 있습니다.",
                             str(e))


class CutWorker(QThread):
    progress = Signal(int, int, float)   # 현재 번호, 전체 개수, 0~1
    message = Signal(str)
    succeeded = Signal(list)
    failed = Signal(str, str)
    cancelled = Signal()

    def __init__(self, ff: FFmpegPaths, src: str, info, segments, out_dir: str,
                 mode: str, crf: int, preset: str, output: str = "video", parent=None):
        super().__init__(parent)
        self.runner = cutter.CutRunner(ff, src, info, segments, out_dir, mode, crf, preset,
                                       output=output,
                                       on_progress=self.progress.emit, on_log=self.message.emit)

    def cancel(self):
        self.runner.cancel()

    def run(self):
        try:
            self.succeeded.emit(self.runner.run())
        except cutter.CancelledError:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001
            log.exception("컷 작업 오류")
            msg = "구간 저장 중 오류가 발생했습니다."
            if isinstance(e, (NoAudioError, MissingEncoderError)):
                msg = str(e)
            elif isinstance(e, PermissionError):
                msg = "저장 폴더에 쓸 권한이 없습니다. 다른 폴더를 선택하세요."
            elif isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                msg = "디스크 공간이 부족합니다."
            self.failed.emit(msg, str(e))
