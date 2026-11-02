"""The daily metrics report: right numbers for the day, safe to re-run, and nothing written when it can't be trusted."""
import csv
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily  # noqa: E402
import db  # noqa: E402
import dbprofile  # noqa: E402
import metrics  # noqa: E402

METRICS = [{"name": "Revenue", "definition": "d", "sql": "SUM(amount)", "table": "sales", "date": "day", "unit": "USD"},
           {"name": "Sales", "definition": "d", "sql": "COUNT(*)", "table": "sales", "date": "day"},
           {"name": "Big sales", "definition": "d", "sql": "COUNT(*)", "table": "sales", "filter": "amount >= 100"}]


@pytest.fixture(autouse=True)
def shop(tmp_path, monkeypatch):
    """60 days of sales: 10 a day for the first 30 days, then 20 a day; one sale of 100 on the last day."""
    path = tmp_path / "shop.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sales (day TEXT, amount REAL)")
    start = dt.date(2025, 1, 1)
    con.executemany("INSERT INTO sales VALUES (?, ?)",
                    [((start + dt.timedelta(days=i)).isoformat() + " 10:00:00", 10.0 if i < 30 else 20.0)
                     for i in range(60)] + [("2025-03-01 18:00:00", 100.0)])
    con.commit(); con.close()
    for k in ("DB_USER", "DB_PASSWORD", "DB_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    dbprofile.load_profile.cache_clear(); db._engine_for.cache_clear(); metrics._ok_cache.clear()
    yield
    dbprofile.load_profile.cache_clear(); db._engine_for.cache_clear()


def profile(ms=METRICS):
    return {**dbprofile.load_profile("generic"), "name": "shop", "metrics": ms}


def test_the_numbers_for_the_day_and_the_two_windows():
    r = daily.compute(profile(), "latest")
    assert r["day"] == dt.date(2025, 3, 1)
    rev, n, big = r["rows"]
    assert (rev["day"], rev["last_30_days"], rev["previous_30_days"]) == (120, 700, 300)
    assert rev["change_pct"] == pytest.approx(133.3) and rev["all_time"] == 1000
    assert (n["day"], n["last_30_days"], n["previous_30_days"], n["change_pct"]) == (2, 31, 30, 3.3)
    assert big["day"] is None and big["all_time"] == 1                       # no date column: all time only
    assert r["alerts"] == ["Revenue changed +133.3% against the 30 days before."]


def test_the_day_is_worked_out_not_written_in():
    assert daily.compute(profile(), "yesterday")["day"] == dt.date.today() - dt.timedelta(days=1)
    assert daily.compute(profile(), "2025-01-15")["rows"][0]["day"] == 10


def test_running_twice_replaces_the_day_instead_of_adding_it(tmp_path):
    path = tmp_path / "history.csv"
    for day in ("2025-02-01", "2025-02-02", "2025-02-01"):
        daily.save(daily.compute(profile(), day)["rows"], path)
    rows = list(csv.DictReader(path.open()))
    assert [(r["report_day"], r["metric"]) for r in rows] == [
        ("2025-02-01", "Revenue"), ("2025-02-01", "Sales"), ("2025-02-01", "Big sales"),
        ("2025-02-02", "Revenue"), ("2025-02-02", "Sales"), ("2025-02-02", "Big sales")]


def test_a_broken_metric_writes_nothing(tmp_path):
    path = tmp_path / "history.csv"
    daily.save(daily.compute(profile(), "2025-02-01")["rows"], path)
    before = path.read_text()
    broken = METRICS + [{"name": "Typo", "definition": "d", "sql": "SUM(amont)", "table": "sales"}]
    with pytest.raises(daily.ReportError, match="Typo"):
        daily.compute(profile(broken), "2025-02-02")
    assert path.read_text() == before


def test_the_command_fails_loudly_and_reports_the_sample(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("DB_PROFILE", "porsche")                  # main() sets it; this undoes it after the test
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert daily.main(["--profile", "porsche", "--day", "latest", "--out", str(tmp_path)]) == 0
    latest = (tmp_path / "porsche" / "latest.md").read_text()
    assert "Report day: **2023-11-22**" in latest and "| Cars sold | 6 |" in latest
    assert latest in summary.read_text()
    assert daily.main(["--profile", "chinook", "--out", str(tmp_path)]) == 1        # no metric dictionary
    assert daily.main(["--profile", "porsche", "--day", "2023-13-01", "--out", str(tmp_path)]) == 1
    assert "nothing was written" in capsys.readouterr().err
