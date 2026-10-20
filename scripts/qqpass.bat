@echo off
rem Save the group bot's QQ password (MD5 only, in .env) so NapCat can log in without a QR code after reboot
cd /d "%~dp0.."
set "T=%~1"
if "%T%"=="" set "T=qq"

call "%~dp0env.bat" %T% || exit /b 1

python run.py %T% qqpass
echo.
if /i not "%~2"=="nopause" pause
