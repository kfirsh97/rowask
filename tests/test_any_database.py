"""The agent's tools on real databases: the SQLite copy of Porsche, the Chinook music store,
and (when TEST_POSTGRES_URL is set) PostgreSQL. No API key needed - these test the database side only."""
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent  # noqa: E402
import db  # noqa: E402
import dbprofile  # noqa: E402
import tools  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
POSTGRES = os.environ.get("TEST_POSTGRES_URL")       # e.g. postgresql://postgres@localhost:5433/chinook


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Every test starts from a clean configuration: no .env database settings, no cached engines."""
    for k in ("DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_PROFILE", "SCHEMA_NOTES"):
        monkeypatch.delenv(k, raising=False)
    dbprofile.load_profile.cache_clear()
    db._engine_for.cache_clear()
    tools._schema_cache.clear()
    yield
    dbprofile.load_profile.cache_clear()
    db._engine_for.cache_clear()
    tools._schema_cache.clear()


def use(monkeypatch, profile=None, url=None):
    if profile:
        monkeypatch.setenv("DB_PROFILE", profile)
    if url:
        monkeypatch.setenv("DATABASE_URL", url)


# ------------------------------------------------------------------ which database
def test_porsche_sqlite_is_the_zero_setup_default():
    assert db.dialect() == "sqlite"
    assert db.describe() == "SQLite file porsche.sqlite"


def test_database_url_wins_then_sql_server_settings_then_profile(monkeypatch):
    monkeypatch.setenv("DB_USER", "agent_reader")
    monkeypatch.setenv("DB_PASSWORD", "Reader!Passw0rd")
    assert db.database_url() == "mssql+pymssql://agent_reader:Reader%21Passw0rd@localhost:1433/Porsche"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/chinook.sqlite")
    assert db.database_url() == "sqlite:///data/chinook.sqlite"


def test_missing_sqlite_file_is_a_clear_setup_error(monkeypatch):
    use(monkeypatch, url="sqlite:///data/nope.sqlite")
    with pytest.raises(db.SetupError, match="not found"):
        db.engine()


def test_profiles(monkeypatch):
    assert dbprofile.load_profile("generic")["notes"] == ""
    with pytest.raises(FileNotFoundError, match="Available: chinook, porsche, generic"):
        dbprofile.load_profile("shop")
    bad = DATA.parent / "profiles" / "_bad_test.toml"
    bad.write_text('titel = "typo"\n', encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="unknown setting"):
            dbprofile.load_profile(str(bad))
    finally:
        bad.unlink()


# ------------------------------------------------------------------ schema
def test_porsche_schema_has_tables_links_and_notes(monkeypatch):
    s = tools.get_schema()
    assert s.count("\n- ") >= 18 and "- Sales(SaleID integer" in s
    assert "Sales.Seller_ID -> Sellers.Seller_ID" in s
    assert "COLUMN NOTES" in s and "US dollars" in s
    monkeypatch.setenv("SCHEMA_NOTES", "off")
    assert "COLUMN NOTES" not in tools.get_schema()


def test_a_database_it_was_never_built_for(monkeypatch):
    use(monkeypatch, profile="chinook")
    s = tools.get_schema()
    assert "- Track(TrackId integer" in s and "InvoiceLine.TrackId -> Track.TrackId" in s
    r = tools.run_query("SELECT ar.Name, COUNT(*) n FROM Album al JOIN Artist ar ON ar.ArtistId = al.ArtistId "
                        "GROUP BY ar.Name ORDER BY n DESC LIMIT 1")
    assert r["rows"] == [{"Name": "Iron Maiden", "n": 21}]


def test_large_database_lists_tables_first_then_details(monkeypatch, tmp_path):
    path = tmp_path / "big.sqlite"
    con = sqlite3.connect(path)
    for i in range(tools.LARGE_SCHEMA + 5):
        con.execute(f"CREATE TABLE t{i} (id INTEGER PRIMARY KEY, name TEXT, parent INTEGER REFERENCES t0(id))")
    con.close()
    use(monkeypatch, profile="generic", url=f"sqlite:///{path}")
    overview = tools.get_schema()
    assert "LARGE DATABASE" in overview and "- t7 (3 columns)" in overview and "name text" not in overview
    detail = tools.get_schema(["T7", "missing_table"])          # names are matched case-insensitively
    assert "- t7(id integer, name text, parent integer)" in detail and "NOT FOUND: missing_table" in detail
    assert agent.call_tool("get_schema", {"tables": ["t3"]})["schema"].startswith("TABLES:\n- t3(")


# ------------------------------------------------------------------ queries and safety
def test_queries_rows_and_errors():
    r = tools.run_query("SELECT Title FROM CARS WHERE Title LIKE '%Turbo%' ORDER BY Title LIMIT 60")
    assert r["row_count"] == tools.MAX_ROWS and r["truncated"] is True
    assert tools.run_query("SELECT * FROM Salez") == {"error": "no such table: Salez"}
    assert tools.run_query("DELETE FROM Sales")["error"].startswith("Query rejected")


def test_sqlite_file_is_opened_read_only_even_past_the_validator():
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.fetch("CREATE TABLE hack (x INTEGER)", 1)


def test_slow_query_is_stopped(monkeypatch):
    monkeypatch.setattr(db, "QUERY_TIMEOUT_S", 0.3)
    started = time.monotonic()
    r = tools.run_query("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n")
    assert "stopped after" in r["error"] and time.monotonic() - started < 5


@pytest.mark.parametrize("sql", ["PRAGMA writable_schema = 1", "SELECT 1; ATTACH 'x.db' AS x",
                                 "SELECT * FROM x WHERE 1 = 1 AND pg_sleep(5) IS NULL",
                                 "SELECT pg_read_file('/etc/passwd')", "COPY invoice TO '/tmp/x'"])
def test_validator_blocks_other_databases_dangerous_commands(sql):
    assert tools.validate_query(sql) is not None


# ------------------------------------------------------------------ the prompt follows the database
def test_prompt_names_the_business_and_the_dialect(monkeypatch):
    p = agent.system_prompt()
    assert "Porsche car-sales business" in p and "strftime" in p and "TOP" not in p
    use(monkeypatch, profile="chinook")
    assert "digital music store" in agent.system_prompt()


# ------------------------------------------------------------------ PostgreSQL (needs a server)
pg = pytest.mark.skipif(not POSTGRES, reason="set TEST_POSTGRES_URL to run the PostgreSQL tests")


@pg
def test_postgres_schema_and_queries(monkeypatch):
    use(monkeypatch, profile="generic", url=POSTGRES)
    assert db.dialect() == "postgresql" and "LIMIT" in agent.system_prompt()
    assert "- invoice_line(" in tools.get_schema()
    assert tools.run_query("SELECT name FROM artist WHERE name ILIKE '%queen%'")["rows"] == [{"name": "Queen"}]
    assert tools.run_query("SELECT 'a:b %s' AS t")["rows"] == [{"t": "a:b %s"}]   # no parameter parsing


@pg
def test_postgres_session_is_read_only_even_for_a_powerful_user(monkeypatch):
    use(monkeypatch, profile="generic", url=POSTGRES)
    with pytest.raises(Exception, match="read-only transaction"):
        db.fetch("CREATE TABLE hack (x int)", 1)


@pg
def test_postgres_slow_query_is_stopped(monkeypatch):
    monkeypatch.setattr(db, "QUERY_TIMEOUT_S", 1)
    use(monkeypatch, profile="generic", url=POSTGRES)
    with pytest.raises(Exception, match="statement timeout"):
        db.fetch("SELECT pg_sleep(3)", 1)


def test_full_agent_loop_on_a_new_database(monkeypatch, tmp_path):
    """The whole loop - schema, a failed query, the fix, the answer - with a scripted model and the real
    Chinook data. Proves nothing in the loop is tied to Porsche."""
    from test_agent_loop import fake_client
    use(monkeypatch, profile="chinook")
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    good = ("SELECT g.Name, COUNT(*) AS sold FROM InvoiceLine il JOIN Track t ON t.TrackId = il.TrackId "
            "JOIN Genre g ON g.GenreId = t.GenreId GROUP BY g.Name ORDER BY sold DESC LIMIT 1")
    client = fake_client([
        ([("get_schema", {})], None),
        ([("run_query", {"sql": "SELECT TOP 1 Name FROM Genre"})], None),       # T-SQL on SQLite: error
        ([("run_query", {"sql": good})], None),
        ([], f"Rock is the best-selling genre.\n```sql\n{good}\n```"),
    ])
    r = agent.ask("Which genre sells the most?", client=client, verbose=False)
    assert [s["result"].split(":")[0] for s in r["steps"][1:]] == ["ERROR", "1 rows"]
    assert tools.run_query(r["final_sql"])["rows"] == [{"Name": "Rock", "sold": 835}]


def test_sql_server_url_builds_without_connecting(monkeypatch):
    monkeypatch.setenv("DB_USER", "agent_reader")
    monkeypatch.setenv("DB_PASSWORD", "x")
    assert db.dialect() == "mssql" and "TOP n" in db.dialect_hint()
