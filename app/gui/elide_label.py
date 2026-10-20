"""너비가 부족하면 긴 글자(파일 경로 등)를 가운데를 '…'로 줄여 한 줄로 보여주는 라벨."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy


class ElideLabel(QLabel):
    def __init__(self, text: str = "", parent=None, mode=Qt.ElideMiddle):
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(50)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text or ""
        self.setToolTip(self._full)
        self._refresh()

    def fullText(self) -> str:  # noqa: N802
        return self._full

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._refresh()

    def _refresh(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, self._mode, max(10, self.width() - 4))
        super().setText(shown)
