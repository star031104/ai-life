@echo off
rem Setup wizard: setup.bat yuki / setup.bat qq / setup.bat wechat (asks if you leave it out)
cd /d "%~dp0"
set "T=%~1"
if "%T%"=="" (
  echo Set up which one?  1 = yuki ^(private QQ^)   2 = qq ^(QQ group^)   3 = wechat ^(WeChat group^)
  set /p N=Type 1-3 [1]: 
)
if "%T%"=="" if "%N%"=="2" set "T=qq"
if "%T%"=="" if "%N%"=="3" set "T=wechat"
if "%T%"=="" set "T=yuki"
call "%~dp0scripts\env.bat" %T% || exit /b 1
python run.py %T% setup %2 %3
echo.
pause
