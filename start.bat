@echo off
title StockForge
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtualenv belum ada. Jalankan install.bat dulu.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" "app\server.py"
pause
