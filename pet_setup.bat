@echo off
cd /d "%~dp0"
echo Installing PySide6 for the transparent desktop pet (no white box, no taskbar icon).
echo About 200 MB. Please wait ...
echo.
set "ACT="
for %%D in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "C:\ProgramData\anaconda3" "C:\ProgramData\miniconda3" "D:\anaconda3" "D:\Anaconda3" "D:\miniconda3") do (
  if not defined ACT if exist "%%~D\Scripts\activate.bat" set "ACT=%%~D\Scripts\activate.bat"
)
if defined ACT (
  call "%ACT%" ailife
) else (
  call conda activate ailife
)
pip install PySide6 -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo [ERROR] Install failed. Check your network, then run this file again.
  pause
  exit /b 1
)
echo.
echo Done. Close the start.bat window and start it again; the pet will be transparent.
pause
