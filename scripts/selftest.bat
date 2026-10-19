@echo off
cd /d "%~dp0.."

call "%~dp0env.bat" || exit /b 1

python run.py selftest
echo.
pause
