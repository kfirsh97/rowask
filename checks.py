"""Automatic checks on an answer's query - the silent mistakes: the query runs, the number looks fine, it is wrong.

  joins    A join that repeats rows. Sales JOIN Customers is safe when each customer appears once in Customers;
           if the joined column repeats there, every sale is counted once per repeat and SUM() is inflated.
  missing  Empty values in the columns the answer adds up or groups by. SUM and AVG skip them without a word.
  period   An incomplete last period. If the data ends on 14 March, "March" and "this year" are partial, and a
           drop next to the full months is an artifact, not a trend.

Each check queries the database itself (counts only, cached per database), so it needs no model call.
The agent gets the warnings with its query result and can fix the query; the page shows them under the answer.
"""
import re
from datetime import timedelta
from functools import lru_cache

import pandas as pd
import sqlglot
from sqlglot import exp
from sqlalchemy import inspect

import db
from drill import DIALECTS

MISSING_SHARE = 0.01          # warn when at least 1% of a used column is empty
GRACE = {"month": 3, "year": 7}   # a period ending this many days before its end still counts as complete


def _dialect() -> str:
    return DIALECTS.get(db.dialect(), "sqlite")


def _q(name: str) -> str:
    return exp.to_identifier(name, quoted=True).sql(_dialect())


def _scalar_row(sql: str):
    return db.fetch(sql, 1)[1][0]


@lru_cache(maxsize=64)
def _columns(url: str, table: str) -> dict:
    """lower-case name -> (real name, type) for one table."""
    try:
        return {c["name"].lower(): (c["name"], str(c["type"]).lower()) for c in inspect(db.engine()).get_columns(table)}
    except Exception:
        return {}


@lru_cache(maxsize=512)
def _repeats(url: str, table: str, column: str) -> int:
    """How many non-empty values of table.column are repeats (0 = the column is unique)."""
    n, distinct = _scalar_row(f"SELECT COUNT({_q(column)}), COUNT(DISTINCT {_q(column)}) FROM {_q(table)}")
    return int(n or 0) - int(distinct or 0)


@lru_cache(maxsize=512)
def _empty(url: str, table: str, column: str):
    """(rows, empty rows) of table.column."""
    n, filled = _scalar_row(f"SELECT COUNT(*), COUNT({_q(column)}) FROM {_q(table)}")
    return int(n or 0), int(n or 0) - int(filled or 0)


def _last_date_in(select, col):
    """The last date the query itself can see: MAX(col) under the query's own FROM, joins and WHERE."""
    q = select.copy()
    q.set("expressions", [exp.Max(this=col.copy())])
    for part in ("group", "order", "limit", "having", "distinct", "offset"):
        q.set(part, None)
    value = _scalar_row(q.sql(dialect=_dialect()))[0]
    stamp = pd.to_datetime(value, errors="coerce") if value is not None else None
    return None if stamp is None or pd.isna(stamp) else stamp


# ------------------------------------------------------------------ reading the query
class Scope:
    """One SELECT: its real tables by alias, so a column can be traced to the table it comes from."""

    def __init__(self, select, ctes):
        self.select = select
        self.tables = {}
        for t in [select.args.get("from") or select.args.get("from_")] + list(select.args.get("joins") or []):
            node = t.this if t is not None else None
            if isinstance(node, exp.Table) and node.name.lower() not in ctes:
                self.tables[(node.alias or node.name).lower()] = node.name

    def table_of(self, col: exp.Column):
        url = db.database_url()
        if col.table:
            table = self.tables.get(col.table.lower())
            return table if table and col.name.lower() in _columns(url, table) else None
        owners = [t for t in set(self.tables.values()) if col.name.lower() in _columns(url, t)]
        return owners[0] if len(owners) == 1 else None

    def real_name(self, table, column):
        return _columns(db.database_url(), table).get(column.lower(), (column, ""))[0]


def _scopes(tree):
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    return [Scope(s, ctes) for s in tree.find_all(exp.Select)]


def _measures(scope):
    """(table, column, function) for every SUM/AVG/COUNT over a column of a real table; COUNT(*) -> the FROM table."""
    out = []
    for agg in scope.select.find_all(exp.AggFunc):
        if agg.find_ancestor(exp.Select) is not scope.select or isinstance(agg.this, exp.Distinct):
            continue
        name = type(agg).__name__.upper()
        if isinstance(agg.this, exp.Star) or agg.this is None:
            base = scope.select.args.get("from") or scope.select.args.get("from_")
            if base is not None and isinstance(base.this, exp.Table) and \
                    (base.this.alias or base.this.name).lower() in scope.tables:
                out.append((scope.tables[(base.this.alias or base.this.name).lower()], None, f"{name}(*)"))
            continue
        for col in agg.find_all(exp.Column):
            table = scope.table_of(col)
            if table:
                out.append((table, scope.real_name(table, col.name), f"{name}({col.name})"))
    return out


