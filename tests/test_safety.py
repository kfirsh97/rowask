"""Safety tests for the query validator. No database needed:  pytest tests/"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools  # noqa: E402
from tools import validate_query  # noqa: E402

ALLOWED = [
    "SELECT * FROM Sales",
    "select top 5 * from CARS order by Price desc",
    "WITH s AS (SELECT * FROM Sales) SELECT COUNT(*) FROM s",
    "SELECT * FROM Customers WHERE LastName = 'Drop'",          # keyword inside a string is fine
    "SELECT * FROM Customers WHERE Note = 'it''s an update'",    # escaped quote inside a string
    "SELECT COUNT(*) FROM Sales;",                               # one trailing semicolon is fine
    "SELECT Updated_At FROM X",                                  # 'update' as part of a word is fine
]

BLOCKED = [
    "",
    "DELETE FROM Sales",
    "DROP TABLE Sales",
    "UPDATE CARS SET Price = 0",
    "SELECT * FROM Sales; DROP TABLE Sales",
    "SELECT * INTO backup_sales FROM Sales",
    "EXEC xp_cmdshell 'dir'",
    "SELECT 1 /* hidden */ ; DELETE FROM Sales",
    "  -- comment\n  TRUNCATE TABLE Sales",
    "WITH x AS (SELECT 1 AS a) DELETE FROM Sales",
    "SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'SELECT 1')",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql):
    assert validate_query(sql) is None


@pytest.mark.parametrize("sql", BLOCKED)
def test_blocked(sql):
    assert validate_query(sql) is not None


@pytest.mark.parametrize("sql, dialect", [
    ("SELECT '--'; COMMIT; DELETE FROM Sales", "postgresql"),        # a comment marker inside a string
    ("SELECT 'a\\'', 1; DROP TABLE t", "mysql"),                   # MySQL's backslash-escaped quote
    ("SELECT 1 /*! ; DROP TABLE t */", "mysql"),                    # MySQL runs /*! ... */
    ("SELECT 1e'\\''; DELETE FROM t --'", "postgresql"),             # PostgreSQL 14 and older read an E'' string
    ("SELECT 1 /* /* */ ' */; DELETE FROM t", "postgresql"),        # nested comments
    ("SELECT 1 /* /* */ ' */; DELETE FROM t", "mssql"),
    ("SELECT ARRAY[1][1]; DELETE FROM t", "postgresql"),
])
def test_tricks_that_hide_a_second_statement_are_blocked(sql, dialect, monkeypatch):
    monkeypatch.setattr(tools.db, "dialect", lambda: dialect)
    assert validate_query(sql) is not None


def test_names_and_strings_that_look_like_keywords_are_fine(monkeypatch):
    monkeypatch.setattr(tools.db, "dialect", lambda: "mssql")
    assert validate_query("SELECT [Delete Date], N'drop' FROM t -- update later") is None


@pytest.mark.parametrize("sql, dialect", [
    ("SELECT \"pg_read_file\"('/etc/passwd')", "postgresql"),       # a quoted function name is still that function
    ("SELECT 1 /*M! INTO OUTFILE '/tmp/x' */", "mysql"),            # MariaDB runs /*M! ... */
    ("SELECT 1 WAITFOR DELAY '00:00:29'", "mssql"),                 # T-SQL: a second statement needs no semicolon
    ("SELECT 1 KILL 55", "mssql"),
    ("SELECT 1 DBCC FREEPROCCACHE", "mssql"),
    ("SELECT 1 BEGIN TRAN SELECT * FROM t WITH (TABLOCKX, HOLDLOCK)", "mssql"),
    ("SELECT 1", "no-such-database"),                               # unknown dialect: refuse, don't guess
])
def test_second_review_bypasses_are_blocked(sql, dialect, monkeypatch):
    monkeypatch.setattr(tools.db, "dialect", lambda: dialect)
    assert validate_query(sql) is not None


def test_quoted_names_that_are_not_called_stay_fine(monkeypatch):
    monkeypatch.setattr(tools.db, "dialect", lambda: "postgresql")
    assert validate_query('SELECT "Delete Date", "pg_read_file" FROM t') is None


@pytest.mark.parametrize("sql, dialect", [
    ("SELECT U&\"!0070g_read_file\"('/etc/passwd') UESCAPE '!'", "postgresql"),   # unicode-escaped function name
    ("SELECT 1 SELECT 2", "mssql"),                                                # a second SELECT in the batch
    ("SELECT (1) SELECT 2", "mssql"),
    ("SELECT * FROM OPENQUERY(srv, 'SELECT 1')", "mssql"),                         # linked servers
])
def test_third_review_bypasses_are_blocked(sql, dialect, monkeypatch):
    monkeypatch.setattr(tools.db, "dialect", lambda: dialect)
    assert validate_query(sql) is not None


@pytest.mark.parametrize("sql, dialect", [
    ("SELECT [Open], [Close], save, Return FROM prices", "mssql"),                 # column names, not statements
    ("WITH \"Sales Update\"(x) AS (SELECT 1) SELECT x FROM \"Sales Update\"", "postgresql"),
    ("SELECT 1 UNION ALL SELECT 2", "mssql"),
    ("WITH c AS (SELECT 1 AS a) SELECT a FROM c", "mssql"),
])
def test_ordinary_queries_are_not_refused(sql, dialect, monkeypatch):
    monkeypatch.setattr(tools.db, "dialect", lambda: dialect)
    assert validate_query(sql) is None
