# Project Guide

This repository records MiniQMT market data and runs paper-only M0 strategies.

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

At the start of every new Agent session in this repository, read these files completely
before discussing, analyzing, or changing the strategy:

1. `docs/主观做市策略手册.md` — the long-term record of the user's reasoning,
   examples, corrections, and unified decision principles.
2. `docs/做市策略V0.1.md` — the current formal and implementable strategy specification.
3. `docs/做市模型版本记录.md` — the immutable model registry, current execution-branch
   assignments, ancestry, replay results, and promotion criteria.

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

## Safety

- Keep the market connection read-only. Do not import or call `xttrader`.
- Never send broker orders from this project.
- `config.toml`, `data/`, `logs/`, and `backups/` are local runtime state and must not be committed.
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
- Additional recording-only code: `132024.SH` by default.
- Run tests with `.\.venv\Scripts\python.exe -m unittest discover -v`.
- Run diagnostics with `.\.venv\Scripts\python.exe -m zhaiquant --config config.toml doctor`.

## Data Semantics

- `raw_ticks` preserves the received Level 1 snapshot and raw JSON.
- `tick_changes` is locally derived from cumulative fields and book changes.
- `inferred_side` is an estimate, not an exchange Level 2 aggressor flag.
- `snapshot_hash` intentionally excludes historical `tickvol`, which MiniQMT changes between identical history reads.
- M0 is evaluated only for the primary bond/stock pair. Extra watch codes are recorded but not traded.
