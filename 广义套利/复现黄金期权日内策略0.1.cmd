@echo off
cd /d "%~dp0\.."
".venv\Scripts\python.exe" -X utf8 scripts\run_gold_intraday_value.py
pause
