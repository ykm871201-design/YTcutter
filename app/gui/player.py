"""영상 미리보기 플레이어 (QMediaPlayer + 탐색 슬라이더 + 구간 미리보기)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
                               QStyle, QVBoxLayout, QWidget)

from ..utils import format_time


class PlayerWidget(QWidget):
    positionChanged = Signal(float)   # 초
    errorOccurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget(self)
        # 영상 원본 크기(예: 1920x1080, 세로 쇼츠)가 레이아웃을 밀어내지 않도록
        # 크기 힌트를 무시하고, 주어진 공간 안에서 비율을 유지해 표시한다.
        self.video.setMinimumSize(240, 135)
        self.video.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.video.setAspectRatioMode(Qt.KeepAspectRatio)
        self.video.setStyleSheet("background:#000;")
        self.player.setVideoOutput(self.video)

        self.placeholder = QLabel("다운로드한 영상 또는 [로컬 영상 열기]로 불러온 영상이 여기에 표시됩니다.")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color:#888; padding:8px;")

        st = self.style()
        self.btn_play = QPushButton(st.standardIcon(QStyle.SP_MediaPlay), "")
        self.btn_play.setToolTip("재생/일시정지 (Space)")
        self.btn_back = QPushButton("-5초")
        self.btn_fwd = QPushButton("+5초")
        self.btn_prev_f = QPushButton("-0.1")
        self.btn_next_f = QPushButton("+0.1")
        for b, tip in ((self.btn_back, "5초 뒤로 (←)"), (self.btn_fwd, "5초 앞으로 (→)"),
                       (self.btn_prev_f, "0.1초 뒤로 (,)"), (self.btn_next_f, "0.1초 앞으로 (.)")):
            b.setToolTip(tip)
            b.setMinimumWidth(44)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.lbl_time = QLabel("00:00:00.000 / 00:00:00.000")
        self.lbl_time.setStyleSheet("font-family: Consolas, monospace;")
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(70)
        self.vol.setToolTip("볼륨")

        # 1줄: 탐색 슬라이더 + 시간   2줄: 재생 버튼 · 볼륨 → 좁은 화면에서도 잘리지 않음
        seek = QHBoxLayout()
        seek.addWidget(self.slider, 1)
        seek.addWidget(self.lbl_time)
        ctrl = QHBoxLayout()
        for w in (self.btn_play, self.btn_prev_f, self.btn_next_f, self.btn_back, self.btn_fwd):
            ctrl.addWidget(w)
        ctrl.addStretch(1)
        ctrl.addWidget(QLabel("🔊"))
        ctrl.addWidget(self.vol)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video, 1)
        lay.addWidget(self.placeholder)
        lay.addLayout(seek)
        lay.addLayout(ctrl)

        self._dragging = False
        self._stop_at: float | None = None
        self._duration = 0.0

        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_back.clicked.connect(lambda: self.step(-5))
        self.btn_fwd.clicked.connect(lambda: self.step(5))
        self.btn_prev_f.clicked.connect(lambda: self.step(-0.1))
        self.btn_next_f.clicked.connect(lambda: self.step(0.1))
        self.vol.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.slider.sliderPressed.connect(self._on_press)
        self.slider.sliderReleased.connect(self._on_release)
        self.slider.sliderMoved.connect(lambda v: self.player.setPosition(v))
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(
            lambda err, msg: self.errorOccurred.emit(msg or "재생할 수 없는 영상입니다."))
        self.set_enabled(False)

    # ---------------------------------------------------------------- API
    def load(self, path: str) -> None:
        self._stop_at = None
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.placeholder.hide()
        self.set_enabled(True)
        self.player.pause()   # 첫 프레임 표시

    def unload(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())

    def set_enabled(self, on: bool) -> None:
        for w in (self.btn_play, self.btn_back, self.btn_fwd, self.btn_prev_f,
                  self.btn_next_f, self.slider):
            w.setEnabled(on)

    def set_duration_hint(self, seconds: float) -> None:
        if seconds > 0 and self._duration <= 0:
            self._on_duration(int(seconds * 1000))

    def position(self) -> float:
        return self.player.position() / 1000.0

    def seek(self, seconds: float) -> None:
        self.player.setPosition(int(max(0.0, seconds) * 1000))

    def step(self, delta: float) -> None:
        dur_ms = self.player.duration() or int(self._duration * 1000)
        pos = max(0, min(self.player.position() + int(delta * 1000), dur_ms))
        self.player.setPosition(pos)

    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self._stop_at = None
            self.player.play()

    def play_range(self, start: float, end: float) -> None:
        """구간 미리보기: start 부터 재생하고 end 에서 멈춘다."""
        self._stop_at = end
        self.seek(start)
        self.player.play()

    # ---------------------------------------------------------------- 내부
    def _on_press(self):
        self._dragging = True

    def _on_release(self):
        self._dragging = False
        self.player.setPosition(self.slider.value())

    def _on_position(self, ms: int):
        if not self._dragging:
            self.slider.setValue(ms)
        sec = ms / 1000.0
        self.lbl_time.setText(f"{format_time(sec)} / {format_time(self._duration)}")
        if self._stop_at is not None and sec >= self._stop_at:
            self._stop_at = None
            self.player.pause()
        self.positionChanged.emit(sec)

    def _on_duration(self, ms: int):
        self._duration = ms / 1000.0
        self.slider.setRange(0, ms)
        self.lbl_time.setText(f"{format_time(self.position())} / {format_time(self._duration)}")

    def _on_state(self, state):
        icon = QStyle.SP_MediaPause if state == QMediaPlayer.PlayingState else QStyle.SP_MediaPlay
        self.btn_play.setIcon(self.style().standardIcon(icon))
