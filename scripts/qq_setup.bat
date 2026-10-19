@echo off
rem QQ one-click setup: downloads + configures NapCat if needed, scan once, then writes config.yaml qq section
cd /d "%~dp0.."

call "%~dp0env.bat" || exit /b 1
set AILIFE_LAUNCHER=1

python run.py qqsetup
echo.
pause
