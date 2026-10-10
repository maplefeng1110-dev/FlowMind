@echo off
REM Build the FlowMind Agent standalone executable on Windows (requires Python 3.9+).
REM Cannot cross-compile — run this ON Windows to produce the Windows build.
setlocal
cd /d "%~dp0\.."

if exist agent\.env del /q agent\.env

python -m venv .build-venv
call .build-venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm packaging\flowmind-agent.spec
if errorlevel 1 (echo Build failed & exit /b 1)

set OUT=dist\flowmind-agent-windows
if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%"
copy /y dist\flowmind-agent.exe "%OUT%\flowmind-agent.exe"
xcopy /e /i /y browser_bridge\extension "%OUT%\extension"
copy /y packaging\USAGE.txt "%OUT%\USAGE.txt"
echo Done: %OUT%\
endlocal
