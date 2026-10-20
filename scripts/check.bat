@echo off
rem Check the group bot's config, QQ / WeChat connection and the LLM: check.bat qq / wechat
cd /d "%~dp0.."
set "T=%~1"
if "%T%"=="" set "T=qq"

call "%~dp0env.bat" %T% || exit /b 1

python run.py %T% check
echo.
if /i not "%~2"=="nopause" pause
