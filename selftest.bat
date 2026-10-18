@echo off
cd /d "%~dp0"

set "ACT="
for %%D in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "C:\ProgramData\anaconda3" "C:\ProgramData\miniconda3" "D:\anaconda3" "D:\Anaconda3" "D:\miniconda3") do (
  if not defined ACT if exist "%%~D\Scripts\activate.bat" set "ACT=%%~D\Scripts\activate.bat"
)
if defined ACT (
  call "%ACT%" ailife
) else (
  call conda activate ailife
)
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

python run.py selftest
echo.
pause
