@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "LOG=%~dp0build_log.txt"

rem ※ 이 파일은 괄호 블록 if (...) 를 쓰지 않습니다.
rem    블록 안의 echo 문구에 ) 가 있으면 cmd 가 블록 끝으로 잘못 읽어 스크립트가 멈추기 때문입니다.

echo ============================================
echo   YT Cutter 실행 파일 exe 만들기
echo ============================================
echo.
echo 진행 기록은 build_log.txt 에 저장됩니다.
echo.
echo YT Cutter 빌드 기록 %DATE% %TIME%>"%LOG%"

call :step "[1/6] 실행 중인 YT Cutter 종료"
taskkill /IM YTCutter.exe /F >>"%LOG%" 2>&1

call :step "[2/6] 가상환경 준비"
if not exist .venv\Scripts\python.exe py -3 -m venv .venv >>"%LOG%" 2>&1
if not exist .venv\Scripts\python.exe python -m venv .venv >>"%LOG%" 2>&1
if not exist .venv\Scripts\python.exe goto err_venv
.venv\Scripts\python -V >>"%LOG%" 2>&1

call :step "[3/6] 필요한 패키지 확인 - 처음에는 몇 분 걸립니다"
.venv\Scripts\python -m pip install --upgrade pip >>"%LOG%" 2>&1
.venv\Scripts\python -m pip install -r requirements-dev.txt >>"%LOG%" 2>&1
if errorlevel 1 goto err_pip
.venv\Scripts\python -m pip list >>"%LOG%" 2>&1

call :step "[4/6] 이전 빌드 폴더 정리"
if exist dist\YTCutter rmdir /s /q dist\YTCutter >>"%LOG%" 2>&1
if exist dist\YTCutter goto err_dist
if exist build\YTCutter rmdir /s /q build\YTCutter >>"%LOG%" 2>&1

call :step "[5/6] 빌드 - 몇 분 걸립니다. 창을 닫지 마세요"
.venv\Scripts\python -m PyInstaller --noconfirm --clean --windowed --name YTCutter --collect-all yt_dlp_ejs main.py >>"%LOG%" 2>&1
if errorlevel 1 goto err_build
if not exist dist\YTCutter\YTCutter.exe goto err_build

call :step "[6/6] FFmpeg, Deno 와 문서 함께 넣기"
if exist ffmpeg xcopy ffmpeg dist\YTCutter\ffmpeg\ /E /I /Y >>"%LOG%" 2>&1
if not exist ffmpeg echo       [안내] ffmpeg 폴더가 없어 복사하지 않았습니다. 받는 사람이 FFmpeg 를 따로 설치해야 합니다.
if exist deno.exe .venv\Scripts\python -c "from app.downloader import exe_machine as m; import sys; sys.exit(m('deno.exe') != m(r'dist\YTCutter\YTCutter.exe'))" >>"%LOG%" 2>&1 || echo       [경고] deno.exe 가 이 PC 와 다른 CPU 용입니다. 예: ARM64. x64 PC 용은 deno-x86_64-pc-windows-msvc.zip 입니다.
if exist deno.exe copy /Y deno.exe dist\YTCutter\ >>"%LOG%" 2>&1
if exist deno.exe copy /Y deno-LICENSE.md dist\YTCutter\ >>"%LOG%" 2>&1
if exist README.md copy /Y README.md dist\YTCutter\ >>"%LOG%" 2>&1
if exist THIRD_PARTY_NOTICES.md copy /Y THIRD_PARTY_NOTICES.md dist\YTCutter\ >>"%LOG%" 2>&1

echo.>>"%LOG%"
echo ===== 빌드 성공 =====>>"%LOG%"

rem build 폴더는 중간 작업 파일입니다. 그 안의 exe 는 단독 실행이 안 되어 헷갈리므로 지웁니다.
if exist build rmdir /s /q build >>"%LOG%" 2>&1

echo.
echo ============================================
echo   완료!  dist\YTCutter\YTCutter.exe
echo ============================================
echo.
echo 실행할 파일: dist\YTCutter 폴더 안의 YTCutter.exe
echo 배포할 때는 dist\YTCutter 폴더 전체를 압축해서 전달하세요.
echo.
echo 완성된 폴더를 탐색기로 엽니다...
start "" explorer "%~dp0dist\YTCutter"
echo.
pause
exit /b 0

rem ------------------------------------------------ 단계 표시 (화면 + 로그)
:step
echo %~1
echo.>>"%LOG%"
echo ===== %~1 =====>>"%LOG%"
exit /b 0

rem ------------------------------------------------ 오류 안내
:err_venv
echo [오류] 가상환경 생성 실패>>"%LOG%"
echo.
echo [오류] 파이썬 가상환경을 만들지 못했습니다.
echo        Python 을 설치할 때 "Add python.exe to PATH" 를 체크했는지 확인하세요.
goto fail

:err_pip
echo [오류] 패키지 설치 실패>>"%LOG%"
echo.
echo [오류] 패키지 설치에 실패했습니다. 인터넷 연결을 확인하세요.
goto fail

:err_dist
echo [오류] dist\YTCutter 폴더 삭제 실패>>"%LOG%"
echo.
echo [오류] 이전에 만든 dist\YTCutter 폴더를 지울 수 없습니다.
echo        아래를 확인한 뒤 다시 실행하세요.
echo          1^) YT Cutter 프로그램이 실행 중이면 종료
echo          2^) dist\YTCutter 폴더를 연 탐색기 창이 있으면 닫기
echo          3^) 백신이 검사 중일 수 있으니 잠시 후 다시 시도
goto fail

:err_build
echo [오류] PyInstaller 빌드 실패>>"%LOG%"
echo.
echo [오류] 빌드에 실패했습니다.
goto fail

:fail
echo.
echo --- build_log.txt 마지막 부분 ---
powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 25 -Encoding UTF8"
echo ---------------------------------
echo.
echo 전체 기록은 build_log.txt 파일에 있습니다.
echo.
pause
exit /b 1
