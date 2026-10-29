"""Load a SQL script - a database dump or a course project's CREATE TABLE + INSERT files - into SQLite.

The script may be written for SQL Server (T-SQL), MySQL / MariaDB, PostgreSQL or SQLite. Each statement is run
as it is when SQLite accepts it (fast - most INSERTs), and otherwise translated with sqlglot. On top of that:
  - server-only statements are skipped and counted: CREATE DATABASE, USE, GO, SET, logins and users, GRANT,
    procedures, functions, triggers, psql commands (\\c ...)
  - T-SQL table constraints written between columns are moved after them, as SQLite requires
  - N'...' strings (T-SQL) become plain strings; PostgreSQL SERIAL becomes an auto-numbered INTEGER
  - IDENTITY(seed, step) keys get the values the original server would have given them
  - foreign keys added later with ALTER TABLE ... ADD CONSTRAINT (common in dumps) are built into the tables,
    so the agent still sees how the tables link - the thing that lets it JOIN a normalized schema correctly
The script runs in a fresh database file with a guard (an SQLite authorizer) that refuses anything able to
reach outside that file: ATTACH, DETACH, PRAGMA, VACUUM INTO, loading extensions.
"""
import re
import sqlite3
from collections import Counter

import sqlglot

SKIP = re.compile(
    r"^\s*(GO\b|USE\b|SET\b|PRINT\b|DECLARE\b|EXEC(UTE)?\b|GRANT\b|REVOKE\b|DENY\b|BEGIN\s+TRAN|BEGIN\s*$|COMMIT\b|"
    r"ROLLBACK\b|START\s+TRANSACTION|LOCK\s+TABLES|UNLOCK\s+TABLES|\\|"
    r"(CREATE|DROP|ALTER)\s+(DATABASE|SCHEMA|LOGIN|USER|ROLE|PROCEDURE|PROC|FUNCTION|TRIGGER|EXTENSION|SEQUENCE|TYPE)\b|"
    r"ALTER\s+(DATABASE|ROLE|USER|DEFAULT\s+PRIVILEGES|SEQUENCE)\b|ALTER\s+TABLE\s+\S+\s+OWNER\b|COMMENT\s+ON\b|"
    r"SELECT\s+pg_catalog\.|VACUUM\b|ATTACH\b|DETACH\b|PRAGMA\b)",
    re.I)
SKIP_LABEL = [(r"^(CREATE|DROP|ALTER)\s+DATABASE|^USE\b", "database selection (CREATE DATABASE, USE)"),
              (r"^GO\b", "GO batch separators"), (r"^SET\b", "session settings (SET)"),
              (r"LOGIN|USER|ROLE|GRANT|REVOKE|DENY|PRIVILEGES|OWNER", "logins, users and permissions"),
              (r"PROCEDURE|PROC\b|FUNCTION|TRIGGER|DECLARE|EXEC|PRINT", "procedures, functions, triggers"),
              (r"^\\", "psql commands"), (r"VACUUM|ATTACH|DETACH|PRAGMA", "file-level commands (blocked)")]
ADD_FK = re.compile(r"^\s*ALTER\s+TABLE\s+(?P<table>[\w\"`\[\].]+)\s+ADD\s+(CONSTRAINT\s+[\w\"`\[\]]+\s+)?"
                    r"(?P<fk>FOREIGN\s+KEY\s*\(.*)$", re.I | re.S)
IDENTITY = re.compile(r"(?P<col>[\w\"`\[\]]+)\s+\w+\s+IDENTITY\s*\(\s*(?P<seed>\d+)\s*,\s*(?P<step>\d+)\s*\)", re.I)
CREATE = re.compile(r"^\s*CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?(?P<name>[\w\"`\[\].]+)", re.I)


