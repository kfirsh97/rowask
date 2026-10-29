"""End-to-end test of the evaluation runner with a fake agent and fake database."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent  # noqa: E402
import eval as evaluation  # noqa: E402
import tools  # noqa: E402


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
