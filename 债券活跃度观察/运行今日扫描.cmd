@echo off
chcp 65001 >nul
"%~dp0..\.venv\Scripts\python.exe" "%~dp0scan_active_bonds.py" %* || goto :error
exit /b 0

:error
echo.
echo Scan failed. See the error above.
pause
exit /b 1
