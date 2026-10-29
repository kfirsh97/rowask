"""Safety tests for the query validator. No database needed:  pytest tests/"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
