# Project Guide

This repository records MiniQMT market data and runs paper-only M0 and
exchangeable-bond maker strategies. It also contains read-only TDX audit,
market-screen OCR, activity-scanning, and dashboard tools.

## Persistent Strategy Workflow

The ultimate objective is to make the system faithfully reproduce the user's trading
logic and reasoning process from the information that was causally available at each
moment. Strategy documents, implementation, replay, and evaluation should all serve this
goal. Do not optimize primarily for backtest profit, trade count, or matching a handful
of isolated examples when that would distort the user's underlying decision framework.
Prefer behavior that is explainable in the user's terms and generalizes consistently to
new order-book situations.

The user normally starts a fresh Agent session each day and continues explaining the
strategy. Repository documents, not chat history, are the durable cross-session memory.
The user should not need to repeat this workflow or restate previously recorded strategy
knowledge in every new session.

At the start of a new strategy-discussion session, use the repository skill
`.agents/skills/maker-strategy-session/SKILL.md`. Validate and read
`docs/策略会话启动摘要.md` completely before discussing, analyzing, or changing the
strategy. When its source fingerprints are current, do not reread all three long
strategy documents merely as a startup ritual; search and read the relevant original
sections as the user's case develops. If the summary is stale, follow the skill's
fallback and reconcile it with every changed source before continuing.

The compact summary is only a startup index. These three files remain authoritative:

1. `docs/主观做市策略手册.md` — the long-term record of the user's reasoning,
   examples, corrections, and unified decision principles.
2. `docs/做市策略V0.1.md` — the current formal and implementable strategy specification.
3. `docs/做市模型版本记录.md` — the immutable model registry, current execution-branch
   assignments, ancestry, replay results, and promotion criteria.

Whenever confirmed strategy knowledge or the paper matrix changes, update the startup
summary last and refresh its source fingerprints only after the authoritative documents,
implementation, configuration, and tests are consistent. Workflow-only changes do not
need a trading-model version.

During each strategy discussion:

- Treat the user's daily commentary as a partial, opportunistic audit rather than a
  complete annotation of every market event or simulated fill. The user may glance at
  the order book during the day or review selected moments later and will normally
  discuss whichever cases happen to stand out.
- Use that commentary for gap analysis. For every discussed case, inspect the surrounding
  causal market data and determine whether the model missed a correct action, made an
  incorrect action, already handled the case correctly, or exposed a broader principle.
- The user may not review most of the day's simulated trades. Silence about a trade is
  neither approval nor disapproval. Independently audit unmentioned behavior against the
  recorded principles and evidence.
- Fix confirmed omissions and mistakes, but preserve behavior that is already correct
  even when the user did not mention it. Do not narrow the strategy to only the examples
  covered in that day's conversation, and do not manufacture a code change when the
  existing behavior already matches the intended principle.
- Listen for new principles, examples, counterexamples, corrections, and unresolved
  questions. Compare them with the existing handbook instead of treating them in
  isolation.
- Use `docs/主观做市策略手册.md` as the durable source of truth for what the user has
  taught. Record confirmed new knowledge there during the session so a future Agent can
  continue without relying on the current conversation context.
- Distinguish exploratory comments from confirmed rules. If the intended rule is
  materially ambiguous, clarify it before encoding it in the specification or code.
- When a confirmed explanation changes the strategy, update the handbook first, then
  keep `docs/做市策略V0.1.md`, implementation, configuration, and tests consistent with
  it as applicable.
- Treat first-position and queued execution as separate model branches. A change to one
  branch does not authorize changing the other. Any decision change that affects orders,
  fills, inventory paths, or execution assumptions must receive a new registered model
  version; never silently mutate an existing version.
- Every paper account/day and simulated order must be traceable to a registered model ID.
  Preserve prior versions and compare candidates against their actual saved baseline.
- Do not silently erase earlier reasoning. When new guidance conflicts with an older
  principle or example, record the conflict, mark superseded conclusions clearly, and
  preserve the reason for the final resolution.
- Generalize from the user's reasoning rather than adding a one-off patch for a single
  historical case. Add representative positive and negative tests and use causal replay
  when implementation changes are made.
