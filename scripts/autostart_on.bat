@echo off
REM Turn on autostart: put a small launcher into your Windows Startup folder (no admin needed).
set "SU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
(
  echo @echo off
  echo cd /d "%~dp0.."
  echo start "ai-life" /min "%~dp0..\start.bat"
) > "%SU%\ai-life.cmd"
echo Autostart is ON. She will start when you log in to Windows.
echo (To turn it off, double-click autostart_off.bat)
pause
