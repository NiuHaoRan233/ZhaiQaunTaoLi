"""Read-only live-ordering preflight for immutable shared v0.16.

Does not instantiate a paper account or change a live database/configuration.
The mocked parent isolates v0.16's admission guard, not its trading logic.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zhaiquant.shared_thousand_maker_v013_research import SharedThousandV013Allocator
from zhaiquant.shared_thousand_maker_v016_research import SharedThousandV016Allocator


def audit(database: Path, market_date: str) -> dict:
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        rows = connection.execute(
            """SELECT r.id,r.run_id,r.code,r.market_time,r.market_ts_ms,r.received_ts_ns
                 FROM raw_ticks r JOIN sessions s ON s.run_id=r.run_id
                WHERE r.market_date=? AND s.status!='backfill'
                  AND r.code IN ('132026.SH','132024.SH','600900.SH','600362.SH')
                ORDER BY r.id""", (market_date,),
        ).fetchall()
        watermarks = {}
        late = []
        for row in rows:
            previous = watermarks.get(row["run_id"])
            if previous and row["market_ts_ms"] < previous["market_ts_ms"]:
                late.append({
                    "earlier_received_highwater": dict(previous),
                    "later_received_older_snapshot": dict(row),
                    "market_time_regression_ms": previous["market_ts_ms"] - row["market_ts_ms"],
                })
            else:
                watermarks[row["run_id"]] = row

        reproductions = []
        for case in late:
            before = case["earlier_received_highwater"]
            after = case["later_received_older_snapshot"]
            # Seed exactly the last admitted event. No account/engine is needed:
            # the concrete v0.16 guard rejects before calling its parent.
            allocator = object.__new__(SharedThousandV016Allocator)
            allocator._last_event_key = (before["market_ts_ms"], before["id"])
            tick = SimpleNamespace(market_ts_ms=after["market_ts_ms"], tick_id=after["id"])
            with patch.object(SharedThousandV013Allocator, "on_replay_tick") as parent:
                try:
                    allocator.on_replay_tick(tick)
                except ValueError as exc:
                    message = str(exc)
                else:
                    raise AssertionError("expected immutable v0.16 to reject late event")
                assert not parent.called
            reproductions.append({"tick_id": after["id"], "error": message, "parent_called": False})
        return {
            "model_id": "maker_shared_1000_v0_16_candidate",
            "market_date": market_date,
            "input": "raw_ticks database insertion order, per session, non-backfill only",
            "ticks": len(rows),
            "late_events": late,
            "guard_reproductions": reproductions,
            "scope": "input guard only; not an economic replay or evidence of a live v0.16 failure",
            "conclusion": "live input contract unresolved" if late else "no regression found on this date",
            "live_database_config_and_processes_changed": False,
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/zhaiquant.sqlite3"))
    parser.add_argument("--date", default="2026-09-07")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite evidence")
    report = audit(args.database, args.date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"ticks": report["ticks"], "late_events": len(report["late_events"]),
                      "reproduced": len(report["guard_reproductions"]),
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
