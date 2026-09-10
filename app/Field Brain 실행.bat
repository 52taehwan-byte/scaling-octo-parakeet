@echo off
chcp 65001 >nul
setlocal

set "FB_APP_DIR=%~dp0"
set "FB_VENV=%LOCALAPPDATA%\FieldBrain\venv"
set "FB_PYTHON=%FB_VENV%\Scripts\python.exe"

if not exist "%FB_PYTHON%" (
  echo Field Brain을 처음 실행하기 위한 준비를 시작합니다.
  echo 실제 업무 데이터와 실행 환경은 OneDrive 밖에 저장합니다.
  py -3 -m venv "%FB_VENV%"
  if errorlevel 1 goto :setup_error
  "%FB_PYTHON%" -m pip install --upgrade pip
  if errorlevel 1 goto :setup_error
  "%FB_PYTHON%" -m pip install -r "%FB_APP_DIR%requirements.txt"
  if errorlevel 1 goto :setup_error
)

"%FB_PYTHON%" "%FB_APP_DIR%run.py"
if errorlevel 1 goto :run_error
goto :end

:setup_error
echo.
echo 설치 중 문제가 생겼습니다. 인터넷 연결을 확인한 뒤 다시 실행해 주세요.
pause
exit /b 1

:run_error
echo.
echo Field Brain 실행 중 문제가 생겼습니다. 이 창의 내용을 개발실에 알려 주세요.
pause
exit /b 1

:end
endlocal

