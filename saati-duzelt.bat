@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================================
echo   BILGISAYAR SAATINI BINANCE ILE ESITLE
echo ==========================================================
echo.
echo Binance, saati 1 saniyeden fazla ILERI olan istekleri reddeder
echo (hata kodu -1021). Bu dosya Windows saatini sunucuyla esitler.
echo.

net session >nul 2>nul
if errorlevel 1 (
  echo [DIKKAT] Bu dosya YONETICI olarak calistirilmali.
  echo   Sag tik  ^>  "Yonetici olarak calistir"
  echo.
  pause
  exit /b 1
)

echo [1/3] Saat servisi baslatiliyor...
net start w32time >nul 2>nul
sc config w32time start= auto >nul 2>nul

echo [2/3] Zaman sunucusu ayarlaniyor...
w32tm /config /manualpeerlist:"time.windows.com,0x9 pool.ntp.org,0x9" /syncfromflags:manual /update >nul 2>nul

echo [3/3] Esitleniyor...
w32tm /resync /force
if errorlevel 1 (
  echo.
  echo Otomatik esitleme basarisiz oldu. Elle yap:
  echo   Ayarlar ^> Saat ve Dil ^> Tarih ve saat
  echo   "Saati otomatik ayarla" ACIK olsun, sonra "Simdi esitle" butonuna bas.
) else (
  echo.
  echo Saat esitlendi.
)

echo.
echo Simdi:  ayarlari-kontrol-et.bat
echo.
pause
