@echo off
chcp 65001 >nul
"%~dp0..\.venv\Scripts\python.exe" "%~dp0reader.py" inspect --show
echo.
pause
