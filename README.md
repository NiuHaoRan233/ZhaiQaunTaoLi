# G三峡EB2 Tick数据库与M0模拟盘

本项目研究 `132026.SH`（G三峡EB2）相对 `600900.SH`（长江电力）的日内盘口机会。
当前主线不是估算长期内在价值，而是以近期价格状态为锚，捕捉短暂失衡、盘口恢复和买卖价差。

项目包含两个可直接运行的任务：

1. 从 MiniQMT 持续保存 Level 1 tick、五档盘口及其变化，维护本地 SQLite 数据库。
2. 在同一行情流上运行 M0 信号以及 E1-E4 四类执行方式的只读模拟盘。

代码不导入交易接口，不会向券商发送真实委托。

## 快速开始

先打开并登录 MiniQMT，然后在 PowerShell 中运行：

```powershell
cd C:\Users\Administrator\Desktop\重操旧业
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml backfill --days 7
powershell -ExecutionPolicy Bypass -File .\scripts\run_live.ps1
```

另开一个终端检查状态：

```powershell
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml status
```

按 `Ctrl+C` 正常停止，随后创建一致性备份：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\backup_data.ps1
```

## 数据环境

- MiniQMT安装目录：`D:\东北证券NET专业版`
- 本机行情端口：`58611`
- Python连接：`xtdata.connect(port=58611)`
- 默认采集：`132026.SH`、`132024.SH`、`600900.SH`
- 当前可用：实时行情、Level 1五档盘口、累计成交字段和历史tick快照
- 当前不可用或尚未确认：交易所Level 2逐笔委托与真实排队位置

## 项目文档

- [2026-08-10研究记录](docs/2026-08-10_研究记录.md)
- [M0局部盘口模型v1.0](docs/M0_局部盘口模型_v1.0.md)
- [实时采集与模拟盘运行手册](docs/实时采集与模拟盘运行手册.md)
- [Windows换机迁移教程](docs/Windows换机迁移教程.md)

## 当前决定

- `M0`近期价格均值回归是主信号模型。
- 复杂条件估值模型降级为研究和风险标签，不拦截M0交易。
- 后续先接只读实时模拟盘，不向券商发送真实委托。
- 主信号与执行方式分离，分别测试主动成交、触发后挂单、预埋买单和双边挂单。

## 命令

- `doctor`：检查配置、数据库、MiniQMT连接和所有代码快照。
- `backfill`：回灌历史tick并幂等重建盘口变化与M0观测。
- `run`：实时采集并运行模拟盘；同一数据库只允许一个实例。
- `status`：显示数据量、配对质量、会话状态和模拟成绩。
- `snapshot`：保存一次当前快照，不运行模拟成交。
- `backup`：使用SQLite在线备份接口生成可迁移文件。
- `maker-report`：只读回放本地Level 1数据，输出做市策略V0.1的价格锚、低价接砸和扫尾候选报告。

做市策略V0.1的规则、成交口径和已知限制见
[做市策略V0.1](docs/做市策略V0.1.md)。

## 做市模拟盘

`[maker_paper]`启用后，`run`会在同一条只读行情流上并行运行做市模拟盘。它只向本地
SQLite写入虚拟委托与成交，不连接交易接口。默认同时记录两种执行口径：

- `priority`：买价改善一厘成为新买一，按第一顺位估计成交；
- `queue`：加入原买一，必须先消耗快照中显示的排队量。

可选的`super_windfall`是第三个完全隔离的纸面账户，默认只使用一手（10张）和2,000元虚拟额度，专门预埋到相对近期合理价低至少1.50元的异常深档。它不会调用券商资金，也不占普通做T账户库存；当前只定义买入，退出规则仍待人工校准。

账户每天从1,000张底仓和可再买1,000张的现金开始，库存限制为0至2,000张。查看实时
结果：

```powershell
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml status
```

关注输出中的`maker_paper_accounts`和`maker_paper_fills`。`trading_pnl`会按恢复期初
1,000张底仓所需的盘口价格对库存差额盯市；它不是券商账户实际盈亏。

## 现有代码

- `backtest_eb2_v02.py`：复杂条件估值的对照回测，不是当前主策略实现。
- `src/zhaiquant/`：实时数据库、M0和模拟执行引擎。
- `tests/`：去重、盘口变化、M0、成交和重启恢复测试。
