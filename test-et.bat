@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Once kur.bat calistir. & pause & exit /b 1)
if not exist "data" (
  echo Veri klasoru yok. Once veri-indir.bat calistir.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py backtest --csv data --trades 20
echo.
pause
