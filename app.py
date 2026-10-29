"""Web interface for the SQL agent.

Run:   python -m streamlit run app.py
Demo:  DEMO=1 python -m streamlit run app.py     (replays two recorded runs - no database or API key needed)
Title, description and example questions come from the active profile (DB_PROFILE, see profiles/) -
or, after someone uploads files in the sidebar, from the cleaned upload (for that browser session only).
"""
import contextvars
import glob
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import streamlit as st

import charts
import dashboards
import db
import drill
import dbprofile
import upload as uploads
from dbprofile import load_profile

HERE = Path(__file__).parent


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
DEMO = os.environ.get("DEMO", "").lower() in ("1", "true", "yes")
UPLOAD_TYPES = ["csv", "txt", "tsv", "xlsx", "xlsm", "xls", "sql", "sqlite", "sqlite3", "db"]


def sync_upload():
    """Build a database from the files in the sidebar uploader whenever they change, and drop it when they are
    removed. Runs at the top of every page run, so the title, the agent and the schema all follow the upload."""
    files = st.session_state.get(f"files{st.session_state.get('upload_gen', 0)}") or []
    signature = tuple((f.name, f.size) for f in files)
    if signature == st.session_state.get("upload_sig", ()):
        return
    old = st.session_state.pop("upload", None)
    if old:
        uploads.remove(old)
    st.session_state.upload_sig, st.session_state.upload_error = signature, None
    st.session_state.history = []                                   # new data, new conversation
    st.session_state.dash_upload = []                               # and an empty dashboard for it
    st.session_state.chat_id = st.session_state.get("chat_id", 0) + 1
    if files:
        try:
            with st.spinner("Loading your files - a large SQL script can take a few seconds..."):
                st.session_state.upload = uploads.build([(f.name, f.getvalue()) for f in files],
                                                        Path(tempfile.mkdtemp(prefix="sql-agent-upload-")))
        except Exception as e:
            st.session_state.upload_error = str(e)


if not DEMO:
    sync_upload()
UPLOAD = st.session_state.get("upload")
db.SESSION_URL.set(UPLOAD.url if UPLOAD else None)                 # this session only - see db.py
dbprofile.SESSION_PROFILE.set(UPLOAD.profile if UPLOAD else None)

try:
    PROFILE = load_profile("porsche" if DEMO else None)      # the recorded demo runs are on Porsche
    PROFILE_ERROR = None
except Exception as e:                                        # bad DB_PROFILE: still show the page, with the reason
    PROFILE, PROFILE_ERROR = load_profile("generic"), str(e)
EXAMPLES = PROFILE["examples"]

