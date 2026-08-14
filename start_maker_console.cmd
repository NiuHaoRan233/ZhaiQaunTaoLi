@echo off
title 132026.SH G三峡EB2 做市模拟盘监控
cd /d "%~dp0"
set PYTHONUTF8=1
mode con cols=180 lines=55 >nul 2>&1
if not exist ".\.venv\Scripts\python.exe" (
  echo Python virtual environment not found. Run scripts\setup_windows.ps1 first.
  pause
  exit /b 1
)
".\.venv\Scripts\python.exe" -m zhaiquant --config config.toml maker-console --bond-code 132026.SH
if errorlevel 1 echo Dashboard exited with an error. See the message above.
echo Press any key to close this window.
pause >nul
