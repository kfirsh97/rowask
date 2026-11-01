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

Every metric is checked against the database when it is first shown - its expression, filter and date column must
all resolve, as a read-only SELECT; one that doesn't is left out and reported, so a typo in a profile can't send the agent a broken definition.
"""

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
        for f in ("name", "definition", "sql", "table", "date", "filter", "unit"):
            if f in m and not isinstance(m[f], str):
                raise ValueError(f"metric {label}: {f} must be text")
        aliases = m.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise ValueError(f"metric {label}: aliases must be a list of words")
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
    """The metric as a query: its aggregate over its table, with its filter."""
    where = f" WHERE {m['filter']}" if m["filter"] else ""
    return f"SELECT {m['sql']} AS value FROM {m['table']}{where}"


def _check_sql(m: dict) -> str:
    """What proves a definition is sound without reading the table: every name in it - the expression, the filter
    and the date column - must resolve, which the database checks before running anything (WHERE 1 = 0 returns no
    rows, so even a large table is not scanned)."""
    cond = f"({m['filter']}) AND 1 = 0" if m["filter"] else "1 = 0"
    date = f", MAX({m['date']}) AS last_date" if m["date"] else ""
    return f"SELECT {m['sql']} AS value{date} FROM {m['table']} WHERE {cond}"


_ok_cache = {}                 # (database, definitions) -> metrics that passed. Failures are never cached:
                               # a dropped connection must not leave the agent without its dictionary until a restart


def checked(profile: dict) -> tuple:
    """(metrics that run on this database, [(name, error)] for the ones that don't)."""
    import db
    import tools
    ms = parse(profile.get("metrics"))
    if not ms:
        return [], []
    key = (db.database_url(), repr(ms))
    if key in _ok_cache:
        return list(_ok_cache[key]), []
    ok, bad = [], []
    for m in ms:
        try:
            tools.run_query_all(_check_sql(m), max_rows=1)
            ok.append(m)
        except Exception as e:
            bad.append((m["name"], str(getattr(e, "orig", None) or e)[:160]))
    if not bad:
        _ok_cache[key] = ok
    return ok, bad


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