# ------------------------------------------------------------------ the checks
def _join_check(scopes):
    url, found, checked = db.database_url(), [], False
    for scope in scopes:
        measures = _measures(scope)
        measure_tables = {t for t, _, _ in measures}
        for join in scope.select.args.get("joins") or []:
            on = join.args.get("on")
            if on is None:
                continue
            for eq in on.find_all(exp.EQ):
                left, right = eq.this, eq.expression
                if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                    continue
                for a, b in ((left, right), (right, left)):
                    ta, tb = scope.table_of(a), scope.table_of(b)
                    if not ta or not tb or ta == tb or ta not in measure_tables or tb in measure_tables:
                        continue
                    checked = True
                    column = scope.real_name(tb, b.name)
                    repeats = _repeats(url, tb, column)
                    if repeats:
                        agg = next(f for t, _, f in measures if t == ta)
                        found.append(f"Joining {tb} on {column} can repeat {ta} rows: {column} is not unique in {tb} "
                                     f"({repeats:,} repeated values), so {agg} may count some rows more than once.")
    if found:
        return [{"check": "joins", "ok": False, "text": t} for t in dict.fromkeys(found)]
    return [{"check": "joins", "ok": True, "text": "Joins don't repeat rows"}] if checked else []


def _missing_check(scopes):
    url, found, seen = db.database_url(), [], set()
    for scope in scopes:
        used = [(t, c, f) for t, c, f in _measures(scope) if c]
        for g in (scope.select.args.get("group").expressions if scope.select.args.get("group") else []):
            if isinstance(g, exp.Column) and scope.table_of(g):
                t = scope.table_of(g)
                used.append((t, scope.real_name(t, g.name), None))
        for table, column, agg in used:
            if (table, column) in seen:
                continue
            seen.add((table, column))
            n, empty = _empty(url, table, column)
            if n and empty / n >= MISSING_SHARE:
                what = f"{agg.split('(')[0]} leaves them out" if agg else "they form a group with no name"
                found.append(f"{column} is empty in {empty / n:.0%} of {table} rows ({empty:,}) - {what}.")
    if found:
        return [{"check": "missing", "ok": False, "text": t} for t in found]
    return [{"check": "missing", "ok": True, "text": "No missing values in the columns used"}] if seen else []


def _granularity(series):
    """'year' for 2025-like numbers, 'month' for '2025-03'-like text, else None."""
    vals = pd.Series(series).dropna()
    if vals.empty:
        return None
    text = vals.astype(str).str.strip()
    if text.str.fullmatch(r"(19|20)\d\d").all():
        return "year"
    if text.str.fullmatch(r"(19|20)\d\d-\d\d").all():
        return "month"
    return None


def _period_check(tree, scope, columns, rows):
    if not rows or not columns:
        return []
    df = pd.DataFrame([list(r) for r in rows], columns=columns)
    url = db.database_url()
    for item in tree.expressions if isinstance(tree, exp.Select) else []:
        name = item.alias_or_name
        if name not in df.columns:
            continue
        kind = _granularity(df[name])
        month_col = None
        if kind == "year":                       # a year next to a month number: the period is the month
            for other in df.columns:
                if other != name and pd.to_numeric(df[other], errors="coerce").between(1, 12).all() \
                        and any(w in other.lower() for w in ("month", "חודש")):
                    month_col, kind = other, "month"
        if not kind:
            continue
        last = None
        for col in item.find_all(exp.Column):
            if scope.table_of(col):
                last = _last_date_in(scope.select, col)
                if last is not None:
                    break
        if last is None:
            continue
        if kind == "year":
            period, end, shown = str(last.year), pd.Timestamp(last.year, 12, 31), df[name].astype(str).str.strip()
            present = (shown == period).any()
        else:
            period = f"{last.year}-{last.month:02d}"
            end = (pd.Timestamp(last.year, last.month, 1) + pd.offsets.MonthEnd(1))
            if month_col:
                present = ((df[name].astype(str).str.strip() == str(last.year)) &
                           (pd.to_numeric(df[month_col], errors="coerce") == last.month)).any()
            else:
                present = (df[name].astype(str).str.strip() == period).any()
        if not present:
            return []
        if end - last > timedelta(days=GRACE[kind]):
            return [{"check": "period", "ok": False,
                     "text": f"The data ends on {last:%d %b %Y}, so {period} is not complete - "
                             f"compare it with the other {kind}s with care."}]
        return [{"check": "period", "ok": True, "text": f"The last {kind} is complete"}]
    return []


def run(sql: str, columns=None, rows=None) -> list:
    """Checks for one query (and its result, for the period check). [{check, ok, text}]; [] if nothing applies."""
    try:
        tree = sqlglot.parse_one(sql or "", read=_dialect())
    except Exception:
        return []
    if not isinstance(tree, (exp.Select, exp.Union)):
        return []
    out = []
    scopes = _scopes(tree)
    for check in (lambda: _join_check(scopes), lambda: _missing_check(scopes),
                  lambda: _period_check(tree, scopes[0], columns, rows) if scopes else []):
        try:
            out += check()
        except Exception:
            continue                             # a check that can't run says nothing, rather than something wrong
    return out


def warnings(sql, columns=None, rows=None) -> list:
    return [c["text"] for c in run(sql, columns, rows) if not c["ok"]]


ASSUMPTIONS = re.compile(r"^\W*(Assumptions|הנחות)\W*:\W*\s*(.+)$", re.M | re.I)


def split_assumptions(text):
    """The answer without its 'Assumptions:' line, and that line as (label, text) - shown in its own box."""
    m = ASSUMPTIONS.search(text or "")
    if not m:
        return text, None
    return (text[:m.start()] + text[m.end():]).strip(), (m.group(1) + ":", m.group(2).strip(" *_"))
