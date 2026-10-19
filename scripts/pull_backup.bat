@echo off
rem Copy the latest server backup (3 databases + configs + local personas) to this PC: backups\ailife-YYYY-MM-DD.tar.gz
rem Keeps the newest 30. Uses the same server / key as open_home.bat (data\server.txt, data\server_key).
rem   pull_backup.bat           copy once now
rem   pull_backup.bat install   also copy every day at 12:30 (Windows Task Scheduler, runs when you are logged in)
setlocal
cd /d "%~dp0.."
if /i "%~1"=="install" (
  schtasks /Create /SC DAILY /ST 12:30 /TN "ai-life backup" /TR "\"%~f0\" quiet" /F >NUL && echo Every day at 12:30 the latest backup will be copied to %CD%\backups
)
if not exist data\server.txt (echo [ERROR] Run open_home.bat once first so it knows the server. & if /i not "%~1"=="quiet" pause & exit /b 1)
set /p SRV=<data\server.txt
set "KEY="
if exist data\server_key (
  icacls "%CD%\data\server_key" /inheritance:r /grant:r "%USERNAME%":R >NUL 2>NUL
  set KEY=-i "%CD%\data\server_key"
)
if not exist backups mkdir backups
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%d
scp %KEY% -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -q %SRV%:ailife/backups/latest.tar.gz "backups\ailife-%D%.tar.gz"
if errorlevel 1 (echo [ERROR] Could not copy the backup from %SRV%. & if /i not "%~1"=="quiet" pause & exit /b 1)
powershell -NoProfile -Command "Get-ChildItem backups\ailife-*.tar.gz | Sort-Object Name -Descending | Select-Object -Skip 30 | Remove-Item"
echo Saved backups\ailife-%D%.tar.gz
if /i not "%~1"=="quiet" pause
endlocal
