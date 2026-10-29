"""Web interface for the SQL agent.

Run:   python -m streamlit run app.py
Demo:  DEMO=1 python -m streamlit run app.py     (replays two recorded runs - no database or API key needed)
Title, description and example questions come from the active profile (DB_PROFILE, see profiles/).
"""
import glob
import json
import os
import threading
import time
from pathlib import Path

import streamlit as st

import db
from dbprofile import load_profile

HERE = Path(__file__).parent


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
DEMO = os.environ.get("DEMO", "").lower() in ("1", "true", "yes")

try:
    PROFILE = load_profile("porsche" if DEMO else None)      # the recorded demo runs are on Porsche
    PROFILE_ERROR = None
except Exception as e:                                        # bad DB_PROFILE: still show the page, with the reason
    PROFILE, PROFILE_ERROR = load_profile("generic"), str(e)
EXAMPLES = PROFILE["examples"]

st.set_page_config(page_title=PROFILE["title"], page_icon="🏁", layout="centered")

# ------------------------------------------------------------------ look
# Palette taken from period Porsche paint: Chalk (ground), Slate Grey (ink), Oak Green Metallic (accent),
# Signal Yellow (used once, as the tachometer redline under the wordmark).
st.html("""
<style>
@import url("https://fonts.googleapis.com/css2?family=Michroma&family=Hanken+Grotesk:wght@400;500;600;700&family=Heebo:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap");
:root {
  --chalk: #EDEEEA; --paper: #F7F8F5; --slate: #22282A; --steel: #5E6A6B;
  --oak: #2F5D4E; --oak-soft: #DCE6E0; --signal: #E8B71A; --rule: #D3D7D0;
  --ok: #2F7D4F; --bad: #B4432F;
  --display: "Michroma", "Eurostile", "Arial Black", sans-serif;
  --body: "Hanken Grotesk", "Heebo", "Helvetica Neue", Arial, sans-serif;
  --mono: "JetBrains Mono", "SFMono-Regular", Menlo, monospace;
}
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp h4, .stApp td, .stApp th { font-family: var(--body); }
.block-container { padding-top: 4.6rem; max-width: 52rem; }   /* clears Streamlit's 60px top bar */
/* Hebrew and English in the same view: every paragraph takes the direction of its first letter */
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li,
[data-testid="stChatMessageContent"] p, textarea { unicode-bidi: plaintext; text-align: start; }

.brand { display: flex; flex-direction: column; gap: .35rem; margin-bottom: 1.4rem; }
.brand h1 {
  font-family: var(--display); font-weight: 400; font-size: clamp(1.45rem, 3.4vw, 2.1rem);
  letter-spacing: .02em; color: var(--slate); margin: 0; padding: 0; line-height: 1.15;
}
.brand .redline { width: 7.5rem; height: 4px; background: linear-gradient(90deg, var(--slate) 0 70%, var(--signal) 70% 100%); }
.brand p { color: var(--steel); font-size: 1.02rem; margin: .25rem 0 0; max-width: 40rem; line-height: 1.5; }

.trace { list-style: none; margin: .2rem 0 .4rem; padding: 0; display: grid; gap: .45rem; }
.trace li { display: grid; grid-template-columns: 1.7rem 1fr; gap: .6rem; align-items: start; }
.trace .n {
  width: 1.7rem; height: 1.7rem; border-radius: 50%; border: 1.5px solid var(--oak);
  color: var(--oak); font: 600 .78rem/1.6rem var(--body); text-align: center; font-variant-numeric: tabular-nums;
}
.trace .what { min-width: 0; }
.trace .tool { font-weight: 600; color: var(--slate); }
.trace .res { color: var(--steel); font-size: .9rem; }
.trace .res.err { color: var(--bad); }
.trace code {
  display: block; margin-top: .2rem; white-space: pre-wrap; word-break: break-word;
  font: .8rem/1.45 var(--mono); color: var(--slate); background: var(--paper);
  border: 1px solid var(--rule); border-radius: 4px; padding: .35rem .5rem;
}
.meta { color: var(--steel); font-size: .86rem; font-variant-numeric: tabular-nums; }
.demo-note { border-left: 3px solid var(--signal); padding: .4rem .7rem; background: var(--paper); color: var(--slate); font-size: .9rem; margin-bottom: 1rem; }

.score { display: flex; align-items: baseline; gap: .5rem; }
.score b { font-family: var(--display); font-weight: 400; font-size: 1.9rem; color: var(--oak); font-variant-numeric: tabular-nums; }
.score span { color: var(--steel); }
[data-testid="stSidebar"] .stMarkdown p { font-size: .92rem; }
.stApp code, .stApp pre { font-family: var(--mono) !important; }
.rtl, .rtl p, .rtl li { direction: rtl; text-align: right; unicode-bidi: normal; }
.rtl ul, .rtl ol { padding-right: 1.4rem; padding-left: 0; }
</style>
""")

