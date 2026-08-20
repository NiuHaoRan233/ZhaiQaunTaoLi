@echo off
chcp 65001 >nul
"%~dp0..\.venv\Scripts\python.exe" "%~dp0reader.py" calibrate --name order_events --row-height 18 --newest-at bottom --max-rows 20
echo.
pause
