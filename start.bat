@echo off
rem Start in the background with a tray icon (bottom-right of the taskbar), no console window.
rem Right-click the icon: status, log, restart, autostart, quit. Debugging: use start_console.bat.
cd /d "%~dp0"

call "%~dp0tools\env.bat" || exit /b 1

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
start "" pythonw "%~dp0tools\tray.pyw"
exit /b 0

:console
echo Tray icon is not available, running in this window instead.
call "%~dp0start_console.bat"
