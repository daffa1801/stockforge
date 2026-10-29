@echo off
title StockForge - Install
cd /d "%~dp0"

echo === Membuat virtualenv Python 3.12 ===
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.12 -m venv .venv || py -m venv .venv
) else (
  python -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Gagal membuat virtualenv. Pastikan Python 3.10+ terpasang.
  pause
  exit /b 1
)

echo === Memasang dependensi ===
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo Selesai. Berikutnya:
echo   1. salin config.example.json menjadi config.json
echo   2. jalankan start.bat
echo.
pause
