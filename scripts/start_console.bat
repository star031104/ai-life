@echo off
rem Run in this console window (for debugging): start_console.bat yuki / qq / wechat. Normally use start.bat: tray icon, no window.
cd /d "%~dp0.."
set "T=%~1"
if "%T%"=="" set "T=yuki"
title ai-life %T%

call "%~dp0env.bat" %T% || exit /b 1
set AILIFE_LAUNCHER=1
set AIGROUP_LAUNCHER=1

:loop
python run.py %T%
if errorlevel 3 if not errorlevel 4 goto stop
echo.
echo Program exited. Restarting in 10 seconds (close this window to stop) ...
timeout /t 10 >NUL
goto loop

:stop
echo.
echo Not restarting. See the message above.
pause
