@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
".venv\Scripts\python.exe" -X utf8 -m zhaiquant.commodity_flow_cli init
if errorlevel 1 goto failed
echo 商品期权双侧成交策略0.1：只读QMT，纸面撮合。按 Ctrl+C 停止并保留持仓。
echo 日盘时段外自动等待；主策略和对照各自独立资金，不向券商发单。
".venv\Scripts\python.exe" -X utf8 -m zhaiquant.commodity_flow_cli paper
if errorlevel 1 goto failed
exit /b 0
:failed
echo 启动未完成。请查看上面的具体错误，勿删除原账户重置收益。
pause
exit /b 1
