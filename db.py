"""Database connection for the SQL agent - any database SQLAlchemy can reach.

Which database (first match wins):
  0. a file uploaded on the web page - for that browser session only (SESSION_URL, set by app.py)
  1. DATABASE_URL in .env, e.g.
       postgresql://agent_reader:password@localhost:5432/shop
       mssql+pymssql://agent_reader:password@localhost:1433/Porsche
       sqlite:///data/porsche.sqlite          (a path relative to the agent folder, or an absolute one)
  2. DB_USER / DB_PASSWORD / DB_HOST / DB_PORT / DB_NAME - the original SQL Server settings, still supported
  3. the database_url in the active profile (profiles/<DB_PROFILE>.toml)

Read-only, three ways: the agent's own validator (tools.py) lets through only single SELECTs; SQLite files are
opened read-only and PostgreSQL sessions start read-only; and for any server you connect a read-only user.
Every query is stopped after QUERY_TIMEOUT_S seconds.
"""
import contextvars
import os
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

load_dotenv()

HERE = Path(__file__).parent
QUERY_TIMEOUT_S = 30
LOGIN_TIMEOUT_S = 10

# Default driver for each database type, so a plain "postgresql://..." or "mssql://..." URL just works.
DRIVERS = {"postgresql": "postgresql+psycopg", "postgres": "postgresql+psycopg",
           "mssql": "mssql+pymssql", "mysql": "mysql+pymysql", "mariadb": "mariadb+pymysql"}

# What the model needs to know to write correct SQL for each database type.
DIALECT_HINTS = {
    "mssql": "Microsoft SQL Server (T-SQL). Use TOP n, not LIMIT. Year of a date: YEAR(col). Today: GETDATE().",
    "postgresql": "PostgreSQL. Use LIMIT n. Year of a date: EXTRACT(YEAR FROM col). Today: CURRENT_DATE. "
                  "Text comparison is case-sensitive - use ILIKE for case-insensitive matching. "
                  "Integer division truncates - cast to numeric for averages and ratios.",
    "sqlite": "SQLite. Use LIMIT n. Dates are stored as 'YYYY-MM-DD' text: year = CAST(strftime('%Y', col) AS INTEGER), "
              "today = date('now'). Integer division truncates - multiply by 1.0 for averages and ratios.",
    "mysql": "MySQL. Use LIMIT n. Year of a date: YEAR(col). Today: CURDATE(). Quote identifiers with backticks.",
    "mariadb": "MariaDB. Use LIMIT n. Year of a date: YEAR(col). Today: CURDATE(). Quote identifiers with backticks.",
}


# Set by the web page for one browser session (an uploaded file), so two people never see each other's data.
# A context variable follows the code into the agent's background thread when the thread is started with
# contextvars.copy_context().run - see app.py.
SESSION_URL = contextvars.ContextVar("session_database_url", default=None)


class SetupError(RuntimeError):
    """A configuration problem the user can fix (shown as a plain message, not a stack trace)."""


def database_url() -> str:
    if SESSION_URL.get():
        return SESSION_URL.get()
    url = os.environ.get("DATABASE_URL", "").strip().strip("'\"")
    if url:
        return url
    from dbprofile import load_profile
    profile = load_profile()
    if profile["name"] != "porsche" and profile.get("database_url"):
        return profile["database_url"]          # DB_PROFILE=superstore means the Superstore file, even with DB_* set
    if os.environ.get("DB_USER"):              # DB_* is the Porsche database on SQL Server (SETUP.md)
        user = quote_plus(os.environ["DB_USER"])
        password = quote_plus(os.environ.get("DB_PASSWORD", "").strip().strip("'\""))
        host = os.environ.get("DB_HOST", "localhost")
        port = os.environ.get("DB_PORT", "1433")
        name = os.environ.get("DB_NAME", "Porsche")
        return f"mssql+pymssql://{user}:{password}@{host}:{port}/{name}"
    url = profile.get("database_url", "")
    if url:
        return url
    raise SetupError("No database configured. Set DATABASE_URL in .env (see SETUP.md), "
                     "or pick a profile that has a database_url.")


def _sqlite_path(url) -> Path:
    path = Path(url.database or "")
    if not path.is_absolute():
        path = HERE / path
    if not path.exists():
        hint = " Build it with: python setup/build_sqlite.py" if path.name == "porsche.sqlite" else ""
        raise SetupError(f"SQLite file not found: {path}.{hint}")
    return path


# SQLite has no query timeout, but it can call us back while a query runs: we stop it at its deadline.
_sqlite_deadlines = {}


def _sqlite_connect(path: Path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)   # read-only at file level
    _sqlite_deadlines[id(conn)] = float("inf")
    conn.set_progress_handler(lambda: int(time.monotonic() > _sqlite_deadlines.get(id(conn), float("inf"))), 10_000)
    return conn


@lru_cache(maxsize=None)
def _engine_for(url_text: str):
    url = make_url(url_text)
    backend = url.get_backend_name()
    if url.drivername in DRIVERS:
        url = url.set(drivername=DRIVERS[url.drivername])
    if backend == "sqlite":
        path = _sqlite_path(url)
        return create_engine("sqlite://", creator=lambda: _sqlite_connect(path))
    connect_args = {}
    if backend == "postgresql":
        connect_args = {"connect_timeout": LOGIN_TIMEOUT_S,
                        "options": f"-c statement_timeout={QUERY_TIMEOUT_S * 1000} -c default_transaction_read_only=on"}
    elif backend == "mssql":
        connect_args = {"login_timeout": LOGIN_TIMEOUT_S, "timeout": QUERY_TIMEOUT_S}
    elif backend in ("mysql", "mariadb"):
        connect_args = {"connect_timeout": LOGIN_TIMEOUT_S, "read_timeout": QUERY_TIMEOUT_S}
    try:
        return create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    except ModuleNotFoundError as e:
        raise SetupError(f"The driver for {backend} is not installed ({e.name}). Run: pip install {e.name}")


def engine():
    return _engine_for(database_url())


def dialect() -> str:
    """'mssql', 'postgresql', 'sqlite', 'mysql', ..."""
    return engine().dialect.name


def dialect_hint() -> str:
    return DIALECT_HINTS.get(dialect(), f"{dialect()}. Use only syntax this database supports.")


def describe() -> str:
    """Where we are connected, with the password hidden - safe to print or show in the UI."""
    if SESSION_URL.get():
        return "your uploaded files (this browser session only)"
    url = make_url(database_url())
    if url.get_backend_name() == "sqlite":
        return f"SQLite file {Path(url.database).name}"
    return f"{dialect()} database {url.database} on {url.host}"


def fetch(sql: str, max_rows: int):
    """Run one statement on a fresh connection; return (column names, up to max_rows rows as tuples).
    Uses the driver directly, so '%' in LIKE patterns and ':' in text are never read as parameters."""
    raw = engine().raw_connection()
    try:
        inner = raw.driver_connection
        if isinstance(inner, sqlite3.Connection):
            _sqlite_deadlines[id(inner)] = time.monotonic() + QUERY_TIMEOUT_S
        cur = raw.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows) if cur.description else []
        return cols, [tuple(r) for r in rows]
    finally:
        try:
            raw.rollback()          # never keep anything open, even after a read
        except Exception:
            pass
        raw.close()


def connect():
    """A SQLAlchemy connection (for the setup check and schema reading)."""
    return engine().connect()
