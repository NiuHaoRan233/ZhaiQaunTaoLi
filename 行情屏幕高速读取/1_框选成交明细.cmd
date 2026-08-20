@echo off
chcp 65001 >nul
"%~dp0..\.venv\Scripts\python.exe" "%~dp0reader.py" calibrate --name trade_details --row-height 18 --newest-at bottom --max-rows 4 --profile futures_trades
echo.
pause
