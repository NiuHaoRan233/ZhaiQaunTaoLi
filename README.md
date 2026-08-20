# 债券套利策略：只读行情、逐笔审计与做市纸面盘

本仓库从 MiniQMT 只读采集行情，研究两只交换债的日内盘口，并用独立、可追溯的纸面账户运行 M0 和做市策略。项目还包含通达信逐笔数据审计、实时决策看板、行情屏幕 OCR 与债券活跃度观察工具。

代码不会连接交易接口，不导入或调用 `xttrader`，不会向券商发送真实委托。所有“订单”“成交”“账户”和“收益”均为本地模拟结果。

## 当前研究范围

| 债券 | 对应正股 | 用途 |
|---|---|---|
| `132026.SH` G三峡EB2 | `600900.SH` 长江电力 | 行情采集、M0、做市纸面盘和逐笔审计 |
| `132024.SH` 26江铜EB | `600362.SH` 江西铜业 | 行情采集、做市纸面盘和逐笔审计 |

M0 仍只评估主配对 `132026.SH` / `600900.SH`。做市纸面盘同时覆盖两只债券，各债券和各模型使用彼此隔离的账户。

当前生产基线是：

- 第一顺位 `maker_priority_v1_1`
- 排队成交 `maker_queue_v1_0`
- 超级捡漏 `maker_windfall_v1_0`

当前持久化实时比较矩阵按以下顺序运行：

1. `maker_priority_v1_37_candidate`
2. `maker_priority_v1_43_candidate`
3. `maker_queue_v1_17_candidate`

候选进入实时纸面账户只表示正在收集未讲解日证据，不表示已经晋级生产模型。正式状态和版本血缘以 [做市模型版本记录](docs/做市模型版本记录.md) 为准。

## Windows 快速开始

先安装并登录 MiniQMT，确认只读行情端口可用（默认 `58611`），然后在 PowerShell 中运行：

```powershell
git clone https://github.com/NiuHaoRan233/ZhaiQaunTaoLi.git
Set-Location -LiteralPath .\ZhaiQaunTaoLi
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

安装脚本会创建 `.venv`、安装项目、从 `config.example.toml` 生成本地 `config.toml`、运行核心测试并执行 MiniQMT 只读诊断。启动前应检查 `config.toml` 中的证券代码、端口、数据库路径和纸面账户容量。

回灌历史行情并启动实时采集：

```powershell
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml backfill --days 7
powershell -ExecutionPolicy Bypass -File .\scripts\run_live.ps1
```

另开终端查看纸面账户或控制台：

```powershell
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml status
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml maker-console
```

按 `Ctrl+C` 正常停止。SQLite 运行库必须通过在线备份命令备份，不能直接复制带 WAL 的活动数据库：

```powershell
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml backup --output backups\zhaiquant.sqlite3
```

完整运行和换机说明见 [实时采集与模拟盘运行手册](docs/实时采集与模拟盘运行手册.md) 与 [Windows换机迁移教程](docs/Windows换机迁移教程.md)。

## 主要命令

| 命令 | 作用 |
|---|---|
| `init-config` | 从示例创建本地配置 |
| `doctor` | 检查配置、数据库、MiniQMT 连接和四代码快照 |
| `snapshot` | 保存一次只读行情快照 |
| `run` | 采集实时行情并推进 M0 与全部纸面模型 |
| `status` | 查看采集、配对和纸面账户状态 |
| `backfill` | 回灌历史 tick，不生成纸面成交 |
| `maker-report` | 用已录制 Level 1 行情生成做市研究报告 |
| `maker-console` | 显示只读做市控制台；`--once` 可在非刷新时段诊断 |
| `tdx-extract-trades` / `tdx-extract-orders` | 从已核验的通达信截图提取逐笔成交/委托 |
| `tdx-opportunity-report` / `tdx-inventory-path` | 构建逐笔机会与事后库存路径上限 |
| `maker-queue-audit` / `maker-opportunity-audit` | 对照逐笔证据审计排队与做市模型 |
| `backup` | 使用 SQLite 在线备份接口生成一致性备份 |

各命令参数可用 `--help` 查看。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `src/zhaiquant/` | 行情采集、SQLite、M0、做市、纸面账本、控制台与逐笔审计真源 |
| `tests/` | 核心引擎、配置、持久化、模型和审计回归测试 |
| `scripts/` | Windows 运行脚本、候选回放与因果审计工具 |
| `docs/` | 策略长期记忆、正式规格、模型注册表和操作手册 |
| `策略自我迭代优化/` | 候选模型冻结清单、回放证据、版本报告和分支索引 |
| `实盘决策看板/` | 只读本地 Web 看板及模拟回看 |
| `行情屏幕高速读取/` | Windows 行情屏幕 OCR 读取工具 |
| `债券活跃度观察/` | 只读债券活跃度扫描工具 |
| `成交委托数据截图保存/` | 本地通达信截图/OCR 工作区；仓库只保存其边界说明 |

文档总入口见 [docs/README.md](docs/README.md)。讨论或修改做市策略前，必须完整阅读：

1. [主观做市策略手册](docs/主观做市策略手册.md)
2. [做市策略V0.1](docs/做市策略V0.1.md)
3. [做市模型版本记录](docs/做市模型版本记录.md)

## 测试

核心测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

独立子工具测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s 债券活跃度观察 -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s 实盘决策看板\tests -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s 行情屏幕高速读取\tests -p "test_*.py" -v
```

## 本地数据边界

以下内容是运行状态或私人证据，不进入 Git：`config.toml`、`data/`、`logs/`、`backups/`、`tmp/`、`output/`、SQLite/WAL/SHM、通达信截图与 OCR 结构化结果、看板/屏幕读取运行态、活跃度扫描数据和旧工作资料。

普通做市账户默认以 1,000 张客户底仓开盘，并具有额外买入 1,000 张的纸面能力，因此正常库存范围为 0—2,000 张。1 手交换债等于 10 张；配置、报告与讨论不得混用“手”和“张”。
