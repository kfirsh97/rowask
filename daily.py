"""The daily metrics report: every metric in the profile's metric dictionary, computed for one day, saved as history.

It is the kind of job a data team schedules to run every morning, and it follows the rules such a job needs:

- The day is worked out, never written in: by default it is yesterday; --day latest takes the last day that has
  data (the sample databases are historical, so "yesterday" has no sales in them); --day 2023-11-01 re-runs a
  past day.
- Running it twice is safe: the rows for a report day are replaced, never added again.
- All or nothing: if any metric fails, nothing is written and the run exits with an error (a red run in GitHub
  Actions, which emails the repository owner). The history file is replaced in one step, so a crash halfway
  can't leave half a file.
- Every number comes from the metric dictionary (metrics.py), so the report and the agent's answers use the same
  definitions, and every query goes through the same read-only validator as the agent's.

For each metric with a date column it reports the day itself, the last 30 days, the 30 days before that and the
change between them; a change of --alert-pct or more is flagged. A metric without a date column gets its all-time
value only.

Output, per profile:  reports/<profile>/history.csv  (one row per day and metric)
                      reports/<profile>/latest.md    (the latest report as a table; GitHub shows it)

Run:  python daily.py --profile porsche --day latest
Scheduled: .github/workflows/daily-report.yml runs it every morning at 07:00 Israel time.
"""
import argparse
import csv
import datetime as dt
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIELDS = ["report_day", "metric", "unit", "day", "last_30_days", "previous_30_days", "change_pct", "all_time"]


class ReportError(RuntimeError):
    """The report can't be trusted for this day; nothing was written."""


def _value(sql: str):
    import tools
    _, rows = tools.run_query_all(sql, max_rows=2)
    v = rows[0][0] if rows else None
    return None if v is None else round(float(v), 2)


def _where(m: dict, start: dt.date = None, end: dt.date = None) -> str:
    """The metric's own filter plus a date window [start, end). Dates are generated here, never taken from input."""
    conds = [f"({m['filter']})"] if m["filter"] else []
    if start:
        conds.append(f"{m['date']} >= '{start.isoformat()}'")
    if end:
        conds.append(f"{m['date']} < '{end.isoformat()}'")
    return f" WHERE {' AND '.join(conds)}" if conds else ""


def latest_day(ms: list) -> dt.date:
    """The last day with data in any dated metric's table."""
    import tools
    days = []
    for m in ms:
        if m["date"]:
            _, rows = tools.run_query_all(f"SELECT MAX({m['date']}) FROM {m['table']}{_where(m)}", max_rows=2)
            if rows and rows[0][0]:
                days.append(dt.date.fromisoformat(str(rows[0][0])[:10]))
    if not days:
        raise ReportError("No metric has a date column with data, so there is no latest day.")
    return max(days)


def compute(profile: dict, day, alert_pct: float = 50) -> dict:
    """{"day", "rows", "alerts"} for one report day. Raises ReportError if any metric doesn't run."""
    import metrics
    ok, bad = metrics.checked(profile)
    if bad:
        raise ReportError("; ".join(f"{name}: {err}" for name, err in bad))
    if not ok:
        raise ReportError(f"Profile {profile['name']} has no metric dictionary to report on.")
    if day in (None, "yesterday"):
        day = dt.date.today() - dt.timedelta(days=1)
    elif day == "latest":
        day = latest_day(ok)
    elif isinstance(day, str):
        day = dt.date.fromisoformat(day)
    rows, alerts = [], []
    next_day = day + dt.timedelta(days=1)
    for m in ok:
        base = f"SELECT {m['sql']} FROM {m['table']}"
        try:
            row = {"report_day": day.isoformat(), "metric": m["name"], "unit": m["unit"],
                   "all_time": _value(base + _where(m)),
                   "day": None, "last_30_days": None, "previous_30_days": None, "change_pct": None}
            if m["date"]:
                row["day"] = _value(base + _where(m, day, next_day))
                row["last_30_days"] = _value(base + _where(m, next_day - dt.timedelta(days=30), next_day))
                row["previous_30_days"] = _value(base + _where(m, next_day - dt.timedelta(days=60),
                                                               next_day - dt.timedelta(days=30)))
        except Exception as e:
            raise ReportError(f"{m['name']}: {str(getattr(e, 'orig', None) or e)[:200]}") from e
        last, prev = row["last_30_days"], row["previous_30_days"]
        if last is not None and prev:
            row["change_pct"] = round(100 * (last - prev) / abs(prev), 1)
            if abs(row["change_pct"]) >= alert_pct:
                alerts.append(f"{m['name']} changed {row['change_pct']:+.1f}% against the 30 days before.")
        elif prev and last is None:
            alerts.append(f"{m['name']} has no data in the last 30 days, but had some in the 30 days before.")
        rows.append(row)
    return {"day": day, "rows": rows, "alerts": alerts}


