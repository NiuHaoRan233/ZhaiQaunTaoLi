# 广义套利

从交换债盘口做市经验出发，探索商品期权等市场中有成交、可周转的价差机会。本目录包含只读行情扫描、盘口看板、纸面策略入口及黄金离线研究包；可执行策略真源统一位于仓库根目录的 `src/zhaiquant/`。

## 运行入口

| 工具或策略 | 运行方式 | 说明 |
|---|---|---|
| [商品期权盘口看板](option_dashboard/README.md) | 双击 `启动期权盘口看板.cmd` | 只读QMT行情，浏览器地址 <http://127.0.0.1:8766>；停止用 `停止期权盘口看板.cmd` |
| [商品期权双侧成交策略0.1](commodity_strategy/README.md) | 双击 `启动商品期权量化策略.cmd` | 当前固定配置为 `strategy_r2.json`，主候选与对照独立纸面运行 |
| 商品期权纸面日报 | 双击 `查看商品期权策略日报.cmd` | 展示全部64合约账户、日度和尾仓 |
| 商品期权开仓控制 | 双击 `暂停商品期权策略开仓.cmd` 或 `恢复商品期权策略开仓.cmd` | 暂停仅控制新开仓，已有库存仍按规则处理 |
| 商品期权账本核验 | 双击 `核验商品期权策略账本.cmd` | 从自身接收日志重建账户和流水 |
| [黄金日内估值做市0.1](gold_intraday_strategy/README.md) | 双击 `复现黄金期权日内策略0.1.cmd` | 只读复现2026-09-11冻结输入，不是实时入口 |
| [黄金大价差做市0.2](gold_intraday_strategy_v02/README.md) | 根目录运行 `scripts/run_gold_intraday_v02.py` | 固定开发日、固定分支的8账户离线复现 |

纸面程序不向券商发单。黄金历史研究与上述商品期权纸面账户使用各自的身份、输入和账本，成绩分别报告。

## 安装与目录

应克隆并安装整个[主仓库](../README.md)，不能只下载本目录。运行入口依赖根目录的 `.venv`、`config.toml`、`src/zhaiquant/` 和 `scripts/`。Windows 需要 Python 3.11及以上；看板启动器还需要 PowerShell 7。QMT 默认端口58611，实际连接以本地配置为准。

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

只读行情看板和纸面程序需要已登录的 MiniQMT 行情服务。安装和迁移见[主仓库快速开始](../README.md#windows-快速开始)及[换机教程](../docs/Windows换机迁移教程.md)。

| 路径 | 用途 | 是否上传 Git |
|---|---|---|
| 本目录的扫描 `.py` 文件 | 目录枚举、历史初筛、深化与核验 | 是 |
| `option_dashboard/` | 无外部前端依赖的本地盘口看板 | 是 |
| `commodity_strategy/`、`gold_intraday_strategy*/` | 固定策略配置和使用说明 | 是 |
| `../src/zhaiquant/`、`../scripts/`、`../tests/` | 执行真源、研究复现和回归测试 | 是 |
| `../docs/` | 规则、研究结论、模型及核验记录 | 是 |
| `data/`、`reports/` | 行情、冻结输入、账户、缓存、日志与生成报告 | 否 |

GitHub不包含本地行情和生成HTML。扫描可以重新采集新数据；冻结策略复现需要相应原始输入和合同哈希，缺少输入时不能用新行情代替旧输入或声称已经重现历史结果。SQLite账户不可通过复制活动WAL主库备份，按对应工具说明正常停止或使用在线backup API。

## 研究索引

当前研究原文统一收录于[文档索引](../docs/README.md)，整理前的进度和全部本地报告入口保留在[研究进度归档](研究进度归档.md)。

| 研究方向 | 长期记录 |
|---|---|
| 商品期权全目录观察与大道至简循环 | [首轮试验](../docs/商品期权大道至简首轮试验.md)、[收益归因与首轮优化](../docs/商品期权收益归因与首轮优化.md) |
| 商品期权纸面策略与共享资金 | [双侧成交策略0.1](../docs/商品期权双侧成交策略0.1.md)、[共享资金与第二轮优化](../docs/商品期权共享资金与第二轮优化.md) |
| 黄金估值与大价差规则 | [同步定价与退出](../docs/黄金期权同步定价与退出研究.md)、[大价差与期货结合](../docs/黄金期权大价差与期货结合研究.md)、[逐条规则与资金核对](../docs/黄金期权规则拆解与资金核对.md) |
| 黄金多日验证与亏损原因 | [历史多策略对比](../docs/黄金期权历史多策略对比研究.md)、[逐笔盈亏复盘](../docs/黄金期权历史盈亏逐笔复盘.md)、[休市排除与靠山保护](../docs/黄金期权休市排除与靠山保护研究.md) |
| 固定方向、每日换约与时段差异 | [先多先空完整对照](../docs/黄金期权先多先空完整对照.md)、[按百分比每日换约](../docs/黄金期权按百分比每日换约回测.md)、[日夜及分时段比较](../docs/黄金期权日夜及分时段比较.md) |

已完成的单日黄金研究尚不能认定稳定盈利；历史多日对照存在亏损、盈利集中和休市清仓失败。完整失败、严格成交对照和旧版本均保留。原交换债策略的权威背景见[主观做市策略手册](../docs/主观做市策略手册.md)。

## 扫描与核验

扫描入口依次为 `probe_commodity_options.py`（枚举目录）、`probe_history.py`（历史覆盖核验）、`capture_daily.py`（下载日线）、`scan_history.py`（初筛）、`deepen_shortlist.py`（扩展重点盘口）、`audit_measurements.py`/`finalize_audit.py`（独立核对）和 `build_report.py`（生成报告）。首次观察为2026-09-12，覆盖QMT四所64品种；能源中心及部分夜盘覆盖缺口不能称为全国市场完整覆盖。

在仓库根目录执行测试：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -v
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s 广义套利 -p "test_*.py" -v
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s 广义套利\option_dashboard -p "test_*.py" -v
```

研究复现脚本的参数、固定身份与输入要求见相应研究文档。不要直接运行所有研究脚本；其中部分入口会只读连接QMT、下载大量行情或创建新的独立研究输出。

生成研究图表前，在仓库根目录运行 `.\.venv\Scripts\python.exe -X utf8 -m pip install -e ".[research]"` 安装可选的绘图依赖。
