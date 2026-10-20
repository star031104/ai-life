@echo off
rem Show how she sees each person in the group (profiles): people.bat qq / wechat
cd /d "%~dp0.."
set "T=%~1"
if "%T%"=="" set "T=qq"

call "%~dp0env.bat" %T% || exit /b 1

python run.py %T% people
echo.
if /i not "%~2"=="nopause" pause