BRAND = "RowAsk"
st.set_page_config(page_title=f"{BRAND} · {PROFILE['title']}", page_icon="🏁", layout="centered")

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
.brand .wordmark { font-family: var(--display); font-size: .8rem; letter-spacing: .28em; text-transform: uppercase;
  color: var(--oak); margin-bottom: -.1rem; }
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
.stat { background: var(--paper); border: 1px solid var(--rule); border-radius: 8px; padding: .8rem 1rem; }
.stat-label { color: var(--steel); font-size: .85rem; unicode-bidi: plaintext; }
.stat-value { font-size: 2rem; font-weight: 600; color: var(--slate); line-height: 1.2; }
.stat-caption { color: var(--steel); font-size: .78rem; unicode-bidi: plaintext; }
.dash-title { font-weight: 600; color: var(--slate); unicode-bidi: plaintext; text-align: start; margin-bottom: .3rem; }
.rtl, .rtl p, .rtl li { direction: rtl; text-align: right; unicode-bidi: normal; }
.rtl ul, .rtl ol { padding-right: 1.4rem; padding-left: 0; }
</style>
""")

st.html(f"""
<div class="brand">
<div class="wordmark">{BRAND}</div>
<h1>{esc(PROFILE["title"])}</h1>
<div class="redline" aria-hidden="true"></div>
<p>{esc(PROFILE["description"])}</p>
</div>
""")
if PROFILE_ERROR:
    st.error(PROFILE_ERROR)
if UPLOAD:
    tables = ", ".join(f"{t} ({n:,} rows)" for t, n in UPLOAD.tables.items())
    with st.expander(f"Loaded {len(UPLOAD.tables)} table{'s' if len(UPLOAD.tables) != 1 else ''}: {tables}",
                     expanded=not st.session_state.get("history"), icon=":material/cleaning_services:"):
        st.markdown("**What was cleaned**\n\n" + "\n".join(f"- {esc(r)}" for r in UPLOAD.report))
        st.caption("The agent also gets a description of every column - its values, ranges and dates - "
                   "so it can write correct SQL for data it has never seen.")


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
    st.markdown("#### Your data")
    st.file_uploader("Upload CSV, Excel, SQL scripts or a SQLite database", type=UPLOAD_TYPES,
                     accept_multiple_files=True, key=f"files{st.session_state.get('upload_gen', 0)}", disabled=DEMO,
                     help="CSV and Excel are cleaned (column names, dates, numbers, codes). SQL scripts - CREATE TABLE "
                          "and INSERT, from SQL Server, MySQL, PostgreSQL or SQLite - are run in order of file name. "
                          "Everything is loaded into a database for this browser session only. Remove the files "
                          "to go back to the default database.")
    if st.session_state.get("upload_error"):
        st.error(st.session_state.upload_error)
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


def table(columns, rows):
    import pandas as pd
    df = pd.DataFrame([list(r) for r in rows], columns=columns)
    cfg = {c: st.column_config.NumberColumn(format="localized")
           for c in df.select_dtypes("number").columns if "year" not in str(c).lower()}
    st.dataframe(df, hide_index=True, width="stretch", column_config=cfg)


def draw(spec, columns, rows, height=300) -> bool:
    """Draw a chart (or a stat tile) from real query rows. False when this result has no chart."""
    out = charts.build(spec, columns, rows, height=height) if spec else None
    if out is None:
        return False
    if isinstance(out, tuple):
        st.html(charts.stat_html(out[1], out[2]))
    else:
        st.altair_chart(charts.style(out), width="stretch", theme=None)   # theme=None keeps the chart's own styling
    return True


DRILLABLE = {"bar", "grouped_bar", "stacked_bar"}


def drill_level(key, spec, columns, rows, sql):
    """The level of a drill-down this chart is at: (spec, columns, rows, sql, path). path = [(column, value), ...]."""
    path = st.session_state.setdefault("drill", {}).get(key, [])
    if not path:
        return spec, columns, rows, sql, []
    level = path[-1]
    try:
        cols, rws = tools_mod().run_query_all(level["sql"], max_rows=500)
    except Exception as e:
        st.error(f"Could not drill down: {e}")
        st.session_state.drill[key] = []
        return spec, columns, rows, sql, []
    child_spec = {**spec, "x": level["child"]}
    spec2 = charts.check_spec(child_spec, cols) or charts.auto_spec(cols, rws)
    return spec2, cols, rws, level["sql"], path


def tools_mod():
    import tools
    return tools


def drill_into(key, sql, x, value):
    out = drill.rewrite(sql, x, value)
    if out:
        new_sql, child = out
        st.session_state.drill.setdefault(key, []).append({"x": x, "value": value, "child": child, "sql": new_sql})


def drill_back(key, depth):
    st.session_state.drill[key] = st.session_state.drill.get(key, [])[:depth]


def chart_view(key, spec, columns, rows, sql, height=300, tabs=True):
    """Chart (+ table twin) with drill-down: a click on a bar zooms into the next level inside it.
    Returns False when the result has no chart (the caller shows the table)."""
    spec, columns, rows, sql, path = drill_level(key, spec, columns, rows, sql)
    if not spec:
        return False
    if path:                                                   # where we are: All › West › California
        names = [f"All {path[0]['x'].replace('_', ' ')}"] + [str(step["value"]) for step in path]
        widths = [len(n) + 6 for n in names]                    # each crumb as wide as its text
        crumbs = st.columns(widths + [max(4, 90 - sum(widths))], gap="small")
        crumbs[0].button(names[0], key=f"crumb{key}-0", icon=":material/arrow_upward:",
                         on_click=drill_back, args=(key, 0))
        for i, step in enumerate(path, 1):
            crumbs[i].button(str(step["value"]), key=f"crumb{key}-{i}", disabled=i == len(path),
                             on_click=drill_back, args=(key, i))
    level = drill.next_level(sql, spec["x"]) if spec.get("type") in DRILLABLE and sql else None

    def chart():
        out = charts.build(spec, columns, rows, height=height, drillable=bool(level))
        if out is None:
            st.caption("No chart for this result.")
            return
        if isinstance(out, tuple):
            st.html(charts.stat_html(out[1], out[2]))
            return
        event = st.altair_chart(charts.style(out), width="stretch", theme=None, key=f"chart{key}-{len(path)}",
                                on_select="rerun" if level else "ignore",
                                selection_mode=charts.DRILL if level else None)
        notes = []
        if spec.get("type") == "bar" and len(rows) > charts.MAX_BARS:
            notes.append(f"Showing the top {charts.MAX_BARS} of {len(rows):,} - all of them are in the Table tab.")
        if level:
            notes.append(f"Click a bar to see its {level[2].replace('_', ' ')}.")
        if notes:
            st.caption(" ".join(notes))
            picked = (event.selection or {}).get(charts.DRILL) if event else None
            if picked:
                value = picked[0].get(spec["x"]) if isinstance(picked, list) else None
                if value is not None:
                    drill_into(key, sql, spec["x"], value)
                    st.rerun()

    if not tabs:
        chart()
        return True
    as_chart, as_table = st.tabs(["Chart", "Table"])           # every chart has its table twin
    with as_chart:
        chart()
    with as_table:
        table(columns, rows)
    return True


def show_result(run, question=None, key=None):
    import re
    text = re.sub(r"```sql.*?```", "", run["answer"] or "", flags=re.S | re.I).strip()   # SQL has its own expander
    md(text or "_No answer._")
    spec = None
    if run.get("rows"):
        spec = charts.check_spec(run.get("chart"), run["columns"]) or charts.auto_spec(run["columns"], run["rows"])
        if not chart_view(f"a{key}", spec, run["columns"], run["rows"], run.get("final_sql")):
            table(run["columns"], run["rows"])
    if run.get("final_sql"):
        with st.expander("SQL behind this answer"):
            st.code(run["final_sql"], language="sql")
    if key is not None and run.get("final_sql") and run.get("rows"):
        pinned = any(i["sql"] == run["final_sql"].strip() for i in dash_items())
        st.button("On the dashboard" if pinned else "Add to dashboard", key=f"pin{key}", disabled=pinned,
                  icon=":material/check:" if pinned else ":material/dashboard_customize:",
                  on_click=pin, args=(question, run["final_sql"], run.get("chart") or spec))
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

    # copy_context: the background thread must see this session's upload (db.SESSION_URL), not the default
    worker = threading.Thread(target=contextvars.copy_context().run, args=(work,), daemon=True)
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


def abandon(job):
    """The page was rerun while the agent was working - the Stop button, or any other click. Tell the
    background agent to stop before its next model call, and keep the question in the chat as stopped."""
    job["cancel"].set()
    st.session_state.history.append({"question": job["question"], "run": {
        "answer": "_Stopped. The agent finishes the model call already in progress, then makes no more._",
        "steps": list(job["steps"]), "final_sql": None, "columns": [], "rows": [], "stopped": True,
        "model": "stopped", "seconds": time.time() - job["started"]}})
    st.session_state.job = None


# ------------------------------------------------------------------ dashboard
def dash_items() -> list:
    """This database's dashboard: saved on this computer per profile; for an upload, kept in this session only."""
    if UPLOAD:
        return st.session_state.setdefault("dash_upload", [])
    store = st.session_state.setdefault("dash", {})
    if PROFILE["name"] not in store:
        store[PROFILE["name"]] = dashboards.load(PROFILE["name"])
    return store[PROFILE["name"]]


