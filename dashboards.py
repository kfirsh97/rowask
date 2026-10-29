"""Dashboards: answers pinned from the chat, re-run live against the database every time they are shown.

A dashboard item keeps the question, the SQL behind the answer and its chart spec - never the numbers, so the
dashboard is always current. Dashboards for a database profile are saved in dashboards/<profile>.json on this
computer; a dashboard on an uploaded file lives only as long as that browser session.

export_html() turns a dashboard into one standalone page (headline numbers, charts, and the table behind each
chart) that opens in any browser - for sending to someone who does not run the app.
"""
import json
import uuid
from datetime import datetime
from html import escape
from pathlib import Path

import charts
import tools

DIR = Path(__file__).parent / "dashboards"
MAX_ROWS = 500
# Pinned versions of the chart runtime for the exported page (vega-lite matches the Altair that built the specs)
CDN = ["https://cdn.jsdelivr.net/npm/vega@6.3.1", "https://cdn.jsdelivr.net/npm/vega-lite@6.4.1",
       "https://cdn.jsdelivr.net/npm/vega-embed@7.1.0"]


def _path(name: str) -> Path:
    return DIR / f"{''.join(ch for ch in name if ch.isalnum() or ch in '-_') or 'default'}.json"


def load(name: str) -> list:
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save(name: str, items: list):
    DIR.mkdir(exist_ok=True)
    _path(name).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def new_item(title: str, sql: str, chart: dict = None) -> dict:
    return {"id": uuid.uuid4().hex[:8], "title": title.strip(), "sql": sql.strip(), "chart": chart,
            "added": datetime.now().isoformat(timespec="seconds")}


def run(item: dict) -> dict:
    """Run one item's SQL now. Returns columns, rows, the chart spec to draw (or None), and any error."""
    try:
        cols, rows = tools.run_query_all(item["sql"], max_rows=MAX_ROWS)
    except Exception as e:
        return {"columns": [], "rows": [], "spec": None, "error": str(getattr(e, "orig", None) or e)[:300]}
    spec = charts.check_spec(item.get("chart"), cols) or charts.auto_spec(cols, rows)
    return {"columns": cols, "rows": rows, "spec": spec, "error": None}


# ------------------------------------------------------------------ the standalone page
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{scripts}
<style>
:root {{ color-scheme: light; --page: #EDEEEA; --card: #F7F8F5; --ink: #22282A; --ink-2: #52514e; --muted: #898781;
  --rule: rgba(11,11,11,.10); --accent: #2F5D4E; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--page); color: var(--ink); font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 32px 16px 48px; }}
.brand {{ font-size: 12px; letter-spacing: .28em; text-transform: uppercase; color: var(--accent); }}
h1 {{ font-size: 28px; font-weight: 600; margin: 4px 0 4px; }}
.sub {{ color: var(--ink-2); margin: 0 0 24px; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 12px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 520px), 1fr)); gap: 12px; }}
.card, .stat {{ background: var(--card); border: 1px solid var(--rule); border-radius: 8px; padding: 16px 18px; min-width: 0; }}
.card h2 {{ font-size: 15px; font-weight: 600; margin: 0 0 8px; unicode-bidi: plaintext; text-align: start; }}
.stat-label {{ color: var(--ink-2); font-size: 13px; unicode-bidi: plaintext; }}
.stat-value {{ font-size: 32px; font-weight: 600; margin-top: 2px; }}
.stat-caption {{ color: var(--muted); font-size: 12px; margin-top: 2px; unicode-bidi: plaintext; }}
.chart {{ width: 100%; }}
details {{ margin-top: 8px; }} summary {{ cursor: pointer; color: var(--ink-2); font-size: 13px; }}
.table-wrap {{ overflow-x: auto; max-height: 320px; overflow-y: auto; margin-top: 6px; }}
table {{ border-collapse: collapse; font-size: 13px; width: 100%; }}
th, td {{ text-align: start; padding: 5px 8px; border-bottom: 1px solid #e1e0d9; white-space: nowrap; unicode-bidi: plaintext; }}
td.n {{ text-align: end; font-variant-numeric: tabular-nums; }}
th {{ color: var(--ink-2); font-weight: 600; position: sticky; top: 0; background: var(--card); }}
pre {{ white-space: pre-wrap; font-size: 12px; color: var(--ink-2); }}
.error {{ color: #B4432F; font-size: 13px; }}
footer {{ color: var(--muted); font-size: 12px; margin-top: 24px; }}
</style></head>
<body><main>
<div class="brand">RowAsk</div>
<h1>{title}</h1>
<p class="sub">{subtitle}</p>
{stats}
<div class="grid">{cards}</div>
<footer>Exported {when}. Every number comes from the SQL shown under its chart.</footer>
</main>
<script>
const SPECS = {specs};
for (const [id, spec] of Object.entries(SPECS)) {{
  vegaEmbed("#" + id, spec, {{ actions: false, renderer: "svg" }}).catch(e => {{
    document.getElementById(id).textContent = "Chart could not be drawn: " + e.message; }});
}}
</script>
</body></html>"""


def _table(cols, rows) -> str:
    head = "".join(f"<th>{escape(str(c))}</th>" for c in cols)
    body = "".join("<tr>" + "".join(
        f'<td class="n">{v:,}</td>' if isinstance(v, (int, float)) and not isinstance(v, bool)
        else f"<td>{escape('' if v is None else str(v))}</td>" for v in r) + "</tr>" for r in rows[:200])
    more = f"<p class='stat-caption'>First 200 of {len(rows):,} rows.</p>" if len(rows) > 200 else ""
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>{more}'


def export_html(title: str, subtitle: str, items: list, results: dict) -> str:
    """One standalone HTML page for a dashboard. results: {item id: run(item)}."""
    stats, cards, specs = [], [], {}
    for item in items:
        res = results[item["id"]]
        name = escape(item["title"])
        if res["error"]:
            cards.append(f'<section class="card"><h2>{name}</h2><p class="error">{escape(res["error"])}</p></section>')
            continue
        spec = res["spec"]
        if spec and spec["type"] == "stat":
            _, label, value = charts.build(spec, res["columns"], res["rows"])
            stats.append(charts.stat_html(item["title"], value, label))
            continue
        chart = charts.build(spec, res["columns"], res["rows"]) if spec else None
        body = ""
        if chart is not None:
            cid = f"c{item['id']}"
            spec_dict = charts.style(chart.properties(width="container")).to_dict()
            specs[cid] = spec_dict
            body += f'<div class="chart" id="{cid}"></div>'
            body += f"<details><summary>Table</summary>{_table(res['columns'], res['rows'])}</details>"
        else:
            body += _table(res["columns"], res["rows"])
        body += f"<details><summary>SQL</summary><pre>{escape(item['sql'])}</pre></details>"
        cards.append(f'<section class="card"><h2>{name}</h2>{body}</section>')
    scripts = "\n".join(f'<script src="{u}"></script>' for u in CDN)
    return PAGE.format(title=escape(title), subtitle=escape(subtitle), scripts=scripts,
                       stats=f'<div class="stats">{"".join(stats)}</div>' if stats else "",
                       cards="".join(cards), when=datetime.now().strftime("%d %b %Y, %H:%M"),
                       specs=json.dumps(specs, ensure_ascii=False, default=str).replace("</", "<\\/"))
