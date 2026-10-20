@echo off
rem Add your own LLM API (any OpenAI-compatible URL + key): llm.bat yuki / qq / wechat
cd /d "%~dp0.."
set "T=%~1"
if "%T%"=="" set "T=yuki"

call "%~dp0env.bat" %T% || exit /b 1

python run.py %T% llm
echo.
if /i not "%~2"=="nopause" pause
