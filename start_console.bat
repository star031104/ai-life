@echo off
rem Run in this console window (for debugging). Normally use start.bat: tray icon, no window.
cd /d "%~dp0"
title %~n0

call "%~dp0tools\env.bat" || exit /b 1
set AILIFE_LAUNCHER=1

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
