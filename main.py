"""YT Cutter 실행 진입점:  python main.py"""
import os
import sys

# 창 모드(--windowed)로 패키징하면 stdout/stderr 가 None 이 되어
# 일부 라이브러리가 출력 시 오류를 낼 수 있으므로 빈 출력으로 대체한다.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from PySide6.QtWidgets import QApplication

from app import APP_NAME
from app.gui.main_window import MainWindow
from app.logging_setup import setup_logging


def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
