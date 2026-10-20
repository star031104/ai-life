@echo off
set "T=%~1"
if "%T%"=="" set "T=all"
if /i "%T%"=="nopause" set "T=all"
set "SU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
del "%SU%\ai-life-%T%.lnk" 2>NUL
if /i "%T%"=="all" for %%F in (ai-life.cmd ai-life-group.cmd ai-life-wechat.cmd ai-life-wechat.lnk ai-life-yuki.lnk ai-life-qq.lnk ai-life-wechat.lnk) do del "%SU%\%%F" 2>NUL
echo Autostart is OFF ^(%T%^).
if /i not "%~1"=="nopause" if /i not "%~2"=="nopause" pause
