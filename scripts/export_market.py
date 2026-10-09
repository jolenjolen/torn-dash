#!/usr/bin/env python3
"""Export the shared market history (no personal data, no keys) to a standalone file.

    python3 scripts/export_market.py                 # writes seed/market.db
    python3 scripts/export_market.py /tmp/market.db

Anyone running their own copy can put that file at seed/market.db (or point SEED_DB at
it). An empty server imports it on first start, so it begins with your price history.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402

SHARED = ("items", "item_snapshots", "item_prices", "listings")


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else config.ROOT / "seed" / "market.db")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(out)
    for table in SHARED:
        sql = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not sql:
            continue
        dst.execute(sql[0])
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if rows:
            dst.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
        print(f"{table}: {len(rows):,} rows")
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    src.close()
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB). Contains market data only.")


if __name__ == "__main__":
    main()
