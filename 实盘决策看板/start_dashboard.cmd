@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set "PYTHONPATH=%~dp0..\src;%PYTHONPATH%"
if not exist "..\.venv\Scripts\python.exe" (
  echo Python virtual environment not found. Run ..\scripts\setup_windows.ps1 first.
  pause
  exit /b 1
)
"..\.venv\Scripts\python.exe" server.py
if errorlevel 1 (
  echo Dashboard exited with an error. See the message above.
  pause
)
endlocal
