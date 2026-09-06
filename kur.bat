@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo ==========================================================
echo   SWING BOT - KURULUM
echo ==========================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")

%PY% --version >nul 2>nul
if errorlevel 1 (
  echo [HATA] Python bulunamadi.
  echo.
  echo   1^) https://www.python.org/downloads/ adresinden Python 3.10+ kur
  echo   2^) Kurulum ekraninda "Add Python to PATH" kutusunu ISARETLE
  echo   3^) Bu dosyayi tekrar calistir
  echo.
  pause
  exit /b 1
)

echo [1/4] Python bulundu:
%PY% --version

if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Sanal ortam olusturuluyor...
  %PY% -m venv .venv
  if errorlevel 1 goto :hata
) else (
  echo [2/4] Sanal ortam zaten var.
)

echo [3/4] Kutuphaneler kuruluyor ^(birkac dakika surebilir^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 goto :hata

if not exist "config.yaml" (
  copy config.example.yaml config.yaml >nul
  echo [4/4] config.yaml olusturuldu - ayarlarini buradan yaparsin.
) else (
  echo [4/4] config.yaml zaten var, dokunulmadi.
)

echo.
echo Kod testi calistiriliyor...
echo.
".venv\Scripts\python.exe" run.py selftest --bars 1500 --symbols-count 2

echo.
echo ==========================================================
echo   Simdi birkac ayar sorusu soracak.
echo   Bilmedigin yerde Enter'a bas, varsayilan kalir.
echo ==========================================================
echo.
".venv\Scripts\python.exe" run.py ayarla

echo.
echo ==========================================================
echo   Kurulum tamam.
echo   SIRADAKI ADIM:  ayarlari-kontrol-et.bat
echo   Ayarlari sonra degistirmek istersen:  ayarla.bat
echo ==========================================================
pause
exit /b 0

:hata
echo.
echo [HATA] Kurulum tamamlanamadi. Yukaridaki mesaji bana gonder.
pause
exit /b 1
