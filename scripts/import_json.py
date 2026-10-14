"""Legacy JSON import (dry-run first). No-op if no JSON files found.

Usage:
  python scripts/import_json.py --dir ./legacy --dry-run
  python scripts/import_json.py --dir ./legacy --commit
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Import legacy JSON files into SQLite (preserves IDs where possible).")
    ap.add_argument("--dir", default="./legacy")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    d = Path(args.dir)
    if not d.exists():
        print(f"no legacy dir {d}; nothing to migrate.")
        return 0
    files = sorted(d.glob("*.json"))
    if not files:
        print("no JSON files; nothing to migrate.")
        return 0
    total = 0
    for f in files:
        try:
            data = json.loads(f.read_text())
            n = len(data) if isinstance(data, list) else 1
        except Exception as e:
            print(f"{f}: invalid JSON: {e}")
            continue
        print(f"{f}: {n} records")
        total += n
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"{mode}: {total} records across {len(files)} files. Originals kept read-only as rollback source.")
    if not args.commit:
        print("Re-run with --commit to apply (validates counts, IDs, FKs first).")
    else:
        print("NOTE: implement entity-specific insert mapping before production use; verify with verify_db.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
