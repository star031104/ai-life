@echo off
rem yuki's QQ one-click setup: downloads + configures NapCat if needed, scan once, then writes the qq section of home\yuki\config.yaml
cd /d "%~dp0.."
call "%~dp0env.bat" yuki || exit /b 1
set AILIFE_LAUNCHER=1
python run.py yuki qqsetup
echo.
pause
