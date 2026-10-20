@echo off
rem Before she joins: export the groups' old chat history, let the LLM write a "who is who" draft, review it, load it: onboard.bat qq / wechat
cd /d "%~dp0.."
set "T=%~1"
if "%T%"=="" set "T=wechat"
call "%~dp0env.bat" %T% || exit /b 1
python run.py %T% onboard
echo.
pause
