# 千张第一顺位分支

最后更新：2026-09-02

本目录名作为旧技术归档路径保留，正文统一使用公开称呼“千张第一顺位”。该家族
日初零底仓，两只债共享一块1,000张（100手）纸面资金；它是独立0.x家族，不属于
普通第一顺位1.x/2.x、排队或Windfall账户。普通第一顺位按每债独立账户使用1,000张
客户底仓和额外1,000张买入能力，正常库存0—2,000张；千张第一顺位两债合计库存
0—1,000张。两类模型ID、父链、账户和历史账本不得合并。

## 当前候选与归档

- `maker_shared_1000_v0_1_candidate`：公开称“千张第一顺位0.1”，首版共享择优，直接父ID为
  `maker_priority_v2_52_candidate_r2`；已完成研究归档锁定，历史参数和结果不可变。
  [研究摘要](千张共享资金0.1_二十一日回放研究归档_2026-09-02.md)；
  [归档清单](maker_shared_1000_v0_1_研究归档清单_2026-09-02.json)。
- `maker_shared_1000_v0_11_candidate_r2`：公开称“千张第一顺位0.11”，只在0.1父版处理后普通
  做T批次确实没有卖单时补卖方第一顺位；首版`maker_shared_1000_v0_11_candidate`
  因覆盖所有买入批次和父版原生卖单而撤回，仅保留历史审计。重做版已完成研究归档
  锁定，但未作样本外冻结、未进实时矩阵、未生产部署。[研究摘要](千张共享资金0.11_退出空档兜底重做_2026-09-02.md)；
  [归档清单](maker_shared_1000_v0_11_r2_研究归档清单_2026-09-02.json)。
- `maker_shared_1000_v0_2_candidate`：公开称“千张第一顺位0.2”，是0.1直接子版，把现金设为第三候选，并使用
  价格局部流量、非零尾部风险、主动逆向选择和预计锁资时间评分；总毛值低于0.1，
  但占资效率和历史尾部路径改善。它仍是未冻结、未进实时矩阵的开发候选。
  [研究摘要](千张共享资金0.2_风险调整占资选择研究_2026-09-02.md)；
  [归档清单](maker_shared_1000_v0_2_研究归档清单_2026-09-02.json)。
- `maker_shared_1000_v0_3_candidate`：公开称“千张第一顺位0.3”，是0.2直接子版，用日内高侧价格韧性、低侧
  入场可达性和当前承托形成复合候选，再与三峡、江铜和现金统一比较；已修复
  2026-09-02江铜10:44漏选并完成21日开发回放，仍未冻结、未进实时矩阵。
  [研究摘要](千张共享资金0.3_日内韧性综合选择研究_2026-09-02.md)；
  [归档清单](maker_shared_1000_v0_3_研究归档清单_2026-09-02.json)。
- `maker_shared_1000_v0_31_candidate`：公开称“千张第一顺位0.31”，是0.3直接子版，完整保留0.3综合
  入场和资金选择，仅对普通`low_bid_reversion`批次增加重做0.11的父版退出空档兜底；
  21日持仓时间下降但总毛值和亏损段退化，仍是未冻结、未进实时矩阵的离线候选。
  [研究摘要](千张共享资金0.31_0.3合并0.11走弱退出_2026-09-02.md)；
  [归档清单](maker_shared_1000_v0_31_研究归档清单_2026-09-02.json)。
- `maker_shared_1000_v0_12_candidate`：公开称“千张第一顺位0.12”，是有效0.11 r2直接子版，
  在保留普通退出空档兜底的同时补入0.3完整韧性候选与风险调整资金选择。21日相对
  0.11毛值降低但占资、最差闭合和终仓浮亏改善；当前与0.31路径等价，父链分别保存。
  [研究摘要](千张共享资金0.12_0.11补入日内韧性综合选择_2026-09-02.md)；
  [归档清单](maker_shared_1000_v0_12_研究归档清单_2026-09-02.json)。
- `maker_shared_1000_v0_13_candidate`：公开称“千张第一顺位0.13”，是不可变0.12直接
  子版，只修正父版合法主动深折价完整吃掉卖一时的成交后剩余卖档估值。21日相对
  0.12毛值增加5,679.97元，亏损闭合、最差闭合和终仓浮亏不增加；用户随后将其
  与既有0.1一起指派进入实时模拟盘，两个模型各有独立1,000张资金槽。0.13仍未
  冻结、未生产部署。[研究摘要](千张共享资金0.13_主动深折价剩余卖档估值_2026-09-02.md)；
  [归档清单](maker_shared_1000_v0_13_研究归档清单_2026-09-02.json)。

