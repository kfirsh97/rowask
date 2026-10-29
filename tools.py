"""The agent's two tools: read the database structure, and run a read-only query.

Works on any database SQLAlchemy can reach (see db.py). Safety has layers:
  1. Before any query reaches the database, validate_query() rejects anything
     that is not a single SELECT statement.
  2. The connection itself is read-only: a read-only database user (setup/01_create_reader.sql),
     SQLite files opened read-only, PostgreSQL sessions that start read-only.
  3. Every query is stopped after 30 seconds.
"""
import datetime
import decimal
import os
import re
import sqlite3

from sqlalchemy import inspect

import db
from dbprofile import load_profile

MAX_ROWS = 50          # rows sent back to the model; enough to answer, small enough to stay cheap
LARGE_SCHEMA = 40      # above this many tables, get_schema lists tables first and the model asks for details
FORBIDDEN = [
    "insert", "update", "delete", "merge", "drop", "alter", "create", "truncate",
    "exec", "execute", "grant", "revoke", "deny", "backup", "restore", "shutdown",
    "into",            # blocks SELECT ... INTO, which creates a table (and MySQL's INTO OUTFILE)
    "openrowset", "opendatasource", "xp_", "sp_",                        # SQL Server
    "attach", "detach", "pragma", "vacuum",                              # SQLite
    "copy", "call", "dblink", "lo_import", "lo_export",                  # PostgreSQL
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_sleep",
    "load_file",                                                         # MySQL
]

_schema_cache = {}


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

def notes_enabled() -> bool:
    return os.environ.get("SCHEMA_NOTES", "on").lower() not in ("off", "0", "false", "no")


def _type(t) -> str:
    try:
        text = str(t)
    except Exception:                    # some database-specific types cannot be printed generically
        text = t.__class__.__name__
    return text.split(" COLLATE")[0].lower()


def _describe(insp, name):
    cols = ", ".join(f"{c['name']} {_type(c['type'])}" for c in insp.get_columns(name))
    return f"- {name}({cols})"


def get_schema(tables=None) -> str:
    """Describe the database: tables (and views) with their columns and types, how they are linked,
    and (unless SCHEMA_NOTES=off) the business notes from the profile.
    On a large database, the first call lists the tables only; call again with `tables` for their columns."""
    notes = notes_enabled()
    key = (db.database_url(), notes, tuple(sorted(t.lower() for t in tables)) if tables else None)
    if key in _schema_cache:
        return _schema_cache[key]
    insp = inspect(db.engine())
    names = insp.get_table_names()
    views = insp.get_view_names()
    by_lower = {n.lower(): n for n in names + views}

    if tables:
        wanted = [by_lower[t.lower()] for t in tables if t.lower() in by_lower]
        missing = [t for t in tables if t.lower() not in by_lower]
        lines = ["TABLES:"] + [_describe(insp, n) for n in wanted]
        if missing:
            lines += ["", f"NOT FOUND: {', '.join(missing)}"]
        links = _links(insp, wanted)
    elif len(names) + len(views) > LARGE_SCHEMA:
        lines = [f"LARGE DATABASE: {len(names)} tables, {len(views)} views. Columns are not listed here - "
                 "call get_schema again with the names of the tables you need.", "", "TABLES:"]
        lines += [f"- {n} ({len(insp.get_columns(n))} columns)" for n in names]
        if views:
            lines += ["", "VIEWS:"] + [f"- {v}" for v in views]
        links = _links(insp, names)
    else:
        lines = ["TABLES:"] + [_describe(insp, n) for n in names]
        if views:
            lines += ["", "VIEWS:"] + [_describe(insp, v) for v in views]
        links = _links(insp, names)

    lines += ["", "FOREIGN KEYS:"] + [f"- {l}" for l in links]
    profile_notes = load_profile()["notes"].strip()
    if notes and profile_notes:
        lines += ["", "COLUMN NOTES (business meaning):", profile_notes]
    _schema_cache[key] = "\n".join(lines)
    return _schema_cache[key]


def _links(insp, names):
    links = []
    for t in names:
        for fk in insp.get_foreign_keys(t):
            for a, b in zip(fk["constrained_columns"], fk["referred_columns"]):
                links.append(f"{t}.{a} -> {fk['referred_table']}.{b}")
    return links


def _jsonable(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    return v


def _clean_error(e: Exception) -> str:
    if isinstance(e, sqlite3.OperationalError) and str(e) == "interrupted":
        return f"Query stopped after {db.QUERY_TIMEOUT_S} seconds - make it cheaper (filter earlier, aggregate, or LIMIT)."
    msg = str(getattr(e, "orig", None) or e)
    msg = msg.split("DB-Lib error")[0] if "DB-Lib error" in msg else msg
    return msg.strip()[:500]


def run_query_all(sql: str, max_rows: int = 100_000):
    """For evaluation only: run a validated SELECT and return ALL rows as plain tuples (no 50-row cap)."""
    reason = validate_query(sql)
    if reason:
        raise ValueError(f"Query rejected: {reason}")
    cols, rows = db.fetch(sql, max_rows)
    return cols, [tuple(_jsonable(v) for v in r) for r in rows]


def run_query(sql: str) -> dict:
    """Run one read-only SELECT. Returns the rows, or an error message the agent can learn from."""
    reason = validate_query(sql)
    if reason:
        return {"error": f"Query rejected: {reason}"}
    try:
        cols, rows = db.fetch(sql, MAX_ROWS + 1)
    except db.SetupError:
        raise                         # a setup problem is for the user, not for the model to retry
    except Exception as e:            # SQL errors go back to the agent so it can fix its query
        return {"error": _clean_error(e)}
    truncated = len(rows) > MAX_ROWS
    rows = [{c: _jsonable(v) for c, v in zip(cols, r)} for r in rows[:MAX_ROWS]]
    return {"columns": cols, "rows": rows, "row_count": len(rows), "truncated": truncated}
