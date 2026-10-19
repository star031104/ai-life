@echo off
rem Start a group-chat character. Usage: start_group.bat [config file], default config.group.yaml
cd /d "%~dp0"

call "%~dp0tools\env.bat" || exit /b 1
set AILIFE_LAUNCHER=1
set "AILIFE_CONFIG=%~1"
if "%AILIFE_CONFIG%"=="" set "AILIFE_CONFIG=config.group.yaml"
if not exist "%AILIFE_CONFIG%" (
  echo %AILIFE_CONFIG% not found. Copy config.group.example.yaml to config.group.yaml and fill it in.
  pause
  exit /b 1
)
title ai-life group: %AILIFE_CONFIG%

:loop
python run.py
if errorlevel 3 if not errorlevel 4 goto stop
echo.
echo Program exited. Restarting in 10 seconds (close this window to stop) ...
timeout /t 10 >NUL
goto loop

:stop
echo.
echo Not restarting. See the message above.
pause
