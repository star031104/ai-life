@echo off
rem Self test for yuki (private QQ companion)
cd /d "%~dp0.."
call "%~dp0env.bat" yuki || exit /b 1
python run.py yuki selftest
echo.
pause
