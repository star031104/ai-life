@echo off
rem Check for a new version of ai-life, update (with backup) or roll back: update.bat [apply^|rollback]
cd /d "%~dp0.."
call "%~dp0env.bat" || exit /b 1
if not "%1"=="" (
  python run.py update %*
  pause
  exit /b
)
python run.py update
echo.
set /p yn=Update now? [y/N] 
if /i "%yn%"=="y" python run.py update apply
pause
