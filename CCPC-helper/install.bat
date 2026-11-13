@echo off
cd /d "%~dp0"
py -3 --version >nul 2>&1
if errorlevel 1 goto missing
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Setup complete. Run start.bat to configure your API in the app.
pause
exit /b 0
:missing
echo Install Python 3.10+ from python.org with the Python launcher.
pause
exit /b 1
:failed
echo Setup failed. Check the error above and try again.
pause
exit /b 1
