"""The agent's two tools: read the database structure, and run a read-only query.

Safety has two layers:
  1. The database user can only read (see setup/01_create_reader.sql).
  2. Before any query reaches the database, validate_query() rejects anything
     that is not a single SELECT statement.
"""
import datetime
import decimal
import re

from db import connect

MAX_ROWS = 50          # rows sent back to the model; enough to answer, small enough to stay cheap
FORBIDDEN = [
    "insert", "update", "delete", "merge", "drop", "alter", "create", "truncate",
    "exec", "execute", "grant", "revoke", "deny", "backup", "restore", "shutdown",
    "into",            # blocks SELECT ... INTO, which creates a table
    "openrowset", "opendatasource", "xp_", "sp_",
]

_schema_cache = None


# ---------------------------------------------------------------- safety

def _strip_comments_and_strings(sql: str) -> str:
    """Remove comments and string literals, so keywords hidden inside them are ignored
    (a customer named 'Drop' must not be blocked) and keywords hidden in comments are caught."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"N?'(?:[^']|'')*'", "''", sql)
    return sql


def validate_query(sql: str):
    """Return None if the query is allowed, otherwise a short reason why not."""
    if not sql or not sql.strip():
        return "Empty query."
    clean = _strip_comments_and_strings(sql).strip().rstrip(";").strip()
    if ";" in clean:
        return "Only one statement is allowed."
    first = clean.split(None, 1)[0].lower()
    if first not in ("select", "with"):
        return "Only SELECT queries are allowed."
    lowered = clean.lower()
    for word in FORBIDDEN:
        pattern = rf"\b{re.escape(word)}" if word.endswith("_") else rf"\b{re.escape(word)}\b"
        if re.search(pattern, lowered):
            return f"Forbidden keyword: {word.upper()}"
    return None


# ---------------------------------------------------------------- tools

def get_schema() -> str:
    """Describe every table: its columns with types, and how the tables are linked."""
    global _schema_cache
    if _schema_cache:
        return _schema_cache
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """)
    tables = {}
    for r in cur.fetchall():
        tables.setdefault(r["TABLE_NAME"], []).append(f"{r['COLUMN_NAME']} {r['DATA_TYPE']}")

    cur.execute("""
        SELECT tp.name AS from_table, cp.name AS from_col,
               tr.name AS to_table,   cr.name AS to_col
        FROM sys.foreign_key_columns fk
        JOIN sys.tables  tp ON tp.object_id = fk.parent_object_id
        JOIN sys.columns cp ON cp.object_id = fk.parent_object_id     AND cp.column_id = fk.parent_column_id
        JOIN sys.tables  tr ON tr.object_id = fk.referenced_object_id
        JOIN sys.columns cr ON cr.object_id = fk.referenced_object_id AND cr.column_id = fk.referenced_column_id
        ORDER BY tp.name
    """)
    links = [f"{r['from_table']}.{r['from_col']} -> {r['to_table']}.{r['to_col']}" for r in cur.fetchall()]
    conn.close()

    lines = ["TABLES:"]
    lines += [f"- {t}({', '.join(cols)})" for t, cols in tables.items()]
    lines += ["", "FOREIGN KEYS:"] + [f"- {l}" for l in links]
    _schema_cache = "\n".join(lines)
    return _schema_cache


def _jsonable(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def run_query(sql: str) -> dict:
    """Run one read-only SELECT. Returns the rows, or an error message the agent can learn from."""
    reason = validate_query(sql)
    if reason:
        return {"error": f"Query rejected: {reason}"}
    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchmany(MAX_ROWS + 1)
        conn.close()
    except Exception as e:  # SQL errors go back to the agent so it can fix its query
        msg = str(e)
        msg = msg.split("DB-Lib error")[0] if "DB-Lib error" in msg else msg
        return {"error": msg[:500]}
    truncated = len(rows) > MAX_ROWS
    rows = [{k: _jsonable(v) for k, v in r.items()} for r in rows[:MAX_ROWS]]
    return {
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }
