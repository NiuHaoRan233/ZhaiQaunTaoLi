@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
".venv\Scripts\python.exe" -X utf8 -m zhaiquant.commodity_flow_cli report
if errorlevel 1 goto failed
start "" "%~dp0reports\commodity_flow_v01_r2\纸面策略日报.html"
exit /b 0
:failed
echo 日报未生成。首次使用请先初始化并运行纸面策略。
pause
exit /b 1
