@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name LocalNetworkMonitor --add-data "main_window.ui;." --add-data "assets;assets" main.py
if errorlevel 1 goto failed
echo.
echo Built: dist\LocalNetworkMonitor.exe
pause
exit /b 0
:failed
echo.
echo Build failed. Check the error above.
pause
exit /b 1
