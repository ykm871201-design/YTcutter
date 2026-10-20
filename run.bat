@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo [1/2] 가상환경 만드는 중...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
echo [2/2] 실행
start "" .venv\Scripts\pythonw.exe main.py
