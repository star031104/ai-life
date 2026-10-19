@echo off
cd /d "%~dp0"

call "%~dp0scripts\env.bat" || exit /b 1

python run.py setup
echo.
pause
