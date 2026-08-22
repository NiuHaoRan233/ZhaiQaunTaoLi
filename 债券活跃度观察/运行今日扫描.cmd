@echo off
chcp 65001 >nul
echo [%date% %time:~0,8%] Starting bond activity scan. Please wait...
echo Updating historical ticks can take tens of seconds.
echo.
"%~dp0..\.venv\Scripts\python.exe" -u "%~dp0scan_active_bonds.py" %* || goto :error
echo.
echo Scan finished. Press any key to close this window.
pause >nul
exit /b 0

:error
echo.
echo Scan failed. See the error above.
pause
exit /b 1
