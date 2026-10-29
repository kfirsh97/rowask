"""Tests for the agent loop, using a fake model (no API key or database needed)."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent  # noqa: E402


class FakeModels:
    """Plays back a script of model responses: tool calls first, then a final answer."""
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def generate_content(self, model, contents, config):
        self.seen.append(list(contents))
        calls, text = self.script.pop(0)
        fc = [SimpleNamespace(name=n, args=a) for n, a in calls]
        content = agent.types.Content(role="model", parts=[agent.types.Part.from_text(text="(tool call)")])
        return SimpleNamespace(function_calls=fc or None, text=text,
                               candidates=[SimpleNamespace(content=content)])


def fake_client(script):
    return SimpleNamespace(models=FakeModels(script))


def test_fixes_its_own_error(monkeypatch, tmp_path):
    """The model writes a bad query, gets the error back, fixes it, then answers."""
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    results = iter([
        {"error": "Invalid object name 'Salez'."},
        {"columns": ["n"], "rows": [{"n": 4554}], "row_count": 1, "truncated": False},
    ])
    monkeypatch.setattr(agent, "run_query", lambda sql: next(results))
    monkeypatch.setattr(agent, "get_schema", lambda: "TABLES: Sales(...)")

    client = fake_client([
        ([("get_schema", {})], None),
        ([("run_query", {"sql": "SELECT COUNT(*) n FROM Salez"})], None),
        ([("run_query", {"sql": "SELECT COUNT(*) n FROM Sales"})], None),
        ([], "There were 4,554 sales."),
    ])
    r = agent.ask("How many sales?", client=client, verbose=False)

    assert r["answer"] == "There were 4,554 sales."
    assert r["final_sql"] == "SELECT COUNT(*) n FROM Sales"       # the fixed query, not the broken one
    assert [s["tool"] for s in r["steps"]] == ["get_schema", "run_query", "run_query"]
    assert r["steps"][1]["result"].startswith("ERROR")
    assert (tmp_path).glob("*.jsonl")


def test_stops_after_max_steps(monkeypatch, tmp_path):
    """A model that never stops calling tools is cut off - no infinite loop."""
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    monkeypatch.setattr(agent, "get_schema", lambda: "TABLES")
    client = fake_client([([("get_schema", {})], None)] * (agent.MAX_STEPS + 5))
    r = agent.ask("loop forever", client=client, verbose=False)
    assert "Stopped after" in r["answer"]
    assert len(r["steps"]) == agent.MAX_STEPS


def test_unknown_tool_is_reported_not_crashed():
    assert "error" in agent.call_tool("format_disk", {})


def test_switches_model_when_main_is_overloaded(monkeypatch, tmp_path):
    """503 'high demand' on the main model: retry, then switch to a fallback before step 1."""
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    used = []

    class Busy:
        def generate_content(self, model, contents, config):
            used.append(model)
            if model == agent.MODEL:
                raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
            content = agent.types.Content(role="model", parts=[agent.types.Part.from_text(text="ok")])
            return SimpleNamespace(function_calls=None, text="answer", candidates=[SimpleNamespace(content=content)])

    r = agent.ask("q", client=SimpleNamespace(models=Busy()), verbose=False)
    assert r["answer"] == "answer"
    assert r["model"] == agent.FALLBACK_MODELS[0]
    assert used.count(agent.MODEL) == 4          # 1 try + 3 retries before switching