st.html(f"""
<div class="brand">
<h1>{esc(PROFILE["title"])}</h1>
<div class="redline" aria-hidden="true"></div>
<p>{esc(PROFILE["description"])}</p>
</div>
""")
if PROFILE_ERROR:
    st.error(PROFILE_ERROR)


# ------------------------------------------------------------------ sidebar
def latest_score():
    files = sorted(glob.glob(str(HERE / "eval_results" / "*notes-on.json")))
    if not files:
        return None
    try:
        data = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
        return data["score"], data.get("avg_seconds"), data.get("models_used", [])
    except Exception:
        return None


with st.sidebar:
    st.markdown("#### Model")
    provider = st.radio("Provider", ["claude", "gemini"], index=0 if os.environ.get("PROVIDER") == "claude" else 1,
                        format_func=lambda p: {"claude": "Claude (paid API)", "gemini": "Gemini (free tier)"}[p],
                        label_visibility="collapsed", disabled=DEMO, key="provider")
    notes = st.toggle("Business notes in the schema", value=True, disabled=DEMO, key="notes",
                      help="Currency, units and value formats the raw schema cannot tell the model.")
    st.markdown("#### Database")
    try:
        st.caption("Recorded runs - no database connected" if DEMO
                   else f"{db.describe()} · profile: {PROFILE['name']}")
    except Exception as e:
        st.caption(f"Not connected: {e}")
    if PROFILE["answer_key"]:
        st.markdown("#### Answer key")
        score = latest_score() or ("12/12", 10.0, ["claude-sonnet-5-5"])
        st.html(f'<div class="score"><b>{score[0]}</b><span>questions correct</span></div>')
        st.caption("Scored against 12 business questions with known answers, by running the agent's SQL "
                   "on SQL Server and comparing the full result.")
    st.markdown("#### Safety")
    st.caption("Read-only connection. Only single SELECT queries reach the database, each stopped after "
               "30 seconds. At most 8 steps per question.")
    st.markdown("#### Conversation")
    turns = sum(1 for h in st.session_state.get("history", []) if not h["run"].get("stopped"))
    st.caption(f"The agent sees the last {min(turns, 6)} of {turns} earlier turns, so follow-up questions work."
               if turns else "Follow-up questions see the earlier turns of this chat.")
    if st.button("New conversation", icon=":material/add_comment:", disabled=not st.session_state.get("history")):
        st.session_state.history = []
        st.session_state.chat_id = st.session_state.get("chat_id", 0) + 1   # fresh example picker too
        st.rerun()
    st.markdown("[Source code on GitHub](https://github.com/kfirsh97/porsche-sales-database/tree/main/agent)")


# ------------------------------------------------------------------ rendering


def step_html(i, step):
    tool = {"get_schema": "Read the database structure", "run_query": "Ran a query"}.get(step["tool"], step["tool"])
    sql = step.get("args", {}).get("sql")
    res = step.get("result", "")
    err = "err" if str(res).startswith("ERROR") else ""
    body = f'<span class="tool">{esc(tool)}</span> <span class="res {err}">{esc(res)}</span>'
    if sql:
        body += f"<code>{esc(sql)}</code>"
    return f'<li><span class="n">{i}</span><div class="what">{body}</div></li>'


def trace_html(steps):
    return '<ol class="trace">' + "".join(step_html(i, s) for i, s in enumerate(steps, 1)) + "</ol>"


def is_hebrew(text):
    for ch in text or "":
        if ch.isalpha():
            return "\u0590" <= ch <= "\u05FF"
    return False


def md(text):
    """Markdown that flips to right-to-left (bullets included) when the text is Hebrew."""
    if is_hebrew(text):
        st.markdown(f'<div class="rtl">\n\n{text}\n\n</div>', unsafe_allow_html=True)
    else:
        st.markdown(text)


def show_result(run):
    md(run["answer"] or "_No answer._")
    if run.get("rows"):
        import pandas as pd
        df = pd.DataFrame(run["rows"], columns=run["columns"])
        cfg = {c: st.column_config.NumberColumn(format="localized")
               for c in df.select_dtypes("number").columns if "year" not in c.lower()}
        st.dataframe(df, hide_index=True, width="stretch", column_config=cfg)
    if run.get("final_sql"):
        with st.expander("SQL behind this answer"):
            st.code(run["final_sql"], language="sql")
    steps = len(run["steps"])
    st.html(f'<div class="meta">{run["model"]}, {steps} step{"s" if steps != 1 else ""}, '
            f'{run["seconds"]:.1f} s</div>')


def run_demo(question, status, live, history, job):
    from demo_runs import DEMO_RUNS
    rec = DEMO_RUNS.get(question)
    if rec is None:
        status.update(label="Demo mode answers only the two recorded questions", state="error")
        return None
    for i in range(1, len(rec["steps"]) + 1):
        time.sleep(0.7)
        job["steps"][:] = rec["steps"][:i]
        live.html(trace_html(rec["steps"][:i]))
    return rec


