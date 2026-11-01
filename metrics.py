"""The metric dictionary: one agreed definition for each business number.

Without it, "revenue" is whatever the model decides on that day - by order date or by ship date, before or after
discount, cancelled orders in or out - so two similar questions can get two different, defensible numbers. With it,
the profile says once what each metric means, the agent computes it exactly that way, and the answer says which
definition it used. This is what the warehouse tools (Snowflake Cortex Analyst, Databricks Genie) call a semantic
model; their accuracy depends on it.

A profile defines metrics in TOML:

    [[metrics]]
    name = "Revenue"
    definition = "Sales after discount, by order date."
    sql = "SUM(Sales)"                 # the aggregate, as it goes in SELECT
    table = "superstore"
    date = "Order_Date"                # optional: the date a period filter or a trend uses
    filter = ""                        # optional: a WHERE condition that is always part of the metric
    aliases = ["sales", "הכנסות"]      # optional: other words people use for it

Every metric is checked against the database when it is first shown (it must run as a read-only SELECT); one that
doesn't is left out and reported, so a typo in a profile can't send the agent a broken definition.
"""
from functools import lru_cache

FIELDS = {"name", "definition", "sql", "table", "date", "filter", "aliases", "unit"}
REQUIRED = ("name", "definition", "sql", "table")


def parse(raw) -> list:
    """The profile's metrics as clean dicts. A malformed entry raises ValueError naming it."""
    out = []
    for i, m in enumerate(raw or [], 1):
        if not isinstance(m, dict):
            raise ValueError(f"metric #{i}: expected a [[metrics]] table")
        unknown = set(m) - FIELDS
        missing = [f for f in REQUIRED if not str(m.get(f, "")).strip()]
        label = m.get("name") or f"#{i}"
        if unknown:
            raise ValueError(f"metric {label}: unknown setting(s) {', '.join(sorted(unknown))}")
        if missing:
            raise ValueError(f"metric {label}: missing {', '.join(missing)}")
        aliases = m.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        out.append({"name": m["name"].strip(), "definition": m["definition"].strip(), "sql": m["sql"].strip(),
                    "table": m["table"].strip(), "date": (m.get("date") or "").strip(),
                    "filter": (m.get("filter") or "").strip(), "unit": (m.get("unit") or "").strip(),
                    "aliases": [a.strip() for a in aliases if str(a).strip()]})
    names = [m["name"].lower() for m in out]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise ValueError(f"metric defined twice: {', '.join(sorted(dup))}")
    return out


def probe_sql(m: dict) -> str:
    """The query that proves a metric runs: its aggregate over its table, with its filter."""
    where = f" WHERE {m['filter']}" if m["filter"] else ""
    return f"SELECT {m['sql']} AS value FROM {m['table']}{where}"


@lru_cache(maxsize=64)
def _checked(url: str, profile: str, frozen: tuple) -> tuple:
    import tools
    ok, bad = [], []
    for m in (dict(x) for x in frozen):
        m["aliases"] = list(m["aliases"])
        try:
            tools.run_query_all(probe_sql(m), max_rows=1)
            ok.append(m)
        except Exception as e:
            bad.append((m["name"], str(getattr(e, "orig", None) or e)[:160]))
    return tuple(ok), tuple(bad)


def checked(profile: dict) -> tuple:
    """(metrics that run on this database, [(name, error)] for the ones that don't). Cached per database."""
    import db
    ms = parse(profile.get("metrics"))
    if not ms:
        return [], []
    frozen = tuple(tuple(sorted({**m, "aliases": tuple(m["aliases"])}.items())) for m in ms)
    ok, bad = _checked(db.database_url(), profile.get("name", ""), frozen)
    return list(ok), list(bad)


def describe(ms: list) -> str:
    """The dictionary as the agent sees it, in get_schema."""
    if not ms:
        return ""
    lines = ["METRIC DICTIONARY - this business's agreed definitions. When a question asks for one of these "
             "(by name or alias, in any language), compute it exactly as defined here - same expression, table, "
             "date column and filter - and say so in the Assumptions line: \"Metric: <name>\". Don't redefine it."]
    for m in ms:
        also = f" (also: {', '.join(m['aliases'])})" if m["aliases"] else ""
        parts = [f"{m['sql']} FROM {m['table']}"]
        if m["filter"]:
            parts.append(f"WHERE {m['filter']}")
        if m["date"]:
            parts.append(f"dated by {m['date']}")
        unit = f" [{m['unit']}]" if m["unit"] else ""
        lines.append(f"- {m['name']}{also}{unit}: {m['definition']} SQL: {'; '.join(parts)}")
    return "\n".join(lines)
