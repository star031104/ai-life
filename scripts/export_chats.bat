@echo off
rem Export chat history of a few small groups (read-only) to home\wechat\data\export, for writing the persona
cd /d "%~dp0.."
set "T=%~1"
if "%T%"=="" set "T=wechat"

call "%~dp0env.bat" %T% || exit /b 1

python run.py %T% wxexport
echo.
if /i not "%~2"=="nopause" pause
