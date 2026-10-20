"""GUI 스모크 테스트 (화면 없이 offscreen 으로 실행).

로컬 영상 열기 → 구간 지정/검증 → 구간 추가 → 정확한 컷/빠른 컷 저장 흐름을 확인한다.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from app.ffmpeg_tools import find_ffmpeg  # noqa: E402

FF = find_ffmpeg()


def _multimedia_works() -> bool:
    """QtMultimedia(재생 기능)를 쓸 수 있는지 별도 프로세스에서 확인한다.

    오디오 장치가 없는 서버·CI 환경에서는 QMediaPlayer 생성만으로 죽을 수 있어
    본 테스트까지 함께 중단되는 것을 막는다. (Windows 실사용 환경과는 무관)
    """
    code = ("from PySide6.QtWidgets import QApplication;"
            "from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput;"
            "a=QApplication([]);p=QMediaPlayer();o=QAudioOutput();p.setAudioOutput(o)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, timeout=120)
    return r.returncode == 0


MULTIMEDIA = _multimedia_works()
pytestmark = pytest.mark.skipif(FF is None, reason="ffmpeg 없음")


class _StubPlayer(QWidget):
    """재생 기능을 쓸 수 없는 환경에서 PlayerWidget 대신 쓰는 최소 구현.

    창의 흐름(버튼 활성화, 구간 지정, 저장)을 재생 기능과 분리해 검증하기 위한 것으로,
    실제 실행 시에는 사용되지 않는다.
    """
    errorOccurred = Signal(str)
    positionChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.video = QWidget(self)
        self._pos = 0.0
        self.loaded: str | None = None

    def load(self, path):
        self.loaded = path

    def unload(self):
        self.loaded = None

    def set_enabled(self, on):
        pass

    def set_duration_hint(self, seconds):
        pass

    def position(self):
        return self._pos

    def seek(self, seconds):
        self._pos = max(0.0, seconds)

    def step(self, delta):
        self.seek(self._pos + delta)

    def toggle_play(self):
        pass

    def play_range(self, start, end):
        self.seek(start)


def wait(cond, timeout=60):
    app = QApplication.instance()
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture(scope="module")
def _window(tmp_path_factory):
    """창은 모듈당 하나만 만든다 (QMediaPlayer 를 여러 개 만들면 불안정한 환경이 있음)."""
    tmp = tmp_path_factory.mktemp("gui")
    os.environ["APPDATA"] = str(tmp / "appdata")
    app = QApplication.instance() or QApplication([])
    import app.gui.main_window as mw
    errors: list[str] = []
    orig_error, orig_exec = mw.show_error, QMessageBox.exec
    orig_player = mw.PlayerWidget
    mw.show_error = lambda parent, m, d="": errors.append(m)
    QMessageBox.exec = lambda self: 0
    if not MULTIMEDIA:
        mw.PlayerWidget = _StubPlayer   # 재생 불가 환경에서도 창 흐름은 검증
    w = mw.MainWindow()
    w.errors = errors
    w.show()
    yield w
    w.close()
    mw.show_error, QMessageBox.exec = orig_error, orig_exec
    mw.PlayerWidget = orig_player


@pytest.fixture
def win(_window, tmp_path):
    """테스트마다 상태를 초기화해서 같은 창을 다시 쓴다."""
    w = _window
    w.errors.clear()
    w.table.setRowCount(0)
    w.last_outputs = []
    w.cmb_output.setCurrentIndex(w.cmb_output.findData("video"))
    w.rb_accurate.setChecked(True)
    w.ed_outdir.setText(str(tmp_path / "저장 폴더"))
    return w


def test_full_local_flow(win, tmp_path):
    src = tmp_path / "한글 경로" / "내 영상 파일.mp4"
    src.parent.mkdir()
    subprocess.run([FF.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=25:duration=8", "-f", "lavfi", "-i",
                    "sine=duration=8", "-c:v", "libx264", "-c:a", "aac", "-shortest",
                    str(src)], check=True)
    win.load_file(str(src))
    assert wait(lambda: win.current_file == str(src) and win.media is not None)
    assert win.ed_end.text() == "00:00:08.000"
    assert win.btn_save_current.isEnabled()
    assert not win.btn_save_all.isEnabled()          # 구간 목록 비어 있음

    # 잘못된 입력 검증
    win.ed_start.setText("00:00:05.000"); win.ed_end.setText("00:00:03.000")
    win.on_add_segment()
    assert win.table.rowCount() == 0 and "커야" in win.errors[-1]
    win.ed_end.setText("00:00:09.000")
    win.on_add_segment()
    assert "넘습니다" in win.errors[-1]
    win.ed_end.setText("abc")
    win.on_add_segment()
    assert "형식" in win.errors[-1]
    assert "잘못된" in win.lbl_len.text()

    # 구간 2개 추가
    win.ed_start.setText("1"); win.ed_end.setText("00:03.5"); win.on_add_segment()
    win.ed_start.setText("00:00:04.000"); win.ed_end.setText("00:00:06.000")
    win.ed_seg_name.setText("하이라이트"); win.on_add_segment()
    assert win.table.rowCount() == 2
    assert win.table.item(0, 3).text() == "00:00:02.500"

    # 정확한 컷 - 목록 전체 저장
    win.rb_accurate.setChecked(True)
    win.on_export(all_segments=True)
    assert not win.btn_save_all.isEnabled()          # 작업 중 비활성화
    assert win.btn_cut_cancel.isEnabled()
    assert wait(lambda: not win.cut_worker.isRunning())
    wait(lambda: len(win.last_outputs) == 2, 5)
    assert len(win.last_outputs) == 2 and all(Path(f).exists() for f in win.last_outputs)
    assert any("하이라이트" in f for f in win.last_outputs)

    # 빠른 컷 - 선택 구간 저장
    win.rb_fast.setChecked(True)
    assert "키프레임" in win.lbl_mode_note.text()
    win.on_export(all_segments=False)
    assert wait(lambda: not win.cut_worker.isRunning())
    wait(lambda: len(win.last_outputs) == 1, 5)
    assert len(win.last_outputs) == 1 and Path(win.last_outputs[0]).exists()
    assert src.exists()                               # 원본 보존

    # 음원 추출 - 선택 구간
    win.cmb_output.setCurrentIndex(win.cmb_output.findData("mp3"))
    assert not win.rb_accurate.isEnabled()        # 음원에는 컷 모드가 적용되지 않음
    assert "음원" in win.btn_save_current.text()
    win.ed_start.setText("00:00:01.000"); win.ed_end.setText("00:00:03.000")
    win.ed_seg_name.setText("배경음악")
    win.on_export(all_segments=False)
    assert wait(lambda: not win.cut_worker.isRunning())
    wait(lambda: len(win.last_outputs) == 1, 5)
    assert win.last_outputs[0].endswith(".mp3") and "배경음악" in win.last_outputs[0]

    # 영상 전체 음원 추출 (구간과 무관)
    win.cmb_output.setCurrentIndex(win.cmb_output.findData("video"))
    assert win.btn_extract_all.isEnabled()
    win.on_extract_all()
    assert win.cmb_output.currentData() == "mp3"   # 영상 형식이면 MP3 로 전환
    assert wait(lambda: not win.cut_worker.isRunning())
    wait(lambda: len(win.last_outputs) == 1, 5)
    whole = Path(win.last_outputs[0])
    assert whole.suffix == ".mp3" and whole.stem == src.stem
    assert src.exists()

    win.grab().save(str(tmp_path / "screenshot.png"))


def test_video_without_audio(win, tmp_path):
    """소리 없는 영상에서는 음원 추출 버튼이 꺼지고 안내가 보인다."""
    src = tmp_path / "무음.mp4"
    subprocess.run([FF.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=160x120:rate=25:duration=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    win.load_file(str(src))
    assert wait(lambda: win.current_file == str(src) and win.media is not None)
    assert not win.btn_extract_all.isEnabled()
    win.cmb_output.setCurrentIndex(win.cmb_output.findData("mp3"))
    assert "소리가 없어" in win.lbl_output_note.text()
    win.on_export(all_segments=False)
    assert "소리가 없어" in win.errors[-1]


def test_bad_url(win):
    win.ed_url.setText("https://vimeo.com/1234")
    win.on_fetch_info()
    assert "유튜브" in win.errors[-1]
    assert win.info_worker is None
