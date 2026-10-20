@echo off
REM Turn on autostart (no admin needed, no console window): autostart_on.bat [yuki|qq|wechat|all]  (default: all that are set up)
set "T=%~1"
if "%T%"=="" set "T=all"
if /i "%T%"=="nopause" set "T=all"
set "SU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:SU+'\ai-life-%T%.lnk'); $s.TargetPath='wscript.exe'; $s.Arguments='\"%~dp0..\start.vbs\" %T%'; $s.WorkingDirectory='%~dp0..'; $s.Save()"
if exist "%SU%\ai-life-%T%.lnk" (
  echo Autostart is ON ^(%T%^). It starts when you log in to Windows.
) else (
  echo Could not create the shortcut. Put a shortcut to start.vbs into: %SU%
)
echo (To turn it off: scripts\autostart_off.bat %T%)
if /i not "%~1"=="nopause" if /i not "%~2"=="nopause" pause
