"""The metric dictionary: definitions are validated, checked against the database, and reach the agent."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db  # noqa: E402
import dbprofile  # noqa: E402
import metrics  # noqa: E402
import tools  # noqa: E402


@pytest.fixture(autouse=True)
def superstore(monkeypatch):
    if not (Path(__file__).resolve().parents[1] / "data" / "superstore.sqlite").exists():
        pytest.skip("data/superstore.sqlite not built (python setup/csv_to_sqlite.py data/superstore.csv)")
    for k in ("DATABASE_URL", "DB_USER", "DB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DB_PROFILE", "superstore")
    monkeypatch.setenv("SCHEMA_NOTES", "on")
    dbprofile.load_profile.cache_clear(); db._engine_for.cache_clear(); tools._schema_cache.clear()
    metrics._ok_cache.clear()
    yield
    dbprofile.load_profile.cache_clear(); db._engine_for.cache_clear(); tools._schema_cache.clear()


def test_every_superstore_metric_runs():
    ok, bad = metrics.checked(dbprofile.load_profile())
    assert bad == [] and {m["name"] for m in ok} >= {"Revenue", "Orders", "Active customers", "Late shipment rate"}


def test_the_agent_sees_the_dictionary_only_with_notes_on(monkeypatch):
    schema = tools.get_schema()
    assert "METRIC DICTIONARY" in schema and "Active customers" in schema and "לקוחות פעילים" in schema
    assert "90 days before the latest order date" in schema
    monkeypatch.setenv("SCHEMA_NOTES", "off")                     # eval.py --notes off: the "before" score
    assert "METRIC DICTIONARY" not in tools.get_schema()


def test_a_metric_that_does_not_run_is_left_out_and_reported():
    profile = {**dbprofile.load_profile(), "name": "broken",
               "metrics": [{"name": "Good", "definition": "d", "sql": "SUM(Sales)", "table": "superstore"},
                           {"name": "Typo", "definition": "d", "sql": "SUM(Saels)", "table": "superstore"}]}
    ok, bad = metrics.checked(profile)
    assert [m["name"] for m in ok] == ["Good"] and bad[0][0] == "Typo" and "Saels" in bad[0][1]


def test_a_metric_cannot_smuggle_in_a_write():
    profile = {**dbprofile.load_profile(), "name": "evil",
               "metrics": [{"name": "Evil", "definition": "d", "sql": "1; DELETE FROM superstore", "table": "superstore"}]}
    ok, bad = metrics.checked(profile)
    assert ok == [] and bad and tools.run_query_all("SELECT COUNT(*) FROM superstore")[1][0][0] > 0


@pytest.mark.parametrize("raw, message", [
    ([{"name": "X", "sql": "SUM(a)", "table": "t"}], "missing definition"),
    ([{"name": "X", "definition": "d", "sql": "SUM(a)", "table": "t", "colour": "red"}], "unknown setting"),
    ([{"name": "X", "definition": "d", "sql": "1", "table": "t"}] * 2, "defined twice"),
])
def test_malformed_definitions_are_rejected(raw, message):
    with pytest.raises(ValueError, match=message):
        metrics.parse(raw)


def test_a_typo_in_the_date_column_is_caught():
    profile = {**dbprofile.load_profile(), "name": "datetypo",
               "metrics": [{"name": "Revenue", "definition": "d", "sql": "SUM(Sales)", "table": "superstore",
                            "date": "Order_Dat"}]}
    ok, bad = metrics.checked(profile)
    assert ok == [] and "Order_Dat" in bad[0][1]


def test_a_failed_check_is_not_remembered(monkeypatch):
    """A dropped connection must not leave the agent without its dictionary until the app restarts."""
    real = tools.run_query_all
    monkeypatch.setattr(tools, "run_query_all", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    assert metrics.checked(dbprofile.load_profile())[0] == []
    monkeypatch.setattr(tools, "run_query_all", real)
    assert len(metrics.checked(dbprofile.load_profile())[0]) == 6


def test_wrong_types_name_the_problem():
    with pytest.raises(ValueError, match="aliases must be a list"):
        metrics.parse([{"name": "X", "definition": "d", "sql": "1", "table": "t", "aliases": [1, 2]}])
