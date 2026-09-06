@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Once kur.bat calistir. & pause & exit /b 1)
echo En likit 15 pariteden 6000 bar ^(4 saatlik^) indiriliyor...
echo.
".venv\Scripts\python.exe" run.py fetch --top 15 --bars 6000
echo.
pause
