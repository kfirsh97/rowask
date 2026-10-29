"""Build a SQLite copy of the Porsche database from the SQL Server scripts in ../sql.

SQLite is a whole database in one file - no server, no Docker - so this copy lets anyone try the
agent in a minute, and lets the tests run the agent's tools against real data.

Run (from the agent folder):  python setup/build_sqlite.py
Output: data/porsche.sqlite

What has to change between the two dialects:
  - CREATE DATABASE / USE / GO are SQL Server batch commands -> dropped
  - GETDATE() defaults -> CURRENT_DATE
  - trailing commas before ")" in two CREATE TABLEs (SQL Server tolerates them, SQLite does not)
  - table constraints (FOREIGN KEY ...) written between columns -> moved after the last column
  - IDENTITY(seed, step) keys -> filled with the same values SQL Server would generate
"""
import re
import sqlite3
import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
SQL = AGENT.parent / "sql"
OUT = AGENT / "data" / "porsche.sqlite"


def to_sqlite(script: str):
    """Translate a T-SQL script; return (sqlite_script, {table: (id_column, seed, step)})."""
    identity = {}

    def ident(m):
        identity[current_table[0]] = (m.group(1), int(m.group(3)), int(m.group(4)))
        return f"{m.group(1)} INTEGER PRIMARY KEY"

    out, current_table = [], [None]
    for line in script.splitlines():
        s = line.strip()
        if re.match(r"(?i)^(GO|USE\s|CREATE\s+DATABASE\s)", s):
            continue
        t = re.match(r"(?i)^CREATE\s+TABLE\s+(\w+)", s)
        if t:
            current_table[0] = t.group(1)
        line = re.sub(r"(?i)(\w+)\s+(BIGINT|INT)\s+IDENTITY\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s+PRIMARY\s+KEY(\s+NOT\s+NULL)?",
                      ident, line)
        line = re.sub(r"(?i)GETDATE\(\)", "CURRENT_DATE", line)
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r",\s*\n(\s*)\)", r"\n\1)", text)          # trailing comma before ")"
    text = re.sub(r"(?is)(CREATE\s+TABLE\s+\w+\s*\()(.*?)(\n\s*\);)", _constraints_last, text)
    return text, identity


def _split_top_level(body):
    """Split a CREATE TABLE body on the commas that are not inside parentheses."""
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur]


def _constraints_last(m):
    """SQLite wants every column first and table constraints after them; T-SQL allows any order."""
    items = [p.strip() for p in _split_top_level(m.group(2)) if p.strip()]
    is_constraint = re.compile(r"(?i)^(FOREIGN\s+KEY|PRIMARY\s+KEY|UNIQUE\s*\(|CHECK\s*\(|CONSTRAINT\s)")
    cols = [i for i in items if not is_constraint.match(i)]
    cons = [i for i in items if is_constraint.match(i)]
    return m.group(1) + "\n    " + ",\n    ".join(cols + cons) + m.group(3)


def main():
    OUT.parent.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    schema, identity = to_sqlite((SQL / "01_schema.sql").read_text(encoding="utf-8"))
    data, _ = to_sqlite((SQL / "02_seed_data.sql").read_text(encoding="utf-8"))
    db.executescript(schema)
    db.executescript("BEGIN;\n" + data + "\nCOMMIT;")
    # SQLite numbered these rows 1, 2, 3... in insertion order; give them SQL Server's IDENTITY values.
    for table, (col, seed, step) in identity.items():
        db.execute(f"UPDATE {table} SET {col} = {seed} + ({col} - 1) * {step}")
    db.commit()
    counts = {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
              for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
    db.execute("VACUUM")
    db.close()
    tmp.replace(OUT)
    print(f"Wrote {OUT.relative_to(AGENT)}: {len(counts)} tables, {sum(counts.values()):,} rows")
    for t, n in counts.items():
        print(f"  {t:<26} {n:>7,}")


if __name__ == "__main__":
    sys.exit(main())