def guess_dialect(text: str) -> str:
    t = text[:200_000]
    score = Counter()
    score["tsql"] += len(re.findall(r"^\s*GO\s*$", t, re.M | re.I)) * 3 + len(re.findall(r"\bIDENTITY\s*\(|\bNVARCHAR\b|\[\w+\]|\bGETDATE\(", t, re.I))
    score["mysql"] += len(re.findall(r"`\w+`|\bAUTO_INCREMENT\b|\bENGINE\s*=", t, re.I)) * 2
    score["postgres"] += len(re.findall(r"\bSERIAL\b|::\w+|^\\c\b|\bCOPY\b.*FROM stdin|GENERATED\s+(ALWAYS|BY DEFAULT)", t, re.I | re.M)) * 2
    best, n = score.most_common(1)[0] if score else ("", 0)
    return best if n else "sqlite"


def split(text: str) -> list:
    """Statements of a script: split on ';' and on GO lines, never inside strings, quoted names or comments."""
    out, cur, i, n = [], [], 0, len(text)
    while i < n:
        ch = text[i]
        two = text[i:i + 2]
        if two == "--" or ch == "#" and (i == 0 or text[i - 1] == "\n"):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if two == "/*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            cur.append(" ")
            continue
        if ch in "'\"`[":
            close = "]" if ch == "[" else ch
            j = i + 1
            while j < n:
                if text[j] == "\\" and ch == "'" and close == "'":
                    j += 2
                    continue
                if text[j] == close:
                    if j + 1 < n and text[j + 1] == close and close != "]":
                        j += 2
                        continue
                    break
                j += 1
            cur.append(text[i:j + 1])
            i = j + 1
            continue
        if ch == ";":
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        if ch == "\n":
            line_end = text.find("\n", i + 1)
            line = text[i + 1: n if line_end < 0 else line_end]
            if re.fullmatch(r"\s*GO\s*(\d+)?\s*", line, re.I) or line.startswith("\\"):
                out.append("".join(cur))
                cur = []
                i = n if line_end < 0 else line_end
                continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


def _split_top_level(body: str) -> list:
    parts, depth, cur, quote = [], 0, "", None
    for ch in body:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur]


def constraints_last(sql: str) -> str:
    """CREATE TABLE with table constraints between columns -> every column first (SQLite's rule)."""
    start, end = sql.find("("), sql.rfind(")")
    if start < 0 or end < start:
        return sql
    items = [p.strip() for p in _split_top_level(sql[start + 1:end]) if p.strip()]
    is_con = re.compile(r"(?i)^(CONSTRAINT\s|FOREIGN\s+KEY|PRIMARY\s+KEY|UNIQUE\s*\(|UNIQUE\s+KEY|CHECK\s*\()")
    cols = [p for p in items if not is_con.match(p)]
    cons = [p for p in items if is_con.match(p)]
    return sql[:start + 1] + ", ".join(cols + cons) + sql[end:]


def _prepare(stmt: str, dialect: str) -> str:
    stmt = re.sub(r"(?<![\w'])N'", "'", stmt)                       # T-SQL N'text' -> 'text'
    if dialect == "postgres":
        stmt = re.sub(r"\b(BIG|SMALL)?SERIAL\b", "INTEGER", stmt, flags=re.I)
    return stmt


def _guard(action, arg1, arg2, db, source):
    """SQLite authorizer: the script may build tables in its own file, nothing more."""
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and arg2 and arg2.lower() in ("load_extension", "writefile", "readfile"):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _bare(name: str) -> str:
    return name.split(".")[-1].strip('"`[]')


