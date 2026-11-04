"""BigQuery support, without a network: the validator in BigQuery's dialect, the engine's guard rails, the profile."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db  # noqa: E402
import dbprofile  # noqa: E402
import metrics  # noqa: E402
import tools  # noqa: E402


@pytest.fixture
def bigquery_dialect(monkeypatch):
    monkeypatch.setattr(tools, "_sql_dialect", lambda: "bigquery")


@pytest.mark.parametrize("sql", [
    "SELECT name, MAX(peak_players) FROM dashboard_game_daily GROUP BY name ORDER BY 2 DESC LIMIT 10",
    "SELECT * FROM `marts.dashboard_game_daily` WHERE REGEXP_CONTAINS(genres, r'\\bAction\\b')",
    "WITH d AS (SELECT appid, SAFE_DIVIDE(price_final_usd, price_initial_usd) AS r FROM fct_game_daily) SELECT * FROM d",
    "SELECT 'drop table x; delete' AS note",
])
def test_ordinary_bigquery_queries_pass(bigquery_dialect, sql):
    assert tools.validate_query(sql) is None


@pytest.mark.parametrize("sql, reason", [
    ("EXPORT DATA OPTIONS(uri='gs://b/*.csv', format='CSV') AS SELECT * FROM t", "Only SELECT"),
    ("SELECT * FROM EXTERNAL_QUERY('conn', 'SELECT 1')", "Forbidden"),
    ("SELECT 1; DROP TABLE dashboard_game_daily", "Only one statement"),
    ("DELETE FROM dashboard_game_daily WHERE true", "Only SELECT"),
    ("SELECT * FROM t /*+ hint */", "Executable comments"),
    ("CREATE TEMP TABLE x AS SELECT 1", "Only SELECT"),
])
def test_writes_and_escapes_are_refused(bigquery_dialect, sql, reason):
    assert reason in (tools.validate_query(sql) or "")


class FakeClient:
    def __init__(self, project=None, location=None, default_query_job_config=None):
        self.project, self.location, self.config = project, location, default_query_job_config


def test_the_client_carries_the_guard_rails(monkeypatch):
    from google.cloud import bigquery
    made = []
    monkeypatch.setattr(bigquery, "Client", lambda **kw: made.append(FakeClient(**kw)) or made[-1])
    db._bigquery_engine(db.make_url("bigquery://my-project/marts?location=US"))
    c = made[0]
    assert (c.project, c.location) == ("my-project", "US")
    assert c.config.maximum_bytes_billed == db.BIGQUERY_MAX_BYTES and int(c.config.job_timeout_ms) == db.QUERY_TIMEOUT_S * 1000
    assert c.config.default_dataset.dataset_id == "marts" and c.config.use_legacy_sql is False


def test_no_sign_in_is_a_plain_message(monkeypatch):
    from google.cloud import bigquery

    def fail(**kw):
        raise RuntimeError("DefaultCredentialsError")
    monkeypatch.setattr(bigquery, "Client", fail)
    with pytest.raises(db.SetupError, match="gcloud auth application-default login"):
        db._bigquery_engine(db.make_url("bigquery://p/marts"))
    with pytest.raises(db.SetupError, match="project and a dataset"):
        db._bigquery_engine(db.make_url("bigquery://p"))


def test_the_project_id_comes_from_env(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    with pytest.raises(db.SetupError, match="GCP_PROJECT"):
        db._expand("bigquery://${GCP_PROJECT}/marts")
    monkeypatch.setenv("GCP_PROJECT", "my-project")
    assert db._expand("bigquery://${GCP_PROJECT}/marts?location=US") == "bigquery://my-project/marts?location=US"


def test_a_scan_over_the_limit_reads_as_advice():
    e = RuntimeError("400 Query exceeded limit for bytes billed: 1000000000. 2000000000 or higher required.\n\n"
                     "Location: US\nJob ID: abc")
    assert "would scan more than" in tools._clean_error(e)
    assert tools._clean_error(RuntimeError("400 Unrecognized name: foo\n\nLocation: US\nJob ID: x")) == \
        "400 Unrecognized name: foo"


def test_the_steam_profile_and_its_metrics_parse():
    dbprofile.load_profile.cache_clear()
    p = dbprofile.load_profile("steam")
    assert p["database_url"].startswith("bigquery://${GCP_PROJECT}/marts")
    ms = metrics.parse(p["metrics"])
    assert {m["name"] for m in ms} == {"Peak players", "Average daily players", "Games on sale", "Average discount",
                                       "Data coverage"}
    assert "NEVER SUM peak_players" in p["notes"]
