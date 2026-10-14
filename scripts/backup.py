"""Backup DB + uploads into a single tgz. Usage: python scripts/backup.py --out ./backups/dts.tgz"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    s = get_settings()
    # resolve sqlite path
    db_path = Path(s.database_url.split("sqlite:///")[-1]) if "sqlite" in s.database_url else None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        if db_path and db_path.exists():
            # consistent snapshot via sqlite backup API
            snap = tdp / "defect_tracker.db"
            src = sqlite3.connect(str(db_path))
            dst = sqlite3.connect(str(snap))
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
        up = Path(s.upload_dir)
        with tarfile.open(out, "w:gz") as tar:
            snap_f = tdp / "defect_tracker.db"
            if snap_f.exists():
                tar.add(snap_f, arcname="defect_tracker.db")
            if up.exists():
                tar.add(up, arcname="uploads")
    print(f"backup written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