def run_script(con: sqlite3.Connection, text: str) -> dict:
    """Run a SQL script into con. Returns a summary: dialect, counts, skipped kinds, errors, foreign keys added."""
    dialect = guess_dialect(text)
    con.isolation_level = None                     # we manage transactions ourselves (BEGIN / SAVEPOINT / COMMIT)
    con.set_authorizer(_guard)
    done, skipped, errors, fks, identities = 0, Counter(), [], [], {}
    con.execute("BEGIN")
    for stmt in split(text):
        if SKIP.match(stmt):
            label = next((l for p, l in SKIP_LABEL if re.search(p, stmt.strip(), re.I)), "other server-only statements")
            skipped[label] += 1
            continue
        m = ADD_FK.match(stmt)
        if m:                                                         # rebuilt into the table at the end
            fks.append((_bare(m.group("table")), m.group("fk").strip()))
            continue
        c = CREATE.match(stmt)
        if c:
            for idm in IDENTITY.finditer(stmt):
                seed, step = int(idm.group("seed")), int(idm.group("step"))
                if (seed, step) != (1, 1):
                    identities[_bare(c.group("name"))] = (_bare(idm.group("col")), seed, step)
        stmt = _prepare(stmt, dialect)
        try:
            translated = sqlglot.transpile(stmt, read=None if dialect == "sqlite" else dialect, write="sqlite",
                                           unsupported_level=sqlglot.ErrorLevel.IGNORE)
        except Exception:
            translated = []
        # CREATE TABLE: the translation first - SQLite would accept "INT IDENTITY(1,1)" as a type name as it is,
        # and silently lose the auto-numbering. Everything else: as written first (fast, and exact).
        candidates = translated + [stmt] if c else [stmt] + translated
        if c:
            candidates += [constraints_last(x) for x in candidates[:]]
        last_error = None
        for sql in candidates:
            try:
                con.execute("SAVEPOINT s")
                con.execute(sql)
                con.execute("RELEASE s")
                done += 1
                break
            except sqlite3.Error as e:
                con.execute("ROLLBACK TO s")
                con.execute("RELEASE s")
                last_error = e
        else:
            errors.append(f"{' '.join(stmt.split())[:90]}... -> {last_error}")
    for table, (col, seed, step) in identities.items():            # IDENTITY(seed, step) values
        try:
            con.execute(f'UPDATE "{table}" SET "{col}" = {seed} + ("{col}" - 1) * {step}')
        except sqlite3.Error:
            pass
    con.execute("COMMIT")
    added = _add_foreign_keys(con, fks, dialect)
    con.set_authorizer(None)
    return {"dialect": dialect, "statements": done, "skipped": dict(skipped), "errors": errors,
            "foreign_keys": added, "identities": len(identities)}


def _add_foreign_keys(con, fks, dialect) -> int:
    """SQLite cannot ALTER TABLE ... ADD CONSTRAINT: rebuild each table with its foreign keys written in."""
    by_table = {}
    for table, fk in fks:
        fk = re.sub(r"\s+ON\s+(DELETE|UPDATE)\s+NO\s+ACTION", "", fk, flags=re.I)
        try:
            fk = sqlglot.transpile(f"CREATE TABLE _x (_c INT, {fk})", read=None if dialect == "sqlite" else dialect,
                                   write="sqlite")[0]
            fk = fk[fk.index(",") + 1: fk.rindex(")")].strip()
        except Exception:
            pass
        by_table.setdefault(table, []).append(fk)
    added = 0
    for table, keys in by_table.items():
        row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND lower(name)=lower(?)", (table,)).fetchone()
        if not row:
            continue
        create = row[0]
        name = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name)=lower(?)", (table,)).fetchone()[0]
        new = create[:create.rindex(")")] + ", " + ", ".join(keys) + ")"
        new = re.sub(r"^\s*CREATE\s+TABLE\s+(\"[^\"]+\"|\S+)", 'CREATE TABLE "_rebuild"', new, count=1, flags=re.I)
        try:
            con.execute("BEGIN")
            con.execute(new)
            con.execute(f'INSERT INTO "_rebuild" SELECT * FROM "{name}"')
            con.execute(f'DROP TABLE "{name}"')
            con.execute(f'ALTER TABLE "_rebuild" RENAME TO "{name}"')
            con.execute("COMMIT")
            added += len(keys)
        except sqlite3.Error:
            con.execute("ROLLBACK")
    return added