- At the end of the work, summarize what strategy knowledge was added, what rules or code
  changed, and which questions remain open.

## Repository and Documentation Routing

- `README.md` is the public project entry point. Keep its setup commands, current paper
  matrix, directory map, and local-data boundary aligned with the repository.
- `docs/README.md` is the documentation index. Update it when a durable document or a
  major subtool is added, renamed, or retired.
- `docs/每日债券做市交易PDF制作规范.md` owns the daily maker-paper PDF data formulas,
  approved typography and layout, filename rules, generation procedure, and mandatory
  render/font/data QA. Read it before creating or revising a PDF under `output/pdf/`;
  do not improvise a new font system from visual memory.
- `docs/实时采集与模拟盘运行手册.md` owns daily startup, monitoring, shutdown, and
  backup operations. `docs/Windows换机迁移教程.md` owns migration instructions.
- `策略自我迭代优化/` contains curated candidate evidence, frozen manifests, and
  research summaries. It is not a second implementation tree; executable truth remains
  under `src/zhaiquant/` and `tests/`.
- `成交委托数据截图保存/AGENTS.md` owns the daily TDX screenshot/OCR workflow. The
  screenshots and structured OCR outputs are local evidence and are deliberately not
  committed.
- `债券活跃度观察/`, `实盘决策看板/`, and `行情屏幕高速读取/` are independent read-only
  tools. Preserve their local `.gitignore` rules and keep their READMEs current.
- Before answering a bond-activity request, read `债券活跃度观察/AGENTS.md` for the
  user's persistent output requirements: always include both EB benchmarks and show
  scores for every bond in the reported observation list, including follow-up lists.

## Current Maker Paper Matrix

- Production baselines remain `maker_priority_v1_1`, `maker_queue_v1_0`, and
  `maker_windfall_v1_0`, but the ordinary priority 1.1 and queue 1.0 baselines are
  inactive in the forward real-time paper matrix. Preserve their registrations and
  historical ledgers. Windfall is also inactive in the current forward matrix.
- The current persisted real-time comparison order is
  `maker_priority_v1_37_candidate`, `maker_priority_v1_50_candidate`,
  `maker_priority_v2_52_candidate_r2`, `maker_priority_v2_63_candidate`,
  `maker_priority_v2_70_candidate_r2`, `maker_priority_v2_71_candidate`,
  `maker_shared_1000_v0_1_candidate`, `maker_shared_1000_v0_13_candidate`, then
  `maker_shared_1000_v0_16_candidate_r2`, then `maker_dadao_v0_1_candidate_r2`.
- Publicly present the active ordinary first-position models as 1.37, 1.50,
  2.52, 2.63, 2.70, and 2.71. The 2.70 identity is the validated r2, directly based on
  immutable 2.63 and added by the user on 2026-09-07 without replacing any of
  the previous six models. The initial 2.70 and 2.69 remain offline research.
  The 2.63 candidate is a direct child of immutable 2.6 r3 and
  keeps its trading behavior while receiving its own model identity and ledger.
  Also present the three active shared-capital models as thousand-bond
  first-position 0.1, 0.13, and 0.16 (live revision). The 0.16 r2 is a direct
  child of immutable 0.16 with arrival-ordered execution and a private input
  journal; never replay its forward account by resorting raw market times.
  Each has its own runtime and 1,000-bond capital
  slot; only the two bonds inside the same model share that slot. Former 2.5,
  2.51, 2.6, the other shared-capital 0.x versions, queue, and Windfall forward
  accounts are inactive in the current matrix, but all registrations and
  historical ledgers remain immutable together with 2.52 ancestors and every
  earlier version.
- On 2026-09-11 the user added ordinary 2.71 after 2.70, retaining all nine
  previous models (ten total). ID `maker_priority_v2_71_candidate` is the
  user-corrected former offline numbering 2.73, directly based on immutable 2.63.
  Its profile is unchanged; ordinary same-day recovery replays saved ticks.
- A candidate entering the real-time paper matrix is not a production promotion. Do not
  describe it as deployed or current production, and do not merge its account, fills,
  inventory, or PnL with another model.
