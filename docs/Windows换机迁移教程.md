# Windows换机迁移教程

版本：2026-08-21

## 1. 旧电脑导出

先正常停止实时程序，再创建一致性数据库备份：

```powershell
cd C:\Users\NHR\Desktop\债券套利策略
powershell -ExecutionPolicy Bypass -File .\scripts\backup_data.ps1
```

需要迁移的本地文件只有：

- 最新的 `backups/zhaiquant-*.sqlite3`
- `config.toml`

代码不需要手工复制，直接从 GitHub 克隆。`logs/` 可选，仅用于保留排错记录。不要复制 `.venv/`，虚拟环境带有旧电脑的绝对路径，换机后应重建。

仓库内的 `.agents/skills/` 也随 Git 一起迁移。Codex 从仓库根目录启动时会自动发现这些项目技能，不需要重新讲解通达信采集步骤，也不需要复制旧电脑的个人技能目录。

## 2. 新电脑准备

1. 安装券商 MiniQMT，登录后开通和确认 `132026.SH`、`600900.SH`、`132024.SH`、`600362.SH` 行情权限。
2. 在 MiniQMT 设置中确认行情端口，默认配置为 `58611`。安装路径可以与旧电脑不同。
3. 如需界面采集逐笔委托和成交明细，安装并登录通达信金融终端。安装盘符和目录可以与旧电脑不同；项目技能会按应用名称和窗口重新发现、校准，不依赖 `D:\TDX`。
4. 安装 64 位 Python 3.11-3.13 和 Git，并确认 PowerShell 中 `python --version`、`git --version` 可用。
5. 克隆仓库并安装依赖：

```powershell
cd C:\Users\你的用户名\Desktop
git clone https://github.com/NiuHaoRan233/ZhaiQaunTaoLi.git
cd .\ZhaiQaunTaoLi
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -SkipDoctor
```

若 `python` 不是目标解释器，可指定完整路径：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -PythonCommand "C:\Python313\python.exe" -SkipDoctor
```

## 3. 恢复配置和数据库

将旧电脑的 `config.toml` 放到仓库根目录。检查以下内容：

```toml
[qmt]
port = 58611
bond_code = "132026.SH"
stock_code = "600900.SH"
watch_codes = ["132024.SH", "600362.SH"]

[storage]
database = "data/zhaiquant.sqlite3"
```

创建 `data` 目录，把备份文件复制并改名为 `zhaiquant.sqlite3`：

```powershell
New-Item -ItemType Directory -Force .\data | Out-Null
Copy-Item "D:\迁移文件\zhaiquant-20260810-151000.sqlite3" ".\data\zhaiquant.sqlite3"
```

数据库路径是相对 `config.toml` 解析的，因此仓库放到其他盘符或用户名目录也能工作。若配置的是绝对路径，换机后必须修改。

## 4. 新电脑验收

打开并登录 MiniQMT，然后依次执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml doctor
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml status
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml run --duration-seconds 30
```

验收标准：

- 自动化测试全部通过。
- `doctor` 显示连接成功，并返回所有采集代码的快照。
- `status` 能看到旧电脑的历史数据和模拟仓位。
- 30秒测试正常结束，`latest_session.status` 为 `completed`，回调丢失数为0。
- 从仓库根目录打开 Codex 后，技能列表可见 `tdx-cb-market-capture`。首次在新电脑运行时，允许它重新识别通达信安装位置和界面坐标。

盘后运行30秒时可能只有启动快照而没有订阅回调，这是正常现象。最终实时订阅必须在交易时段确认：四个代码的 `count` 和 `last_ts` 持续变化。

## 5. 正式切换

不要让两台电脑同时采集到同一个后续数据库后再合并。SQLite 文件不是可自动合并的分布式数据库。

推荐切换顺序：

1. 旧电脑收盘后停止程序并生成最后备份。
2. 将最后备份恢复到新电脑。
3. 新电脑完成 `doctor` 和30秒测试。
4. 下一个交易日只在新电脑启动实时程序。
5. 旧电脑保留只读备份，不再运行采集。

如果只是暂时换机而不需要延续模拟仓位，也仍建议迁移数据库，因为 M0 启动时会读取最近1,200个有效观测进行预热。没有历史库时，程序需要先积累至少600个有效债券观测才会产生入场信号。

## 6. 常见问题

`MiniQMT connection failed on port 58611`：确认 MiniQMT 已登录、端口设置一致，且没有防火墙或安全软件拦截本机连接。

`Another live runner is already using ... live.lock`：已有实时进程正在运行。先找到并正常停止旧窗口。锁由操作系统持有，进程退出后无需删除文件。

某个机构债没有快照：先在 MiniQMT 客户端直接搜索代码，确认账户行情权限和证券代码后，再加入 `watch_codes`。程序不能绕过券商权限。

数据库很大：原始 JSON 和五档快照会持续增长，这是细致采集的预期。每日收盘备份，定期把旧备份转移到容量充足的磁盘，但不要删除唯一副本。