def run_live(question, status, live, history, job):
    """The agent runs in a background thread while this page keeps redrawing its steps - so the page stays
    responsive, and a click on Stop ends the run at once instead of after the current model call."""
    os.environ["SCHEMA_NOTES"] = "on" if notes else "off"
    import agent
    from tools import run_query_all

    def work():
        try:
            job["result"] = agent.ask(question, verbose=False, provider=provider, on_step=job["steps"].append,
                                      history=history, should_stop=job["cancel"].is_set)
        except Exception as e:
            job["error"] = e

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    while worker.is_alive():
        live.html(trace_html(job["steps"]))      # every redraw is also the moment Streamlit can notice a Stop click
        worker.join(0.25)
    if job.get("error"):
        raise job["error"]
    r = job["result"]
    rec = {**r, "columns": [], "rows": []}
    if r.get("final_sql"):
        try:
            rec["columns"], rec["rows"] = run_query_all(r["final_sql"], max_rows=500)
        except Exception:
            pass
    return rec


# ------------------------------------------------------------------ conversation
def abandon(job):
    """The page was rerun while the agent was working - the Stop button, or any other click. Tell the
    background agent to stop before its next model call, and keep the question in the chat as stopped."""
    job["cancel"].set()
    st.session_state.history.append({"question": job["question"], "run": {
        "answer": "_Stopped. The agent finishes the model call already in progress, then makes no more._",
        "steps": list(job["steps"]), "final_sql": None, "columns": [], "rows": [], "stopped": True,
        "model": "stopped", "seconds": time.time() - job["started"]}})
    st.session_state.job = None


if DEMO:
    st.html('<div class="demo-note">Demo mode: replaying two real runs recorded on 29 October 2026. '
            "Pick one of the first two example questions.</div>")

if "history" not in st.session_state:
    st.session_state.history = []
if st.session_state.get("job"):
    abandon(st.session_state.job)

for item in st.session_state.history:
    with st.chat_message("user", avatar=":material/person:"):
        md(item["question"])
    with st.chat_message("assistant", avatar=":material/database:"):
        if item["run"].get("stopped"):
            md(item["run"]["answer"])
            if item["run"]["steps"]:
                st.html(trace_html(item["run"]["steps"]))
            continue
        if item["run"]["steps"]:          # a plain chat reply has no steps to show
            n_steps = len(item["run"]["steps"])
            with st.expander(f"How the agent got there ({n_steps} step{'s' if n_steps != 1 else ''})"):
                st.html(trace_html(item["run"]["steps"]))
        show_result(item["run"])

picked = None
if EXAMPLES and not st.session_state.history:   # examples only start a chat; after that, the user just keeps talking
    picked = st.pills("Try a question", EXAMPLES, key=f"pick{st.session_state.get('chat_id', 0)}")
typed = st.chat_input("Ask a follow-up, or a new question" if st.session_state.history
                      else "Ask a question about the data, in Hebrew or English")
question = typed or picked

if question:
    with st.chat_message("user", avatar=":material/person:"):
        md(question)
    with st.chat_message("assistant", avatar=":material/database:"):
        with st.status("Working on it", expanded=True) as status:
            st.button("Stop", icon=":material/stop_circle:", key=f"stop{len(st.session_state.history)}",
                      help="Stop this question. The agent makes no further model calls.")
            live = st.empty()
            job = {"question": question, "steps": [], "cancel": threading.Event(), "started": time.time()}
            st.session_state.job = job
            try:
                earlier = [{"question": h["question"], "answer": h["run"]["answer"]}
                           for h in st.session_state.history if not h["run"].get("stopped")]
                run = (run_demo if DEMO else run_live)(question, status, live, earlier, job)
                st.session_state.job = None
            except Exception as e:
                st.session_state.job = None
                run = None
                msg = str(e)
                if "credit balance" in msg.lower():
                    msg = "The Anthropic account has no API credit. Add credit, or switch to Gemini in the sidebar."
                elif "Login failed" in msg or "TDS" in msg:
                    msg = "Can't reach the database. Start SQL Server in Docker (docker start sqlserver) and try again."
                elif isinstance(e, db.SetupError):
                    msg = f"Setup: {msg}"
                elif "connection" in msg.lower() or "could not connect" in msg.lower():
                    msg = f"Can't reach the database ({db.describe()}). Check DATABASE_URL in .env and that the server is running."
                status.update(label=f"Stopped: {msg[:160]}", state="error")
            if run:
                n = len(run["steps"])
                label = f"Answered in {n} step{'s' if n != 1 else ''}" if n else "Answered"
                status.update(label=label, state="complete", expanded=False)
        if run:
            show_result(run)
            st.session_state.history.append({"question": question, "run": run})
            st.rerun()          # redraw from history: sidebar turn count updates, examples give way to the chat
