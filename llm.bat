@echo off
rem Add your own LLM API (any OpenAI-compatible URL + key): test connection, pick a model, add it to the routes
cd /d "%~dp0"

call "%~dp0tools\env.bat" || exit /b 1

python run.py llm
echo.
pause
