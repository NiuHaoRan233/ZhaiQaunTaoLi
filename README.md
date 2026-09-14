# 债券套利策略：只读行情、逐笔审计与做市纸面盘

本仓库从 MiniQMT 只读采集行情，研究交换债和商品期权的盘口，并用独立、可追溯的纸面账户验证策略。还包含通达信逐笔审计、行情看板、屏幕 OCR 和债券活跃度观察工具。所有订单、成交、账户和收益均为本地模拟，不导入或调用 `xttrader`，不向券商发送真实委托。

## 使用入口

| 需要做什么 | 入口 |
|---|---|
| 安装、启动债券采集和做市纸面盘 | 下方[Windows 快速开始](#windows-快速开始)及[运行手册](docs/实时采集与模拟盘运行手册.md) |
| 使用商品期权看板、纸面策略或黄金离线研究 | [广义套利](广义套利/README.md) |
| 查找策略原文、模型身份、研究和核验记录 | [文档索引](docs/README.md) |
| 换电脑或迁移本地账户 | [Windows 换机迁移教程](docs/Windows换机迁移教程.md) |

当前商品期权纸面入口是[双侧成交策略0.1](广义套利/commodity_strategy/README.md)。黄金[日内估值0.1](广义套利/gold_intraday_strategy/README.md)和[大价差做市0.2](广义套利/gold_intraday_strategy_v02/README.md)是冻结行情的离线复现包。每日换约、日夜分时、先多先空及盈亏归因等后续研究见文档索引；单日收益不代表多日或前向验证通过。历史进度保留在[研究进度归档](广义套利/研究进度归档.md)。

## 当前研究范围

| 债券 | 对应正股 | 用途 |
|---|---|---|
| `132026.SH` G三峡EB2 | `600900.SH` 长江电力 | 行情采集、M0、做市纸面盘和逐笔审计 |
| `132024.SH` 26江铜EB | `600362.SH` 江西铜业 | 行情采集、做市纸面盘和逐笔审计 |

M0 仍只评估主配对 `132026.SH` / `600900.SH`。做市纸面盘同时覆盖两只债券，各债券和各模型使用彼此隔离的账户。

已登记的生产基线是：

- 第一顺位 `maker_priority_v1_1`
- 排队成交 `maker_queue_v1_0`
- 超级捡漏 `maker_windfall_v1_0`

其中第一顺位1.1、排队1.0和超级捡漏1.0保留版本身份与历史账本；普通1.1/1.0已从后续实时纸面盘停用。当前持久化实时比较矩阵按以下顺序运行：

1. `maker_priority_v1_37_candidate`
2. `maker_priority_v1_50_candidate`（公开显示“第一顺位1.50”）
3. `maker_priority_v2_52_candidate_r2`（公开显示“第一顺位2.52”）
4. `maker_priority_v2_63_candidate`（公开显示“第一顺位2.63”）
5. `maker_priority_v2_70_candidate_r2`（公开显示“第一顺位2.70”）
6. `maker_priority_v2_71_candidate`（公开显示“第一顺位2.71”）
7. `maker_shared_1000_v0_1_candidate`（公开显示“千张第一顺位0.1候选”）
8. `maker_shared_1000_v0_13_candidate`（公开显示“千张第一顺位0.13候选”）
9. `maker_shared_1000_v0_16_candidate_r2`（公开显示“千张第一顺位0.16（实时修订）”）
10. `maker_dadao_v0_1_candidate_r2`（公开显示“大道至简0.1（实时修订）”）

2026-09-11按用户指派加入2.71，原九模型保留，形成十模型比较矩阵。2.71沿用已验证的低接断档保护及严格被动成交时序，每债千张底仓和额外千张容量，普通当日账本按保存行情重建，启用前部分属于回放。详见[2.71记录](docs/第一顺位2.71低接与断档风险纠正.md)。

2026-09-08按用户指派追加大道至简，保留原八模型。它以归档极简双债择标0.1为直接父版，单独使用零底仓、1,000张共享资金；买一买到即跟卖一、卖清再择标。实时修订只适配实际接收时钟和日志恢复，不移植普通原生交易判断。首次启用从实际接收输入开始，重启只重放本模型日志；每日独立重置，旧24日88,446.53元仍属于原归档口径。见[大道至简实时接入](docs/大道至简0.1实时接入.md)。

2026-09-08追加0.16实时修订，原七模型保留。它直接继承不可变0.16的原生交易判断，单独处理真实接收顺序、双时钟成交限制和接收日志恢复；自身两债共享一块独立千张槽，不占0.1或0.13资金。旧0.16市场时间排序报告保持不变，不能当作r2实际到达时序报告。普通/0.1/0.13按原方式恢复；r2仅从`maker_shared_arrival_events`原处理序恢复，不把启用前或补录行情插入前向账本。

普通第一顺位模拟盘现在推进彼此独立的1.37、1.50、2.52、2.63、2.70和2.71。2.52 r2以不可变2.52为直接父版，补入“有效客户底仓回补持续暴露在可靠第一顺位”的已确认原则；2.63则以不可变2.6 r3为直接父版，交易行为不变但使用新的模型身份和独立账本。2.70使用直接基于2.63开发并验证的内部r2，按2026-09-07用户指派追加，不替换2.63；首轮2.70与2.69仍离线。千张第一顺位是独立分支，当前另行推进0.1、0.13和0.16实时修订：每个模型内部由两只债共享一块1,000张资金，三个模型之间不共享资金或状态。退出矩阵的2.5、2.51、2.6、其余千张共享0.x、queue、Windfall及其祖先和历史账本均不删除、改名或倒写。进入实时纸面账户只表示收集未讲解日证据，不表示已经晋级生产模型。正式状态和版本血缘以 [做市模型版本记录](docs/做市模型版本记录.md) 为准。

第一顺位2.5于2026-08-31重新加入实时纸面矩阵；同日14:09复核又确认旧2.x分支遗漏1.50已验收的底仓回补盘口暴露原则，因此后续矩阵切换到2.5 r2、2.51 r3、2.52 r2。旧2.5、2.51 r2、2.52账户、报告及模型ID均保持不可变。

第一顺位2.52 r2继续完整保留旧2.52的盈利卖档尾量回补和强收益风险低接覆盖，只增加底仓被动回补实时第一顺位暴露；该修订不是冻结、生产部署或晋级。

第一顺位2.6保留2.52 r2的普通低接；满仓额外批次的原承托联合塌陷、卖盘下压且低位出现新承托时，先被动释放额外1,000张回到中性，再在更低买盘重新部署容量。修订版每次向下撤改都重新检查损失边界和主动深折价买入的一致性，不把会主动买的异常低价反向卖掉；释放后的客户底仓由低位计划与真实高侧恢复动态决定，不使用固定600秒保护。当前r3在真实主动买累计至少1,000张达到或越过原失败入场价后，只解除该次承托塌陷止错生成的未完成低补身份，让父版普通做T重新判断；未恢复到原入场区时继续保留低位计划。它不把正股走弱作为独立触发器，也不是生产晋级。

超级捡漏2.0候选使用一块独立1,000张风险容量：既可在相邻买档断层和独立参考折价均至少1.00元时改善一厘预埋，也可在卖一相对独立参考折价至少1.00元、卖一到卖二断层至少1.00元且卖一完整显示1,000张时主动纸面买入。它仍没有退出规则，不能与普通做T收益合并，也不代表生产晋级。

`maker_shared_1000_v0_1_candidate`从第一顺位2.52 r2分叉，公开称“千张第一顺位0.1”：日初零底仓并让两只债共享本模型唯一1,000张（100手）现金槽；它于2026-09-02进入持久化实时纸面比较，但仍是未冻结、未生产部署的候选。0.13以不可变0.12为直接父版，只修正合法主动深折价完整吃完卖一后的剩余卖档估值；用户查看日度对比后明确将0.13与0.1同时放入模拟盘。0.1、0.13及新增0.16实时修订各有自己的共享运行时与资金槽，不继承其他模型账本。0.11 r2、0.2、0.3、0.31和0.12继续保留为离线研究版本，最终指派不包括0.3。普通第一顺位1.x/2.x按每债独立账户使用1,000张客户底仓和额外1,000张买入能力；千张第一顺位0.x则是两债共享一块现金、零底仓，二者不合并。此前`maker_one_hand_v0_1_candidate`是把用户1,000张口径误读为10张产生的错误实验，已停止推进，仅保留审计记录。

千张第一顺位候选可用只读历史库同时比较共享因果分配、固定单债和两类上界；1,000张严格等于100手：

```powershell
.\.venv\Scripts\python.exe -m zhaiquant.shared_thousand_maker_research --config config.toml --dates 2026-08-28 2026-08-31 2026-09-01 --codes 132026.SH 132024.SH --output output\research\shared_1000_v01_recent.json
.\.venv\Scripts\python.exe -m zhaiquant.shared_thousand_maker_v011_research --config config.toml --dates 2026-08-28 2026-08-31 2026-09-01 --codes 132026.SH 132024.SH --output output\research\shared_1000_v011_recent.json
.\.venv\Scripts\python.exe -m zhaiquant.shared_thousand_maker_v02_research --config config.toml --dates 2026-08-28 2026-08-31 2026-09-01 --codes 132026.SH 132024.SH --output output\research\shared_1000_v02_recent.json
.\.venv\Scripts\python.exe -m zhaiquant.shared_thousand_maker_v03_research --config config.toml --dates 2026-08-28 2026-08-31 2026-09-01 --codes 132026.SH 132024.SH --output output\research\shared_1000_v03_recent.json
.\.venv\Scripts\python.exe -m zhaiquant.shared_thousand_maker_v031_research --config config.toml --dates 2026-08-28 2026-08-31 2026-09-01 --codes 132026.SH 132024.SH --output output\research\shared_1000_v031_recent.json
.\.venv\Scripts\python.exe -m zhaiquant.shared_thousand_maker_v012_research --config config.toml --dates 2026-08-28 2026-08-31 2026-09-01 --codes 132026.SH 132024.SH --output output\research\shared_1000_v012_recent.json
```

巨鲸候选可用只读历史库复核并同时运行墙倍数/持续时间敏感性：

```powershell
.\.venv\Scripts\python.exe -m zhaiquant.whale_maker_research --dates 2026-08-11 2026-08-12 2026-08-13 2026-08-14 2026-08-17 2026-08-18 2026-08-21 --sensitivity
.\.venv\Scripts\python.exe -m zhaiquant.whale_maker_research --model-version v0.2 --dates 2026-08-11 2026-08-12 2026-08-13 2026-08-14 2026-08-17 2026-08-18 2026-08-21 --attribution-sensitivity
```

## Windows 快速开始

先安装并登录 MiniQMT，确认只读行情端口可用（默认 `58611`），然后在 PowerShell 中运行：

```powershell
git clone https://github.com/NiuHaoRan233/ZhaiQaunTaoLi.git
Set-Location -LiteralPath .\ZhaiQaunTaoLi
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

安装脚本会创建 `.venv`、安装项目、从 `config.example.toml` 生成本地 `config.toml`、运行核心测试并执行 MiniQMT 只读诊断。启动前应检查 `config.toml` 中的证券代码、端口、数据库路径和纸面账户容量。
整个项目换盘符或移动目录后，应在新根目录再执行一次安装脚本（可加 `-SkipDoctor`），用于刷新 `.venv` 中的可编辑安装定位。日常模拟盘和看板启动器会优先加载它们所在项目的 `src`，可避免误用旧盘代码。

回灌历史行情并启动实时采集：

```powershell
.\.venv\Scripts\python.exe -m zhaiquant --config config.toml backfill --days 7
powershell -ExecutionPolicy Bypass -File .\scripts\run_live.ps1
```

也可以直接双击项目根目录的 `start_live.cmd`。启动脚本会先完成只读诊断；诊断成功后，如果本项目同一配置的旧 `zhaiquant run` 仍在后台运行，会只结束该旧模拟后台及其 Python 子进程，再在新窗口前台启动替代实例。MiniQMT、做市看板和其他 Python 程序不会被结束。直接绕过脚本启动第二个 `zhaiquant run` 时，数据库单实例锁仍会拒绝它。

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
| `实盘决策看板/` | 本地 Web 看板、模拟回看及手动接管干运行界面 |
| `半自动手动交易/` | 独立的双债比价追价状态机、干运行执行器和本地审计 |
| `行情屏幕高速读取/` | Windows 行情屏幕 OCR 读取工具 |
| `债券活跃度观察/` | 只读债券活跃度扫描工具 |
| `广义套利/` | 商品期权全目录观察、[盘口看板](广义套利/option_dashboard/README.md)、双侧成交纸面0.1、黄金离线策略包与研究索引；行情、账户和报告仅本地保存 |
| `成交委托数据截图保存/` | 本地通达信截图/OCR 工作区；仓库只保存其边界说明 |

文档总入口见 [docs/README.md](docs/README.md)。新的策略会话先按仓库 skill 校验并完整读取[策略会话启动摘要](docs/策略会话启动摘要.md)，再按案例查阅以下权威原文；摘要过期时按 skill 的回退流程核对变化：

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
.\.venv\Scripts\python.exe -m unittest discover -s 半自动手动交易\tests -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s 行情屏幕高速读取\tests -p "test_*.py" -v
```

生成商品期权和黄金研究曲线时，额外安装报告依赖：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pip install -e ".[research]"
```

通达信截图辅助脚本的离线测试需要 Node.js 20.9及以上；首次安装锁定的开发依赖后运行：

```powershell
npm ci
npm test
```

Node.js仅用于这组辅助测试，普通行情采集、纸面策略及期权看板不依赖它。

## 本地数据边界

以下内容是运行状态或私人证据，不进入 Git：`config.toml`、`data/`、`logs/`、`backups/`、`tmp/`、`output/`、SQLite/WAL/SHM、通达信截图与 OCR 结构化结果、看板/屏幕读取运行态、活跃度扫描数据和旧工作资料。

普通做市账户默认以 1,000 张客户底仓开盘，并具有额外买入 1,000 张的纸面能力，因此正常库存范围为 0—2,000 张。1 手交换债等于 10 张；配置、报告与讨论不得混用“手”和“张”。

广义套利的 `data/` 和 `reports/` 同样仅保存在本机。GitHub 提供代码、固定策略配置与研究文档，不包含冻结行情、账户数据库或生成报告。离线复现必须先取得相应本地输入，并通过合同与哈希核验。
