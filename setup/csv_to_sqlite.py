"""Turn one or more CSV files into a SQLite database the agent can query.

Each CSV becomes one table (named after the file). Column types are inferred from the data:
whole numbers -> INTEGER, decimals -> REAL, everything else -> TEXT. A column of numbers with
leading zeros (ZIP codes, phone numbers, IDs like 00123) stays TEXT, so the zeros are not lost.
Dates are kept as they appear in the file - SQLite reads 'YYYY-MM-DD' best.

Run (from the agent folder):
  python setup/csv_to_sqlite.py data/superstore.csv
  python setup/csv_to_sqlite.py orders.csv customers.csv --out data/shop.sqlite
Then point the agent at it in .env:   DATABASE_URL=sqlite:///data/superstore.sqlite
"""
import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

INT = re.compile(r"^-?(0|[1-9]\d*)$")
LEADING_ZERO = re.compile(r"^-?0\d")
REAL = re.compile(r"^-?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def read_rows(path: Path):
    """Read a CSV as UTF-8 (with or without BOM), falling back to Latin-1 for files saved by old Excel."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with open(path, newline="", encoding=encoding) as f:
                rows = list(csv.reader(f))
            return rows, encoding
        except UnicodeDecodeError:
            continue


def column_type(values) -> str:
    present = [v for v in values if v != ""]
    if not present:
        return "TEXT"
    if any(LEADING_ZERO.match(v) for v in present):     # 01810, 007: a code, not a number
        return "TEXT"
    if all(INT.match(v) for v in present):
        return "INTEGER"
    if all(REAL.match(v) for v in present):
        return "REAL"
    return "TEXT"


def clean_name(name: str) -> str:
    name = re.sub(r"\W+", "_", name.strip()).strip("_")
    return name if name and not name[0].isdigit() else f"c_{name}"


def convert(value: str, kind: str):
    if value == "":
        return None
    return int(value) if kind == "INTEGER" else float(value) if kind == "REAL" else value


def load(db: sqlite3.Connection, path: Path, table: str = None) -> tuple:
    rows, encoding = read_rows(path)
    if not rows:
        raise SystemExit(f"{path} is empty")
    header, data = [clean_name(h) for h in rows[0]], rows[1:]
    if len(set(header)) != len(header):
        raise SystemExit(f"{path}: two columns have the same name after cleaning: {header}")
    width = len(header)
    bad = [i + 2 for i, r in enumerate(data) if len(r) != width]
    if bad:
        raise SystemExit(f"{path}: {len(bad)} line(s) have the wrong number of columns, e.g. line {bad[0]}")
    kinds = [column_type([r[i] for r in data]) for i in range(width)]
    table = table or clean_name(path.stem)
    cols = ", ".join(f'"{h}" {k}' for h, k in zip(header, kinds))
    db.execute(f'DROP TABLE IF EXISTS "{table}"')
    db.execute(f'CREATE TABLE "{table}" ({cols})')
    marks = ", ".join("?" * width)
    db.executemany(f'INSERT INTO "{table}" VALUES ({marks})',
                   ([convert(v, k) for v, k in zip(r, kinds)] for r in data))
    return table, len(data), dict(zip(header, kinds)), encoding


def main(argv=None):
    ap = argparse.ArgumentParser(description="Load CSV files into a SQLite database for the SQL agent.")
    ap.add_argument("csv", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, help="output .sqlite file (default: next to the first CSV)")
    args = ap.parse_args(argv)
    out = args.out or args.csv[0].with_suffix(".sqlite")
    db = sqlite3.connect(out)
    for path in args.csv:
        table, n, kinds, encoding = load(db, path)
        print(f"{path.name} -> table {table}: {n:,} rows ({encoding})")
        print("   " + ", ".join(f"{c} {k.lower()}" for c, k in kinds.items()))
    db.commit()
    db.close()
    print(f"\nWrote {out}. In .env:  DATABASE_URL=sqlite:///{out.as_posix()}")


if __name__ == "__main__":
    sys.exit(main())