- The Dadao model is publicly “大道至简0.1（实时修订）”, a direct child of
  `probe_top_compare_20260908_v1_switch_v013`. It has its own zero-base 1,000-bond
  shared slot and simple buy-one/sell-one loop with the archived v013 selector.
  Its runtime is `src/zhaiquant/dadao_maker_live.py`; native ordinary trading
  decisions must not run under this identity. Restore only its own arrival journal,
  retaining the original 0.1 reports and all other models independently.
- Keep `config.example.toml`, the model registry, the formal specification, runtime model
  registration, and tests consistent whenever this matrix changes.

## Safety

- Keep the market connection read-only. Do not import or call `xttrader`.
- Never send broker orders from this project.
- `config.toml`, `data/`, `logs/`, and `backups/` are local runtime state and must not be committed.
- `output/`, `成交委托数据截图保存/` contents, screen-reader runtime files, activity
  scanner reports/snapshots, and `之前的工作的一些内容/` are also local-only. Do not
  force-add them. The only committed file inside the TDX capture archive is its
  `AGENTS.md` workflow.
- Back up SQLite with `zhaiquant backup`; do not copy a live WAL database directly.

## Daily Maker Account Inputs

- Treat the ordinary maker paper account's opening state as two explicit,
  user-supplied variables: (1) opening base inventory in bonds and (2) additional
  buying capacity in bonds, backed by enough paper cash at the day's actual prices.
- The current defaults are 1,000 bonds of opening base inventory plus enough paper
  cash to buy another 1,000 bonds. This implies a normal inventory range of 0 to
  2,000 bonds unless the user supplies different values.
- Quantity units are strict: one exchangeable-bond hand is 10 bonds. Therefore
  1,000 bonds means 100 hands, not one hand. Keep configuration, database fields,
  console output, tests, strategy documents, and discussion explicit about whether
  a quantity is in bonds or hands.
- Do not silently replace the additional 1,000-bond buying-capacity input with a
  stale fixed cash amount that can afford fewer than 1,000 bonds at the current
  market price. If cash is the implementation unit, derive or validate it against
  the intended additional bond capacity.

## Maker Dashboard Refresh Window

- Both continuous maker-console windows refresh only on weekdays from 09:25
  inclusive to 15:30 exclusive in Asia/Shanghai time. Outside that window they
  must sleep without polling SQLite or rebuilding the trader-thinking snapshot.
- Keep the last rendered screen visible after 15:30 and automatically resume at
  09:25 on the next weekday. When no explicit historical date was requested,
  roll the dashboard market date forward when the new refresh window opens.
- `maker-console --once` remains an explicit diagnostic command and may render
  once outside the continuous refresh window.

## Local Environment

- MiniQMT default port: `58611`.
- Primary pair: `132026.SH` and `600900.SH`.
- The maker paper engine also evaluates `132024.SH` using its mapped underlying
  `600362.SH`. M0 still evaluates only the primary `132026.SH`/`600900.SH` pair.
- Run tests with `.\.venv\Scripts\python.exe -m unittest discover -v`.
- Run the standalone read-only tool tests with:
  `.\.venv\Scripts\python.exe -m unittest discover -s 债券活跃度观察 -p "test_*.py" -v`,
  `.\.venv\Scripts\python.exe -m unittest discover -s 实盘决策看板\tests -p "test_*.py" -v`,
  and `.\.venv\Scripts\python.exe -m unittest discover -s 行情屏幕高速读取\tests -p "test_*.py" -v`.
- Run diagnostics with `.\.venv\Scripts\python.exe -m zhaiquant --config config.toml doctor`.

## Data Semantics

- `raw_ticks` preserves the received Level 1 snapshot and raw JSON.
- `tick_changes` is locally derived from cumulative fields and book changes.
- `inferred_side` is an estimate, not an exchange Level 2 aggressor flag.
- `snapshot_hash` intentionally excludes historical `tickvol`, which MiniQMT changes between identical history reads.
- M0 is evaluated only for the primary bond/stock pair. Extra watch codes are recorded but not traded.
