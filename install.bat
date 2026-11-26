@echo off
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt
echo.
echo Done. Run start.bat
pause
