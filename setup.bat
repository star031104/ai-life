@echo off
cd /d "%~dp0"

call "%~dp0tools\env.bat" || exit /b 1

python run.py setup
echo.
pause
