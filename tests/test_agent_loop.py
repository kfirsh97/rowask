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
    monkeypatch.setattr(agent, "get_schema", lambda tables=None: "TABLES: Sales(...)")

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
    monkeypatch.setattr(agent, "get_schema", lambda tables=None: "TABLES")
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
                raise RuntimeError("504 DEADLINE_EXCEEDED. Deadline expired before operation could complete.")
            content = agent.types.Content(role="model", parts=[agent.types.Part.from_text(text="ok")])
            return SimpleNamespace(function_calls=None, text="answer", candidates=[SimpleNamespace(content=content)])

    monkeypatch.setattr(agent, "_working_model", None)
    r = agent.ask("q", client=SimpleNamespace(models=Busy()), verbose=False)
    assert r["answer"] == "answer"
    assert r["model"] == agent.FALLBACK_MODELS[0]
    assert used.count(agent.MODEL) == 2          # 1 try + 1 quick retry before switching

    # The next question starts directly with the model that worked - no time wasted on the busy one.
    used.clear()
    agent.ask("q2", client=SimpleNamespace(models=Busy()), verbose=False)
    assert used == [agent.FALLBACK_MODELS[0]]


def test_claude_loop_fixes_error_and_answers(monkeypatch, tmp_path):
    """Same loop on Claude's API format: tool_use blocks in, tool_result blocks out."""
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    results = iter([{"error": "Invalid column name 'Year'."},
                    {"columns": ["n"], "rows": [{"n": 1}], "row_count": 1, "truncated": False}])
    monkeypatch.setattr(agent, "run_query", lambda sql: next(results))
    sent = []

    def block(**kw):
        return SimpleNamespace(**kw)

    script = [
        [block(type="tool_use", id="a", name="run_query", input={"sql": "SELECT Year FROM Sales"})],
        [block(type="tool_use", id="b", name="run_query", input={"sql": "SELECT YEAR(Sale_Date) FROM Sales"})],
        [block(type="text", text="Done.")],
    ]

    class Messages:
        def create(self, **kw):
            sent.append(kw["messages"][-1])
            return SimpleNamespace(content=script.pop(0))

    r = agent.ask("q", client=SimpleNamespace(messages=Messages()), verbose=False, provider="claude")
    assert r["answer"] == "Done."
    assert r["final_sql"] == "SELECT YEAR(Sale_Date) FROM Sales"
    assert sent[1]["content"][0]["is_error"] is True          # the error went back to the model
    assert r["model"] == agent.CLAUDE_MODEL


def test_final_sql_is_the_query_shown_in_the_answer(monkeypatch, tmp_path):
    """A side check (NULL dates) runs last, but the answer's SQL block is what gets logged."""
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    monkeypatch.setattr(agent, "run_query", lambda sql: {"columns": [], "rows": [], "row_count": 0, "truncated": False})
    client = fake_client([
        ([("run_query", {"sql": "SELECT YEAR(d), COUNT(*) FROM Sales GROUP BY YEAR(d)"})], None),
        ([("run_query", {"sql": "SELECT COUNT(*) FROM Sales WHERE d IS NULL"})], None),
        ([], "Answer\n```sql\nSELECT YEAR(d), COUNT(*) FROM Sales GROUP BY YEAR(d)\n```"),
    ])
    r = agent.ask("q", client=client, verbose=False)
    assert r["final_sql"] == "SELECT YEAR(d), COUNT(*) FROM Sales GROUP BY YEAR(d)"


def test_on_step_reports_each_step_live(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    monkeypatch.setattr(agent, "get_schema", lambda tables=None: "TABLES")
    seen = []
    client = fake_client([([("get_schema", {})], None), ([], "done")])
    agent.ask("q", client=client, verbose=False, on_step=seen.append)
    assert [s["tool"] for s in seen] == ["get_schema"]


# ------------------------------------------------------------------ conversation (chat memory)
HISTORY = [
    {"question": "How many sales were there in 2022?", "answer": "There were 1,512 sales.\n```sql\nSELECT COUNT(*) FROM Sales WHERE YEAR(Sold_Date) = 2022\n```"},
    {"question": "ומה לגבי 2023?", "answer": "ב-2023 היו 1,487 מכירות."},
]


def _texts(contents):
    return [(c.role, c.parts[0].text) for c in contents]


def test_gemini_sees_earlier_turns_before_the_new_question(monkeypatch, tmp_path):
    """A follow-up like 'and in 2024?' only makes sense with the earlier turns in front of the model."""
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    client = fake_client([([], "ב-2024 היו 1,555 מכירות.")])
    r = agent.ask("ו-2024?", client=client, verbose=False, history=HISTORY)

    sent = _texts(client.models.seen[0])
    assert sent == [("user", HISTORY[0]["question"]), ("model", HISTORY[0]["answer"]),
                    ("user", HISTORY[1]["question"]), ("model", HISTORY[1]["answer"]),
                    ("user", "ו-2024?")]
    assert r["history_turns"] == 2


def test_claude_sees_earlier_turns_before_the_new_question(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    sent = []

    class Messages:
        def create(self, **kw):
            sent.append(list(kw["messages"]))
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    agent.ask("and 2024?", client=SimpleNamespace(messages=Messages()), verbose=False,
              provider="claude", history=HISTORY)
    assert [m["role"] for m in sent[0]] == ["user", "assistant", "user", "assistant", "user"]
    assert sent[0][1]["content"] == HISTORY[0]["answer"]
    assert sent[0][-1]["content"] == "and 2024?"


def test_history_is_capped_and_skips_unfinished_turns():
    long = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(10)]
    long.insert(3, {"question": "crashed before answering", "answer": None})
    pairs = agent._history_pairs(long)
    assert len(pairs) == agent.HISTORY_TURNS
    assert pairs[-1] == ("q9", "a9")                     # the newest turns are the ones kept
    assert all(a for _, a in pairs)
    assert agent._history_pairs(None) == []


def test_no_history_means_a_single_fresh_question(monkeypatch, tmp_path):
    """The eval runner and the CLI one-shot call ask() without history - nothing changes for them."""
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    client = fake_client([([], "done")])
    r = agent.ask("q", client=client, verbose=False)
    assert _texts(client.models.seen[0]) == [("user", "q")]
    assert r["history_turns"] == 0


def test_plain_chat_reply_runs_no_query(monkeypatch, tmp_path):
    """'Thanks!' gets a reply without touching the database, and no SQL is shown under it."""
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    monkeypatch.setattr(agent, "run_query", lambda sql: (_ for _ in ()).throw(AssertionError("no query expected")))
    client = fake_client([([], "בשמחה! יש עוד משהו שתרצה לבדוק?")])
    r = agent.ask("תודה!", client=client, verbose=False, history=HISTORY)
    assert r["steps"] == [] and r["final_sql"] is None
