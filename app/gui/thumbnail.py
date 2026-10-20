"""썸네일 표시 위젯: 어떤 크기의 이미지든 영역 안에 비율을 유지해 딱 맞게 그린다.

QLabel.setPixmap 은 이미지 크기가 레이아웃 크기 계산에 영향을 주고,
Windows 화면 배율(125%·150% 등)에서 영역을 벗어나 다른 위젯을 가릴 수 있어
직접 그리는 방식으로 구현했다. 영역은 16:9 고정 크기이고, 이미지는 비율을 유지해 영역 안에 맞춘다.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget


class ThumbnailView(QWidget):
    WIDTH, HEIGHT = 288, 162

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._text = "썸네일"
        # 16:9 고정 크기: 유튜브 썸네일 비율과 같아 여백 없이 꽉 차고,
        # 레이아웃 크기가 절대 바뀌지 않아 다른 위젯을 가리지 않는다.
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    # ---------------------------------------------------------------- API
    def setPixmap(self, pm: QPixmap | None) -> None:  # noqa: N802 (Qt 스타일)
        self._pixmap = pm if pm is not None and not pm.isNull() else None
        self.update()

    def clear(self, text: str = "썸네일") -> None:
        self._pixmap = None
        self._text = text
        self.update()

    def hasImage(self) -> bool:  # noqa: N802
        return self._pixmap is not None

    # ---------------------------------------------------------------- 그리기
    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        area = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(area, 4, 4)
        p.setClipPath(clip)              # 위젯 영역 밖으로는 절대 그리지 않음
        p.fillRect(area, QColor("#222"))
        if self._pixmap is None:
            p.setPen(QColor("#888"))
            p.drawText(area, Qt.AlignCenter, self._text)
            return
        # 비율을 유지하면서 영역 안에 들어가는 가장 큰 크기 (남는 곳은 검은 여백)
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(area.width() / pw, area.height() / ph)
        w, h = pw * scale, ph * scale
        target = QRectF(area.x() + (area.width() - w) / 2,
                        area.y() + (area.height() - h) / 2, w, h)
        p.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
