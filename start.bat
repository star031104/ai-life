@echo off
rem Start one of them in the background with a tray icon (bottom-right of the taskbar), no console window.
rem   start.bat yuki     private QQ companion
rem   start.bat qq       QQ group
rem   start.bat wechat   WeChat group
rem   start.bat          asks which one (or starts every one that is set up: start.bat all)
rem Right-click the icon: status, open her home page, log, restart, autostart, quit. Debugging: scripts\start_console.bat <name>.
cd /d "%~dp0"
set "T=%~1"
if /i "%T%"=="hidden" set "T=%~2"
if /i "%T%"=="all" (
  for %%N in (yuki qq wechat) do if exist "home\%%N\config.yaml" call "%~f0" %%N
  if exist "config.yaml" if not exist "home\yuki\config.yaml" call "%~f0" yuki
  exit /b 0
)
if "%T%"=="" (
  echo Which one?  1 = yuki ^(private QQ^)   2 = qq ^(QQ group^)   3 = wechat ^(WeChat group^)   4 = all that are set up
  set /p N=Type 1-4 [1]: 
)
if "%T%"=="" if "%N%"=="2" set "T=qq"
if "%T%"=="" if "%N%"=="3" set "T=wechat"
if "%T%"=="" if "%N%"=="4" (call "%~f0" all & exit /b 0)
if "%T%"=="" set "T=yuki"

call "%~dp0scripts\env.bat" %T% || exit /b 1

python -c "import pystray, PIL" >NUL 2>NUL
if errorlevel 1 (
  echo First run: installing the tray icon packages ^(pystray, Pillow^) ...
  python -m pip install pystray Pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
  if errorlevel 1 python -m pip install pystray Pillow
)
python -c "import pystray, PIL" >NUL 2>NUL
if errorlevel 1 goto console
where pythonw >NUL 2>NUL
if errorlevel 1 goto console
start "" pythonw "%~dp0scripts\tray.pyw" %T%
exit /b 0

:console
echo Tray icon is not available, running in this window instead.
call "%~dp0scripts\start_console.bat" %T%
