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
    "openquery", "openxml",                                              # SQL Server: linked servers, XML docs
    "external_query", "export",                                          # BigQuery: other databases, EXPORT DATA
]

_schema_cache = {}


# ---------------------------------------------------------------- safety

# SQL Server runs a whole batch: a second statement needs no semicolon ("SELECT 1 WAITFOR DELAY ..."), so the words
# that start one are refused too, as are lock hints that could hold a table.
TSQL_FORBIDDEN = ["waitfor", "kill", "dbcc", "begin", "commit", "rollback", "save", "declare", "set", "use", "print",
                  "raiserror", "throw", "checkpoint", "reconfigure", "bulk", "readtext", "writetext", "updatetext",
                  "open", "close", "fetch", "deallocate", "goto", "return", "while",
                  "tablockx", "tablock", "xlock", "updlock", "holdlock", "paglock"]


def _sql_dialect():
    """The sqlglot dialect of the connected database, for reading a query exactly as the server will - or None when it
    can't be told, and then validate_query refuses (a generic reader would not see what this server sees)."""
    try:
        from drill import DIALECTS
        return DIALECTS.get(db.dialect())
    except Exception:
        return None


def _tsql_batch(sql: str, words: str, tokens):
    """SQL Server runs a whole batch, and a second statement needs no semicolon ("SELECT 1 WAITFOR DELAY ...").
    A query sqlglot reads as exactly one SELECT is fine - so a column named Open, Save or Return is not refused. One it
    can't read is checked for the words that start another statement, and for table lock hints."""
    import sqlglot
    from sqlglot import exp
    try:
        parsed = [x for x in sqlglot.parse(sql, read="tsql") if x is not None]
    except Exception:
        parsed = None
    if parsed is not None:
        if len(parsed) == 1 and isinstance(parsed[0], exp.Query):
            return None
        return "Only one statement is allowed."
    for w in TSQL_FORBIDDEN:
        if re.search(rf"\b{w}\b", words):
            return f"Forbidden keyword: {w.upper()}"
    depth, starts = 0, 0                      # "SELECT 1 SELECT 2": a SELECT at the top level that no UNION joins
    for i, t in enumerate(tokens):
        kind = t.token_type.name
        depth += kind == "L_PAREN"
        depth -= kind == "R_PAREN"
        if depth == 0 and kind == "SELECT" and (i == 0 or tokens[i - 1].token_type.name not in
                                                 ("UNION", "ALL", "INTERSECT", "EXCEPT", "DISTINCT")):
            starts += 1
    return "Only one statement is allowed." if starts > 1 else None


def validate_query(sql: str):
    """Return None if the query is allowed, otherwise a short reason why not."""
    if not sql or not sql.strip():
        return "Empty query."
    dialect = _sql_dialect()
    if dialect is None:
        return "Could not tell which kind of database this is, so the query was not run."
    if dialect == "postgres" and re.search(r"\bU&[\"']|\bUESCAPE\b", sql, re.I):
        return "Unicode-escaped names and strings are not allowed."   # U&"!0070g_read_file" UESCAPE '!'
    if "\\" in sql and dialect in ("postgres", "mysql"):
        # Backslashes are read differently by server versions and settings (MySQL's escapes, PostgreSQL's E'' strings,
        # and before version 15 even 1e'\\'' became one): refused rather than guessed at. A SELECT rarely needs one.
        return "Backslashes are not allowed in queries on this database."
    try:
        from sqlglot.dialects.dialect import Dialect
        # One left-to-right pass, by this database's own rules: strings, quoted names and comments are each recognised
        # once (a '--' inside a string is text; MySQL's '\\'' is an escaped quote; nested /* */ in PostgreSQL).
        tokens = Dialect.get_or_raise(dialect).tokenize(sql)
    except Exception:
        return "Could not read the query."
    if any(c.lstrip().upper().startswith(("!", "+", "M!")) for t in tokens for c in t.comments):
        return "Executable comments are not allowed."          # MySQL /*! */, MariaDB /*M! */, optimizer hints /*+ */
    while tokens and tokens[-1].token_type.name == "SEMICOLON":
        tokens = tokens[:-1]
    if not tokens:
        return "Empty query."
    if any(t.token_type.name == "SEMICOLON" for t in tokens):
        return "Only one statement is allowed."
    if tokens[0].text.lower() not in ("select", "with"):
        return "Only SELECT queries are allowed."
    # Keywords count outside strings and quoted names (a customer named 'Drop', a column "Delete Date" are fine) -
    # but a quoted name used as a function is that function: PostgreSQL runs "pg_read_file"(...) like pg_read_file().
    called = {t.text.lower() for i, t in enumerate(tokens[:-1])
              if t.token_type.name == "IDENTIFIER" and tokens[i + 1].token_type.name == "L_PAREN"}
    for name in called:
        if name in FORBIDDEN or name.startswith(("xp_", "sp_")):
            return f"Forbidden function: {name}"
    words = " ".join("''" if t.token_type.name.endswith("STRING") else
                     '"x"' if t.token_type.name == "IDENTIFIER" else t.text for t in tokens).lower()
    for w in FORBIDDEN:
        pattern = rf"\b{re.escape(w)}" if w.endswith("_") else rf"\b{re.escape(w)}\b"
        if re.search(pattern, words):
            return f"Forbidden keyword: {w.upper()}"
    if dialect == "tsql":
        return _tsql_batch(sql, words, tokens)
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
    key = (db.database_url(), load_profile()["name"], notes, tuple(sorted(t.lower() for t in tables)) if tables else None)
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
    if notes and not tables:                      # once per conversation, with the full schema or the table list
        import metrics
        ms, _ = metrics.checked(load_profile())
        if ms:
            lines += ["", metrics.describe(ms)]
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
    if "bytes billed" in msg:                           # BigQuery refused the scan before running it
        return (f"BigQuery refused the query: it would scan more than {db.BIGQUERY_MAX_BYTES:,} bytes. Select fewer "
                "columns, or filter on a date column.")
    msg = re.split(r"\n\s*(Location:|Job ID:)", msg)[0]     # BigQuery appends its job details
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
    out_rows = [{c: _jsonable(v) for c, v in zip(cols, r)} for r in rows[:MAX_ROWS]]
    out = {"columns": cols, "rows": out_rows, "row_count": len(out_rows), "truncated": truncated}
    import checks                     # silent-mistake checks: repeated rows from a join, empty values, a partial period
    found = checks.warnings(sql, cols, rows[:MAX_ROWS] if not truncated else [])
    if found:
        out["warnings"] = found
    return out
