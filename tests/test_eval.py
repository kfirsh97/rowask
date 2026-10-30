"""End-to-end test of the evaluation runner with a fake agent and fake database."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent  # noqa: E402
import eval as evaluation  # noqa: E402
import tools  # noqa: E402
import db as database  # noqa: E402
import dbprofile  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def on_sql_server(monkeypatch):
    """The answer key is T-SQL, so eval runs only on SQL Server. These tests fake the database itself;
    the settings just make the agent believe it is on SQL Server (no connection is opened)."""
    for k in ("DATABASE_URL", "DB_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DB_USER", "agent_reader")
    monkeypatch.setenv("DB_PASSWORD", "x")
    database._engine_for.cache_clear()
    dbprofile.load_profile.cache_clear()
    yield
    database._engine_for.cache_clear()


def test_refuses_to_score_on_a_database_the_answer_key_is_not_for(monkeypatch):
    monkeypatch.delenv("DB_USER")                       # -> the SQLite copy of Porsche
    monkeypatch.setattr(sys, "argv", ["eval.py"])
    with pytest.raises(SystemExit, match="SQL Server"):
        evaluation.main()


def test_scores_pass_and_fail(monkeypatch, tmp_path):
    db = {"EXPECTED_8": [(10,)], "GOOD": [("x", 10)], "BAD": [(9,)]}

    def fake_run_all(sql):
        if "EXCEPT" in sql:            # question 8's expected query
            return ["n"], db["EXPECTED_8"]
        if "HAVING SUM(Sold_Price) > 1000000" in sql and "agent" not in sql:
            return ["id", "t"], [(1, 2_000_000)]
        return ["c"], db[sql]

    answers = {8: "GOOD", 12: "BAD"}

    def fake_ask(question, verbose=False, provider=None):
        qid = 8 if "לא ביצעו" in question else 12
        return {"model": "fake", "steps": [{}], "seconds": 1.0, "final_sql": answers[qid], "answer": "..."}

    monkeypatch.setattr(tools, "run_query_all", fake_run_all)
    monkeypatch.setattr(agent, "ask", fake_ask)
    monkeypatch.setattr(evaluation, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["eval.py", "--ids", "8,12", "--pause", "0"])
    evaluation.main()

    saved = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["score"] == "1/2"
    assert [r["correct"] for r in saved["results"]] == [True, False]


def test_overload_is_error_not_wrong_answer(monkeypatch, tmp_path):
    """A question that fails only because the provider is overloaded is not counted as wrong."""
    monkeypatch.setattr(tools, "run_query_all", lambda sql: (["n"], [(1,)]))
    monkeypatch.setattr(evaluation.time, "sleep", lambda s: None)

    def overloaded(question, verbose=False, provider=None):
        raise RuntimeError("503 UNAVAILABLE high demand")

    monkeypatch.setattr(agent, "ask", overloaded)
    monkeypatch.setattr(evaluation, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["eval.py", "--ids", "8", "--pause", "0"])
    evaluation.main()
    saved = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["provider_errors"] == 1
    assert saved["score"] == "0/0"
    assert saved["results"][0]["correct"] is None


def test_superstore_answer_key_scores_traps(monkeypatch, tmp_path):
    """The Superstore key runs on the real SQLite file: right queries pass, the classic wrong one fails."""
    from answer_keys import superstore
    monkeypatch.setenv("DB_PROFILE", "superstore")
    monkeypatch.delenv("DB_USER")
    dbprofile.load_profile.cache_clear()
    gold = {q["question"]: q["expected_sql"] for q in superstore.QUESTIONS}

    def fake_ask(question, verbose=False, provider=None):
        sql = gold[question]
        if question == superstore.QUESTIONS[0]["question"]:          # order lines counted as orders
            sql = "SELECT COUNT(*) FROM superstore WHERE strftime('%Y', Order_Date) = '2025'"
        return {"final_sql": sql, "answer": "", "steps": [], "seconds": 0.1, "model": "fake"}

    monkeypatch.setattr(agent, "ask", fake_ask)
    monkeypatch.setattr(evaluation.time, "sleep", lambda s: None)
    monkeypatch.setattr(evaluation, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["eval.py", "--pause", "0"])
    evaluation.main()
    saved = json.loads(next(tmp_path.glob("*_superstore_*.json")).read_text(encoding="utf-8"))
    n, traps = len(superstore.QUESTIONS), sum(1 for q in superstore.QUESTIONS if q.get("trap"))
    assert saved["score"] == f"{n - 1}/{n}" and saved["traps"] == f"{traps - 1}/{traps}"


def test_a_ranked_list_answers_a_which_is_highest_question():
    assert evaluation.top_row_answers([("California",)], [("California", 457688), ("New York", 310876)],
                                      "SELECT s, SUM(x) FROM t GROUP BY s ORDER BY 2 DESC LIMIT 3")
    assert not evaluation.top_row_answers([("California",)], [("New York", 1), ("California", 2)],
                                          "SELECT s, SUM(x) FROM t GROUP BY s ORDER BY 2 LIMIT 3")
    assert not evaluation.top_row_answers([("California",)], [("California", 1), ("Texas", 2)],
                                          "SELECT s, SUM(x) FROM t GROUP BY s")          # not sorted: no "top"
    assert not evaluation.top_row_answers([(1,), (2,)], [(1,), (2,), (3,)], "SELECT 1 ORDER BY 1")