def dash_save():
    if not UPLOAD and not DEMO:
        dashboards.save(PROFILE["name"], dash_items())


def pin(question, sql, chart):
    dash_items().append(dashboards.new_item(question or "Untitled", sql, chart))
    dash_save()
    st.toast("Added to the dashboard", icon=":material/dashboard_customize:")


def unpin(item_id):
    items = dash_items()
    items[:] = [i for i in items if i["id"] != item_id]
    dash_save()


def move(item_id, step):
    items = dash_items()
    i = next(k for k, it in enumerate(items) if it["id"] == item_id)
    j = max(0, min(len(items) - 1, i + step))
    items[i], items[j] = items[j], items[i]
    dash_save()


@st.cache_data(ttl=300, show_spinner=False)
def run_item(url, sql, chart_json):
    """Cached per database and query; 'Refresh' clears it. The url keeps different databases apart."""
    return dashboards.run({"sql": sql, "chart": json.loads(chart_json)})


def card_controls(item):
    left, right, drop = st.columns([1, 1, 4], gap="small")
    left.button("", icon=":material/arrow_back:", key=f"l{item['id']}", help="Move earlier",
                on_click=move, args=(item["id"], -1))
    right.button("", icon=":material/arrow_forward:", key=f"r{item['id']}", help="Move later",
                 on_click=move, args=(item["id"], 1))
    drop.button("Remove", icon=":material/close:", key=f"x{item['id']}", on_click=unpin, args=(item["id"],))