## 边界

- 日初0张底仓；两债合计最多持有1,000张，只允许一个有效买单和一个共享现金槽。
- 资金按两债首个合法卖一中的较高者乘1,000一次性初始化；亏损后不补资。
- 0.1/0.2分配层只排名已经通过2.52 r2父版合法性检查的买入意图。0.3另行登记
  日内韧性替代许可，只替代固定承托不足，不替代普通合理价优势，并仍须赢过现金。
- 0.13只在父版合法`deep_discount_sweep`将完整消耗当前卖一且五档存在更高剩余
  卖档时，移除已消耗卖一后重估退出上限；部分吃单、扫尾和普通被动单不变。
- 0.1的60秒、100元、30%和10元，以及0.2的局部价格带、风险权重、占资时间、
  60秒、50元、30%、5元和9秒影子状态，都是版本化探索参数，不是永久阈值。
- `maker_one_hand_v0_1_candidate`是把1,000张误读成10张的错误同父子版，只保留
  审计证据，永久停止推进，不得作为本模型父版或结果基线。

## 可执行真源与本地报告

- 模型注册：`src/zhaiquant/maker_paper.py`
- 正确千张入口：`src/zhaiquant/shared_thousand_maker_research.py`
- 0.11入口：`src/zhaiquant/shared_thousand_maker_v011_research.py`
- 0.2入口：`src/zhaiquant/shared_thousand_maker_v02_research.py`
- 0.3入口：`src/zhaiquant/shared_thousand_maker_v03_research.py`
- 0.31入口：`src/zhaiquant/shared_thousand_maker_v031_research.py`
- 0.12入口：`src/zhaiquant/shared_thousand_maker_v012_research.py`
- 0.13入口：`src/zhaiquant/shared_thousand_maker_v013_research.py`
- 共享分配与回放核心：`src/zhaiquant/one_hand_maker_research.py`
- 聚焦测试：`tests/test_shared_thousand_maker_research.py`、
  `tests/test_shared_thousand_maker_v011_research.py`、
  `tests/test_shared_thousand_maker_v02_research.py`、
  `tests/test_shared_thousand_maker_v03_research.py`、
  `tests/test_shared_thousand_maker_v031_research.py`、
  `tests/test_shared_thousand_maker_v012_research.py`、
  `tests/test_shared_thousand_maker_v013_research.py`
- 主报告：`output/research/shared_1000_v01_matrix_20260804_20260901.json`
- 贪婪敏感性：`output/research/shared_1000_v01_greedy_sensitivity_20260804_20260901.json`
- 0.11目标日：`output/research/shared_1000_v011_r2_target_20260902.json`
- 0.11主报告：`output/research/shared_1000_v011_r2_fallback_matrix_20260804_20260901.json`
- 0.2主报告：`output/research/shared_1000_v02_matrix_20260804_20260901.json`
- 0.3目标段：`output/research/shared_1000_v03_target_period_20260902.json`
- 0.3主报告：`output/research/shared_1000_v03_matrix_20260804_20260901.json`
- 0.31目标日：`output/research/shared_1000_v031_target_20260902.json`
- 0.31主报告：`output/research/shared_1000_v031_matrix_20260804_20260901.json`
- 0.31目标日成交表：`output/research/shared_1000_v031_fills_20260902.csv`
- 0.3/0.31逐日对照：`output/research/shared_1000_v03_v031_daily_comparison_20260804_20260901.csv`
- 0.12目标日：`output/research/shared_1000_v012_target_20260902.json`
- 0.12主报告：`output/research/shared_1000_v012_matrix_20260804_20260901.json`
- 0.11/0.12逐日对照：`output/research/shared_1000_v011_v012_daily_comparison_20260804_20260901.csv`
- 0.13目标日：`output/research/shared_1000_v013_target_20260902.json`
- 0.13主报告：`output/research/shared_1000_v013_matrix_20260804_20260901.json`
- 0.11/0.12/0.13逐日对照：`output/research/shared_1000_v011_v012_v013_daily_comparison_20260804_20260901.csv`

`output/`按仓库规则是本地忽略目录，不复制到本归档、不强行加入Git。归档清单保存
各版本报告的精确路径、字节数和SHA256；若本地报告缺失，应从只读SQLite按清单命令
重放，不得用同名但哈希不同的文件冒充本次证据。
