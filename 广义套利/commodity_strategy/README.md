# 商品期权双侧成交策略0.1

完整的只读QMT纸面策略，主候选`flow_patient`和入场对照`flow_entry`各自运行64个独立账户。当前有效家族`commodity_flow_20260913_v0_1_r2`；初稿`strategy.json`保留，运行使用`strategy_r2.json`。每模型初始资金合计1,698,000元，每合约最多1手，禁止卖空或加仓。它是研究与前向验证工具，历史利润不是已验证的实盘收入。

## 使用

在上一级“广义套利”目录双击：

- `启动商品期权量化策略.cmd`：核对配置，连接QMT行情，持续纸面运行；日盘外等待。QMT服务默认端口58611，断线时取消纸面委托并重连。
- `查看商品期权策略日报.cmd`：生成并打开两个模型全部64合约的当前账户、完整日度及尾仓状态。
- `暂停商品期权策略开仓.cmd`：保存暂停请求。运行进程处理后停止新买，已有库存仍可按规则卖出。
- `恢复商品期权策略开仓.cmd`：恢复开仓资格，重新等待足够的双侧证据。
- `核验商品期权策略账本.cmd`：从自己的接收日志重建全部状态和委托/成交，独立核算现金与费用。

停止用运行窗口的Ctrl+C。停止会取消纸面委托，保留现金、持仓和流水；再次启动先恢复自己的账本、清空短窗，再等新行情。不要删除SQLite来抹掉亏损或重复初始化资金。进程崩溃由SQLite事务保证输入与结果一起提交，第二个实例会被操作系统文件锁拒绝。

QMT行情每0.5秒轮询一轮64合约，每个模型按实际处理时刻建立纸面委托。额外延迟0不代表数据传输/轮询为0；这些不是完整逐笔，实际队列和夜盘仍未校准。源报价超过5秒、日期错误、明显未来时间、倒序和异常数据不会形成新有效决策；重复源时间不增加成交证据，变陈旧后撤委托。累计量额重置会撤单并清空证据。合约条款在启动/重连时核对，不匹配即停止，避免错乘数交易。

日盘09:00—10:15、10:30—11:30、13:30—15:00；正常收盘保留库存，没有夜盘交易。到期日只卖不买，到期后冻结撮合并保留待研究结算尾仓，不自动行权或展期。日期选择和资金分配已冻结，不按合约最近盈亏任意换券。

## 命令行

从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m zhaiquant.commodity_flow_cli init
.\.venv\Scripts\python.exe -X utf8 -m zhaiquant.commodity_flow_cli doctor
.\.venv\Scripts\python.exe -X utf8 -m zhaiquant.commodity_flow_cli paper --once
.\.venv\Scripts\python.exe -X utf8 -m zhaiquant.commodity_flow_cli paper
.\.venv\Scripts\python.exe -X utf8 -m zhaiquant.commodity_flow_cli report
.\.venv\Scripts\python.exe -X utf8 -m zhaiquant.commodity_flow_cli audit
```

`--once`只试一次当前行情，不回补历史成交；当前客户端不可达时明确失败并保留待机报告。报告与审计不需要QMT在线。运行还可指定`--config`、`--db`、`--output`、`--port`，仅用于明确的独立账户/环境；同一SQLite的配置和源码合同已锁定，不允许换规则复用账户。

## 文件与证据

- 交易实现：`src/zhaiquant/commodity_flow_strategy.py`；持久化/接收日志：`commodity_flow_paper.py`；入口/日报：`commodity_flow_cli.py`。这里不是第二个实现目录。
- 当前配置：本目录`strategy_r2.json`。初始现金来自原冻结基线，不导入历史盈利作为纸面起始现金。
- 本地纸面账户：`广义套利/data/commodity_flow_v01_r2/paper.sqlite3`及WAL。不要复制活跃WAL库来做备份；本轮不替代原项目备份流程。
- 本地纸面报告：`广义套利/reports/commodity_flow_v01_r2/纸面策略日报.html`、`paper_status.json`、`paper_audit.json`。
- 历史完整复现：`广义套利/reports/commodity_strategy_20260913_r2/完整策略历史日度.html`，两候选全64×15日度，缺失不填0；逐账户gzip文件含完整订单、成交、曲线和终态。
- 复现入口：`scripts/validate_commodity_flow_strategy.py`，校验冻结输入、旧账户和新源码；已经冻结的输出不可用改过的源码覆盖。
- 长期合同与开放问题：[策略说明](../../docs/商品期权双侧成交策略0.1.md)。原黄金/铂等线索保留在上一轮报告，不因通用新策略少赚或不交易而删除。

`data/`、`reports/`内数据是本地运行证据，不提交Git。该策略不会导入券商交易模块，不会给实际账户发单，现有债券模拟矩阵保持原状。
