from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path


SOURCE_PATHS = (
    "docs/主观做市策略手册.md",
    "docs/做市策略V0.1.md",
    "docs/做市模型版本记录.md",
)
SUMMARY_PATH = "docs/策略会话启动摘要.md"
FINGERPRINT_PATTERN = re.compile(
    r"^\s*- `(?P<path>[^`]+)`: `(?P<digest>[0-9a-fA-F]{64}|PENDING)`\s*$",
    re.MULTILINE,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def current_hashes(root: Path) -> dict[str, str]:
    return {relative: sha256(root / relative) for relative in SOURCE_PATHS}


def stored_hashes(summary: Path) -> dict[str, str]:
    if not summary.exists():
        return {}
    text = summary.read_text(encoding="utf-8")
    return {
        match.group("path"): match.group("digest").lower()
        for match in FINGERPRINT_PATTERN.finditer(text)
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether the compact strategy-session bootstrap matches its sources."
    )
    parser.add_argument(
        "--print-hashes",
        action="store_true",
        help="Print current source hashes for maintaining the bootstrap summary.",
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = repository_root()
    actual = current_hashes(root)
    if args.print_hashes:
        for relative in SOURCE_PATHS:
            print(f"- `{relative}`: `{actual[relative]}`")
        return 0

    summary = root / SUMMARY_PATH
    expected = stored_hashes(summary)
    stale = [
        relative
        for relative in SOURCE_PATHS
        if expected.get(relative) != actual[relative]
    ]
    if not summary.exists():
        print(f"STATUS=stale summary_missing={SUMMARY_PATH}")
    elif stale:
        print("STATUS=stale")
        for relative in stale:
            stored = expected.get(relative, "missing")
            print(
                f"changed={relative} stored={stored} current={actual[relative]}"
            )
    else:
        print("STATUS=current")
        print(f"summary={SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
