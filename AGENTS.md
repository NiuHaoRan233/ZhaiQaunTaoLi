# Project Guide

This repository records MiniQMT market data and runs paper-only M0 strategies.

## Safety

- Keep the market connection read-only. Do not import or call `xttrader`.
- Never send broker orders from this project.
- `config.toml`, `data/`, `logs/`, and `backups/` are local runtime state and must not be committed.
- Back up SQLite with `zhaiquant backup`; do not copy a live WAL database directly.

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
