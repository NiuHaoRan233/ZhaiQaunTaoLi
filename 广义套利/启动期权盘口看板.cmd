@echo off
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0option_dashboard\start.ps1"
if errorlevel 1 pause
