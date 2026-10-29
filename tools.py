"""The agent's two tools: read the database structure, and run a read-only query.

Safety has two layers:
  1. The database user can only read (see setup/01_create_reader.sql).
  2. Before any query reaches the database, validate_query() rejects anything
     that is not a single SELECT statement.
"""
import datetime
import decimal
import os
import re

from db import connect

MAX_ROWS = 50          # rows sent back to the model; enough to answer, small enough to stay cheap
FORBIDDEN = [
    "insert", "update", "delete", "merge", "drop", "alter", "create", "truncate",
    "exec", "execute", "grant", "revoke", "deny", "backup", "restore", "shutdown",
    "into",            # blocks SELECT ... INTO, which creates a table
    "openrowset", "opendatasource", "xp_", "sp_",
]

_schema_cache = {}

# Business meaning of columns that the raw schema cannot tell the model.
# Stage 5 measures how much these notes improve accuracy (SCHEMA_NOTES=off turns them off).
SCHEMA_NOTES = """COLUMN NOTES (business meaning):
- Sales.Sold_Price: final sale price of the car, in US dollars. Sales.Sold_Date: date of the sale.
- Sales.Views / Followers / Comments: engagement counts on the car's sale listing.
- Sellers.Dealer_Or_PrivateParty: 'Dealer' or 'Private Party'. Dealer details are in Dealer_Sellers,
  private sellers in Private_Seller (both share Seller_ID with Sellers).
- DealersWithInsurances: which insurance companies (Insurance.InsuranceID) each dealer works with.
- CARS.YEAR: the car's model year. CARS.Title: listing title, includes the model name (e.g. 'Turbo', 'Carrera').
- CARS.Mileage: odometer reading in miles.
- CARS.OWNERS: free text. 'CARFAX 1-Owner vehicle' = one owner; 'NPrevious owners' (no space, e.g. '3Previous owners')
  = N previous owners; 'Types of owners: ...' lists owner types.
- Policy.Insurance_Type: 'Mandatory', 'Comprehensive' or 'Third Party'.
- Services.Service_Cost: cost of one service visit in US dollars (0-3000).
- Payments.Amount: payment amount in US dollars. Payments.Seller_Commission: the seller's commission in US dollars.
- ZipCodes.State: full US state name in English (e.g. 'California', 'New York').
- Customers.FOLLOW / COMMENTS: how many listings the customer follows / comments they wrote.
- Ages: a car's age in years = YEAR(GETDATE()) - CARS.YEAR."""


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


def get_schema() -> str:
    """Describe every table: its columns with types, how the tables are linked,
    and (unless SCHEMA_NOTES=off) the business meaning of key columns."""
    notes = notes_enabled()
    if notes in _schema_cache:
        return _schema_cache[notes]
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
    if notes:
        lines += ["", SCHEMA_NOTES]
    _schema_cache[notes] = "\n".join(lines)
    return _schema_cache[notes]


def _jsonable(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def run_query_all(sql: str, max_rows: int = 100_000):
    """For evaluation only: run a validated SELECT and return ALL rows as plain tuples (no 50-row cap)."""
    reason = validate_query(sql)
    if reason:
        raise ValueError(f"Query rejected: {reason}")
    conn = connect()
    try:
        cur = conn.cursor(as_dict=False)
        cur.execute(sql)
        rows = cur.fetchmany(max_rows)
        cols = [d[0] for d in cur.description] if cur.description else []
    finally:
        conn.close()
    return cols, [tuple(_jsonable(v) for v in r) for r in rows]


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
