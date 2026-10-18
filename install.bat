@echo off
cd /d "%~dp0"
echo ==== ai-life installer (run once) ====

set "ROOTC="
for %%D in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "C:\ProgramData\anaconda3" "C:\ProgramData\miniconda3" "D:\anaconda3" "D:\Anaconda3" "D:\miniconda3") do (
  if not defined ROOTC if exist "%%~D\Scripts\activate.bat" set "ROOTC=%%~D"
)
if defined ROOTC call "%ROOTC%\Scripts\activate.bat"
call conda --version >NUL 2>NUL
if errorlevel 1 (
  echo [ERROR] conda not found. Open "Anaconda Prompt", cd to this folder, then run install.bat
  pause
  exit /b 1
)

echo [1/3] Creating Python env "ailife" ...
call conda env list | findstr /b /c:"ailife " >NUL
if errorlevel 1 call conda create -n ailife python=3.11 -y
call conda activate ailife

echo [2/3] Installing packages (Tsinghua mirror) ...
python -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo [ERROR] pip install failed. See README (Troubleshooting) or open an issue with a screenshot.
  pause
  exit /b 1
)

echo [2b] Optional packages (lunar calendar, now-playing music). Errors here are OK ...
pip install lunar_python -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install winrt-runtime winrt-Windows.Foundation winrt-Windows.Media.Control -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install pywebview -i https://pypi.tuna.tsinghua.edu.cn/simple
echo Installing PySide6 for the transparent desktop pet (about 200 MB, takes a few minutes) ...
pip install PySide6 -i https://pypi.tuna.tsinghua.edu.cn/simple

echo [3/3] Checking ffmpeg ...
where ffmpeg >NUL 2>NUL
if errorlevel 1 winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements

if not exist config.yaml (
  echo.
  echo [setup] First time: answer a few questions (Telegram bot token, API keys, persona) ...
  python run.py setup
)

echo.
echo ==== Done! Run selftest.bat once, then double-click start.bat ====
pause