def render_dashboard():
    items = dash_items()
    if not items:
        st.info("Nothing here yet. In the chat, ask for something - a total, a trend, a comparison - and press "
                "**Add to dashboard** under the answer. Every item runs its query again whenever the dashboard "
                "opens, so the numbers are always current.", icon=":material/dashboard_customize:")
        return
    url = db.database_url()
    results = {i["id"]: run_item(url, i["sql"], json.dumps(i.get("chart"))) for i in items}
    head, refresh, download = st.columns([4, 1.2, 1.6], vertical_alignment="center")
    head.caption(f"{len(items)} item{'s' if len(items) != 1 else ''} · runs live on {db.describe()}")
    if refresh.button("Refresh", icon=":material/refresh:"):
        run_item.clear()
        st.rerun()
    download.download_button(
        "Download HTML", icon=":material/download:", mime="text/html",
        file_name=f"{PROFILE['name']}-dashboard.html",
        data=dashboards.export_html(f"{PROFILE['title']} dashboard", PROFILE["description"].split(".")[0] + ".",
                                    items, results))

    stats = [i for i in items if (results[i["id"]]["spec"] or {}).get("type") == "stat"]
    others = [i for i in items if i not in stats]
    for row in range(0, len(stats), 4):                           # headline numbers first, up to 4 a row
        for col, item in zip(st.columns(4), stats[row:row + 4]):
            with col:
                res = results[item["id"]]
                _, label, value = charts.build(res["spec"], res["columns"], res["rows"])
                st.html(charts.stat_html(item["title"], value, label))
                st.button("Remove", key=f"x{item['id']}", icon=":material/close:", on_click=unpin, args=(item["id"],))
    cols = st.columns(2, gap="medium")
    for k, item in enumerate(others):
        res = results[item["id"]]
        with cols[k % 2], st.container(border=True):
            st.html(f'<div class="dash-title">{esc(item["title"])}</div>')
            if res["error"]:
                st.error(f"This query no longer runs: {res['error']}")
            elif not chart_view(f"d{item['id']}", res["spec"], res["columns"], res["rows"], item["sql"],
                                height=260, tabs=False):
                table(res["columns"], res["rows"])
            with st.expander("Table and SQL"):
                if not res["error"]:
                    table(res["columns"], res["rows"])
                st.code(item["sql"], language="sql")
            card_controls(item)


if "history" not in st.session_state:
    st.session_state.history = []
view = st.segmented_control("View", ["Chat", "Dashboard"], default="Chat", key="view", label_visibility="collapsed",
                            format_func=lambda v: f"Dashboard ({len(dash_items())})" if v == "Dashboard" else v)
if view == "Dashboard":
    if st.session_state.get("job"):          # leaving the chat while the agent works stops it, like Stop does
        abandon(st.session_state.job)
    render_dashboard()
    st.stop()


# ------------------------------------------------------------------ conversation
if DEMO:
    st.html('<div class="demo-note">Demo mode: replaying two real runs recorded on 29 October 2026. '
            "Pick one of the first two example questions.</div>")

if "history" not in st.session_state:
    st.session_state.history = []
if st.session_state.get("job"):
    abandon(st.session_state.job)

for n, item in enumerate(st.session_state.history):
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
        show_result(item["run"], question=item["question"], key=f"{st.session_state.get('chat_id', 0)}-{n}")

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
