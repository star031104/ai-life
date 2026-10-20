@echo off
rem WeChat connection check: version, data folder, keys, can it read the groups, windows
cd /d "%~dp0.."
set "T=%~1"
if "%T%"=="" set "T=wechat"

call "%~dp0env.bat" %T% || exit /b 1

python run.py %T% wxdiag
echo.
if /i not "%~2"=="nopause" pause
