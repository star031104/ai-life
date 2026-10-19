@echo off
cd /d "%~dp0"
echo Installing PySide6 for the transparent desktop pet (no white box, no taskbar icon).
echo About 200 MB. Please wait ...
echo.
call "%~dp0tools\env.bat" || exit /b 1
pip install PySide6 -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo [ERROR] Install failed. Check your network, then run this file again.
  pause
  exit /b 1
)
echo.
echo Done. Close the start.bat window and start it again; the pet will be transparent.
pause