def save(rows: list, path: Path) -> None:
    """Add the day's rows to the history, replacing any earlier run for the same day, in one atomic step."""
    days = {r["report_day"] for r in rows}
    old = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            old = [r for r in csv.DictReader(f) if r["report_day"] not in days]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(old + rows, key=lambda r: r["report_day"]):          # stable: metric order kept
            w.writerow({k: "" if r.get(k) is None else r[k] for k in FIELDS})
    os.replace(tmp, path)


def _fmt(v, unit: str) -> str:
    if v in (None, ""):
        return "-"
    v = float(v)
    if unit == "USD":
        return f"${v:,.0f}"
    if unit == "%":
        return f"{v:.1f}%"
    return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"


def markdown(profile: dict, report: dict) -> str:
    out = [f"# Daily metrics: {profile.get('title') or profile['name']}", "",
           f"Report day: **{report['day'].isoformat()}**. Every number is computed exactly as the profile's metric "
           "dictionary defines it.", "",
           "| Metric | That day | Last 30 days | 30 days before | Change | All time |",
           "|---|---:|---:|---:|---:|---:|"]
    for r in report["rows"]:
        u = r["unit"]
        change = "-" if r["change_pct"] is None else f"{r['change_pct']:+.1f}%"
        out.append(f"| {r['metric']} | {_fmt(r['day'], u)} | {_fmt(r['last_30_days'], u)} | "
                   f"{_fmt(r['previous_30_days'], u)} | {change} | {_fmt(r['all_time'], u)} |")
    out.append("")
    out += [f"- Alert: {a}" for a in report["alerts"]] or ["- No alerts."]
    return "\n".join(out) + "\n"


def run(profile_name: str, day="yesterday", out_dir: Path = HERE / "reports", alert_pct: float = 50) -> dict:
    os.environ["DB_PROFILE"] = profile_name
    import db
    import dbprofile
    dbprofile.load_profile.cache_clear(); db._engine_for.cache_clear()
    profile = dbprofile.load_profile()
    report = compute(profile, day, alert_pct)
    folder = Path(out_dir) / profile["name"]
    save(report["rows"], folder / "history.csv")
    text = markdown(profile, report)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "latest.md").write_text(text, encoding="utf-8")
    report["markdown"] = text
    return report


def _summary(text: str) -> None:
    """In GitHub Actions, the report also appears on the run's page."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--profile", default=os.environ.get("DB_PROFILE") or "porsche")
    p.add_argument("--day", default="yesterday", help="yesterday (default), latest, or YYYY-MM-DD")
    p.add_argument("--out", default=str(HERE / "reports"))
    p.add_argument("--alert-pct", type=float, default=50, help="flag a 30-day change of this many percent or more")
    a = p.parse_args(argv)
    try:
        report = run(a.profile, a.day, Path(a.out), a.alert_pct)
    except Exception as e:                                # a ReportError, a bad --day, an unreachable database
        msg = f"Daily report for {a.profile} failed, nothing was written: {e}"
        print(msg, file=sys.stderr)
        _summary(f"**{msg}**")
        return 1
    print(report["markdown"])
    _summary(report["markdown"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
