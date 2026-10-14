"""Restore backup tgz (DB + uploads). Usage: python scripts/restore.py --in ./backups/dts.tgz --confirm"""
from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--confirm", action="store_true", help="Required to actually overwrite")
    args = ap.parse_args()
    if not args.confirm:
        print("Refusing without --confirm (would overwrite DB + uploads).", file=sys.stderr)
        return 2
    s = get_settings()
    db_path = Path(s.database_url.split("sqlite:///")[-1]) if "sqlite" in s.database_url else Path("./defect_tracker.db")
    up = Path(s.upload_dir)
    inp = Path(args.inp)
    if not inp.exists():
        print(f"missing {inp}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as td:
        with tarfile.open(inp, "r:gz") as tar:
            # safe extract: only expected members
            for m in tar.getmembers():
                if m.name.startswith("/") or ".." in m.name:
                    print(f"unsafe member {m.name}", file=sys.stderr)
                    return 2
            tar.extractall(td)
        tdp = Path(td)
        snap = tdp / "defect_tracker.db"
        if snap.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snap, db_path)
        up_src = tdp / "uploads"
        if up_src.exists():
            if up.exists():
                shutil.rmtree(up)
            shutil.copytree(up_src, up)
    print(f"restored from {inp}; run `alembic upgrade head` and verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
