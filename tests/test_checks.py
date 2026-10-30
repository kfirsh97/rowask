"""Automatic answer checks: they must catch the silent mistakes and stay quiet on correct queries."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import checks  # noqa: E402
import db  # noqa: E402
import dbprofile  # noqa: E402
import tools  # noqa: E402


@pytest.fixture(autouse=True)
def shop(tmp_path, monkeypatch):
    """orders (one row each), customers (unique id), payments (several per order), a column with gaps,
    and data that ends mid-March 2026."""
    path = tmp_path / "shop.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE customers (id INTEGER, name TEXT, city TEXT);
        CREATE TABLE orders (id INTEGER, customer_id INTEGER, order_date TEXT, total REAL, coupon REAL);
        CREATE TABLE payments (order_id INTEGER, method TEXT, amount REAL);
        INSERT INTO customers VALUES (1, 'Dana', 'Haifa'), (2, 'Omer', NULL), (3, 'Noa', 'Eilat');
        INSERT INTO orders VALUES (1, 1, '2025-11-02', 100, NULL), (2, 2, '2025-12-20', 200, 5),
                                  (3, 3, '2026-01-15', 300, NULL), (4, 1, '2026-03-14', 400, 10);
        INSERT INTO payments VALUES (1, 'card', 60), (1, 'cash', 40), (2, 'card', 200), (3, 'card', 300),
                                    (4, 'card', 200), (4, 'gift', 200);
    """)
    con.commit()
    con.close()
    for k in ("DB_USER", "DB_PASSWORD", "DB_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    for f in (dbprofile.load_profile, db._engine_for, checks._columns, checks._repeats, checks._empty):
        f.cache_clear()
    yield
    db._engine_for.cache_clear()


def run(sql):
    cols, rows = tools.run_query_all(sql)
    return {c["check"]: c for c in checks.run(sql, cols, rows)}


def test_join_that_repeats_rows_is_flagged():
    got = run("SELECT p.method, SUM(o.total) FROM orders o JOIN payments p ON p.order_id = o.id GROUP BY p.method")
    assert not got["joins"]["ok"] and "order_id is not unique in payments" in got["joins"]["text"]


def test_join_to_a_unique_key_passes():
    got = run("SELECT c.name, SUM(o.total) FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name")
    assert got["joins"]["ok"]


def test_empty_values_in_used_columns_are_flagged():
    got = run("SELECT SUM(coupon) FROM orders")
    assert not got["missing"]["ok"] and "coupon is empty in 50% of orders rows (2)" in got["missing"]["text"]
    got = run("SELECT c.city, COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.city")
    assert "city is empty" in got["missing"]["text"] and "group with no name" in got["missing"]["text"]


def test_incomplete_last_period_is_flagged():
    got = run("SELECT strftime('%Y-%m', order_date) AS month, SUM(total) FROM orders GROUP BY 1")
    assert not got["period"]["ok"] and "14 Mar 2026, so 2026-03 is not complete" in got["period"]["text"]
    got = run("SELECT CAST(strftime('%Y', order_date) AS INTEGER) AS year, SUM(total) FROM orders GROUP BY 1")
    assert "2026 is not complete" in got["period"]["text"]


def test_period_follows_the_query_filter():
    got = run("SELECT strftime('%Y-%m', order_date) AS month, SUM(total) FROM orders "
              "WHERE order_date < '2025-12-25' GROUP BY 1")
    assert "ends on 20 Dec 2025, so 2025-12" in got["period"]["text"]     # the date the query sees, not the table's
    assert "period" not in run("SELECT customer_id, SUM(total) FROM orders GROUP BY 1")


def test_a_query_that_cannot_be_read_gives_no_checks():
    assert checks.run("not sql at all") == [] and checks.warnings("SELECT 1") == []


def test_agent_sees_the_warnings_with_its_result():
    out = tools.run_query("SELECT SUM(coupon) AS c FROM orders")
    assert out["rows"] and any("coupon is empty" in w for w in out["warnings"])
    assert "warnings" not in tools.run_query("SELECT SUM(total) AS t FROM orders")


@pytest.mark.parametrize("answer, label, text", [
    ("Sales grew 21%.\nAssumptions: sales by order date.\n", "Assumptions:", "sales by order date."),
    ("המכירות עלו ב-21%.\n**הנחות:** לפי תאריך ההזמנה", "הנחות:", "לפי תאריך ההזמנה"),
    ("No assumptions line here.", None, None),
])
def test_assumptions_line_is_taken_out_of_the_answer(answer, label, text):
    rest, found = checks.split_assumptions(answer)
    assert (found or (None, None)) == (label, text)
    assert (label or "@@") not in rest
