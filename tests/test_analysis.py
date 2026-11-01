"""Prepared analyses: right answers on data whose answer is known, and clear refusals on data that can't support one."""
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent  # noqa: E402
import analysis  # noqa: E402
import db  # noqa: E402
import dbprofile  # noqa: E402


@pytest.fixture(autouse=True)
def known(tmp_path, monkeypatch):
    rng = np.random.default_rng(7)
    path = tmp_path / "known.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE yearly (year INTEGER, sales REAL)")
    con.executemany("INSERT INTO yearly VALUES (?, ?)", [(2015 + i, 100.0 * (i + 1)) for i in range(10)])
    con.execute("CREATE TABLE monthly (month TEXT, sales REAL)")
    con.executemany("INSERT INTO monthly VALUES (?, ?)",
                    [(f"{2022 + i // 12}-{i % 12 + 1:02d}", 1000 + 10 * i + (300 if i % 12 == 11 else 0))
                     for i in range(36)])                            # a trend, and every December 300 higher
    con.execute("CREATE TABLE orders (region TEXT, profit REAL, discount REAL, customer TEXT)")
    rows = [("A", float(v), 0.0, f"c{i}") for i, v in enumerate(rng.normal(100, 10, 200))]
    rows += [("B", float(v), 0.0, f"d{i}") for i, v in enumerate(rng.normal(130, 10, 200))]
    rows += [("C", float(v), 0.0, f"e{i}") for i, v in enumerate(rng.normal(100, 10, 200))]
    rows += [("A", 5000.0, 0.0, "whale")]
    con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", rows)
    con.execute("CREATE TABLE pairs (x REAL, y REAL)")
    con.executemany("INSERT INTO pairs VALUES (?, ?)", [(float(x), 2 * x + float(rng.normal(0, 1))) for x in range(50)])
    con.commit()
    con.close()
    for k in ("DB_USER", "DB_PASSWORD", "DB_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    dbprofile.load_profile.cache_clear(); db._engine_for.cache_clear()
    yield
    db._engine_for.cache_clear()


def test_a_straight_line_is_continued():
    r = analysis.run({"kind": "forecast", "sql": "SELECT year, sales FROM yearly", "x": "year", "y": "sales",
                      "periods": 2})
    assert [f["period"] for f in r["forecast"]] == ["2025", "2026"]
    assert abs(r["forecast"][0]["forecast"] - 1100) < 15 and r["forecast"][0]["low"] <= 1100 <= r["forecast"][0]["high"]


def test_monthly_seasonality_is_modelled_with_enough_history():
    r = analysis.run({"kind": "forecast", "sql": "SELECT month, sales FROM monthly", "x": "month", "y": "sales",
                      "periods": 12})
    assert "seasonality" in r["method"]
    dec = next(f for f in r["forecast"] if f["period"].endswith("-12"))
    nov = next(f for f in r["forecast"] if f["period"].endswith("-11"))
    assert dec["forecast"] - nov["forecast"] > 200                     # December's jump comes back


def test_a_real_difference_is_significant_and_no_difference_is_not():
    r = analysis.run({"kind": "compare_groups", "sql": "SELECT region, profit FROM orders WHERE region IN ('B','C')",
                      "group": "region", "value": "profit"})
    assert r["significant"] and r["tests"]["welch_t_test_p"] < 0.001 and "statistically significant" in r["summary"]
    r = analysis.run({"kind": "compare_groups", "sql": "SELECT region, profit FROM orders WHERE region = 'C' OR "
                      "(region = 'A' AND customer <> 'whale')", "group": "region", "value": "profit"})
    assert not r["significant"]


def test_three_groups_use_tests_for_many_groups():
    r = analysis.run({"kind": "compare_groups", "sql": "SELECT region, profit FROM orders WHERE customer <> 'whale'",
                      "group": "region", "value": "profit"})
    assert "kruskal_wallis_p" in r["tests"] and r["significant"]


def test_correlation_and_outliers():
    r = analysis.run({"kind": "correlation", "sql": "SELECT x, y FROM pairs", "x": "x", "y": "y"})
    assert r["pearson_r"] > 0.99 and "not that one causes" in r["summary"]
    r = analysis.run({"kind": "outliers", "sql": "SELECT customer, profit FROM orders WHERE region = 'A'",
                      "label": "customer", "value": "profit"})
    assert r["unusual"][0]["label"] == "whale" and r["unusual"][0]["direction"] == "high"


@pytest.mark.parametrize("args, message", [
    ({"kind": "compare_groups", "sql": "SELECT region, SUM(profit) p FROM orders GROUP BY region",
      "group": "region", "value": "p"}, "one row per observation"),            # totals per group: nothing to test
    ({"kind": "forecast", "sql": "SELECT region, SUM(profit) p FROM orders GROUP BY region", "x": "region",
      "y": "p"}, "one row per month"),
    ({"kind": "forecast", "sql": "SELECT year, sales FROM yearly WHERE year < 2018", "x": "year", "y": "sales"},
     "at least 4 years"),
    ({"kind": "forecast", "sql": "DELETE FROM yearly", "x": "year", "y": "sales"}, "Query rejected"),
    ({"kind": "regression", "sql": "SELECT 1"}, "Unknown analysis"),
    ({"kind": "correlation", "sql": "SELECT x, y FROM pairs", "x": "x", "y": "z"}, "no column 'z'"),
])
def test_data_that_cannot_support_the_analysis_is_refused_clearly(args, message):
    assert message in analysis.run(args)["error"]


def test_the_agent_can_call_it_and_the_step_reads_well():
    r = agent.call_tool("analyze", {"kind": "correlation", "sql": "SELECT x, y FROM pairs", "x": "x", "y": "y"})
    assert agent._summary(r).startswith("correlation: Strong positive relationship")
