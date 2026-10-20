"""공통 대화상자: 오류(간단 안내 + 상세 로그), FFmpeg 안내."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from ..ffmpeg_tools import FFMPEG_HELP


def show_error(parent: QWidget, message: str, detail: str = "", title: str = "오류") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    if detail:
        box.setDetailedText(detail)  # [자세히 보기] 버튼으로 상세 로그 확인
    box.exec()


def ask_ffmpeg(parent: QWidget) -> bool:
    """FFmpeg 안내를 띄우고, 사용자가 직접 위치를 지정하려 하면 True."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle("FFmpeg 필요")
    box.setText(FFMPEG_HELP)
    pick = box.addButton("ffmpeg.exe 위치 지정...", QMessageBox.AcceptRole)
    box.addButton("닫기", QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is pick
