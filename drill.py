"""Drill-down: click a bar (a region) and the chart zooms into the next level inside it (its states), and so on.

No model call: the answer's own SQL is rewritten - the clicked column is swapped for the next, finer column of the
same table, and a filter on the clicked value is added. Everything else in the query (the measure, joins, other
filters, ordering) stays as it was, so the drilled numbers add up to the bar that was clicked.

The next level is found in the data itself - a hierarchy is a column whose values nest inside the clicked one's:
in Superstore, 100% of states sit in exactly one region, 89% of cities in exactly one state, while a cross-cutting
dimension like Segment appears in every region. A profile can also spell the levels out (profiles/*.toml, `drill`).
"""
import re
from functools import lru_cache

import sqlglot
from sqlglot import exp
from sqlalchemy import inspect

import db
from dbprofile import load_profile

DIALECTS = {"mssql": "tsql", "postgresql": "postgres", "sqlite": "sqlite", "mysql": "mysql", "mariadb": "mysql"}
NESTED = 0.25          # at most this share of child values may sit under more than one parent value
MAX_LEVEL = 5000       # a level with more distinct values than this is too fine to chart
CODE = re.compile(r"(_id$|^id$|^id_|code|zip|postal|phone|email|mail|מיקוד|טלפון)", re.I)


def _dialect() -> str:
    return DIALECTS.get(db.dialect(), "sqlite")


def _q(name: str) -> str:
    return exp.to_identifier(name, quoted=True).sql(_dialect())


def _scalar(sql: str):
    return db.fetch(sql, 1)[1][0][0]


@lru_cache(maxsize=256)
def _children(url: str, table: str, column: str) -> tuple:
    """Columns of `table` that nest inside `column`, finest last - measured on the data, cached per database."""
    cols = [c for c in inspect(db.engine()).get_columns(table)]
    by_name = {c["name"].lower(): c["name"] for c in cols}
    column = by_name.get(column.lower(), column)
    for path in load_profile().get("drill") or []:                      # spelled out in the profile: use that
        names = [p.lower() for p in path]
        if column.lower() in names and names.index(column.lower()) + 1 < len(names):
            nxt = by_name.get(names[names.index(column.lower()) + 1])
            if nxt:
                return (nxt,)
    t, parent = _q(table), _q(column)
    try:
        n_parent = _scalar(f"SELECT COUNT(DISTINCT {parent}) FROM {t}")
    except Exception:
        return ()
    found = []
    for c in cols:
        name, kind = c["name"], str(c["type"]).lower()
        if name == column or CODE.search(name) or not any(k in kind for k in ("char", "text", "string", "clob")):
            continue
        child = _q(name)
        try:
            n_child = _scalar(f"SELECT COUNT(DISTINCT {child}) FROM {t}")
            if not (n_parent < n_child <= MAX_LEVEL):
                continue
            spread = _scalar(f"SELECT COUNT(*) FROM (SELECT {child} AS c FROM {t} WHERE {child} IS NOT NULL "
                             f"GROUP BY {child} HAVING COUNT(DISTINCT {parent}) > 1) s")
        except Exception:
            continue
        if spread / n_child <= NESTED:
            found.append((n_child, name))
    return tuple(name for _, name in sorted(found))


def _parse(sql: str):
    try:
        tree = sqlglot.parse_one(sql, read=_dialect())
    except Exception:
        return None
    return tree if isinstance(tree, exp.Select) else None


def _tables(tree) -> dict:
    """alias (or name) -> table name, for the tables in FROM and JOIN."""
    out = {}
    for t in tree.find_all(exp.Table):
        if t.find_ancestor(exp.Subquery):
            continue
        out[(t.alias or t.name).lower()] = t.name
    return out


def _projection(tree, x: str):
    """The SELECT item whose output column is called x, if it is a plain column (Region, z.State, ...)."""
    for item in tree.expressions:
        if item.alias_or_name.lower() == x.lower():
            col = item.this if isinstance(item, exp.Alias) else item
            return item, col if isinstance(col, exp.Column) else None
    return None, None


def next_level(sql: str, x: str):
    """Can a bar of column x in this query be drilled into? -> (table, column, child column) or None."""
    tree = _parse(sql or "")
    if tree is None or not tree.args.get("group"):
        return None
    _, col = _projection(tree, x)
    if col is None:
        return None
    tables = _tables(tree)
    table = tables.get(col.table.lower()) if col.table else (next(iter(tables.values())) if len(tables) == 1 else None)
    if not table:
        return None
    children = _children(db.database_url(), table, col.name)
    return (table, col.name, children[0]) if children else None


def rewrite(sql: str, x: str, value):
    """The query one level down: x -> its child column, filtered to x = value. Returns (sql, child) or None."""
    level = next_level(sql, x)
    if not level:
        return None
    _, _, child = level
    tree = _parse(sql)
    item, col = _projection(tree, x)
    new_col = exp.column(child, table=col.table or None, quoted=True)
    item.replace(exp.alias_(new_col, child, quoted=True))
    group = tree.args["group"]
    for g in group.expressions:                                    # GROUP BY Region (GROUP BY 1 needs nothing)
        if isinstance(g, exp.Column) and g.name.lower() == col.name.lower() and \
                (g.table or "").lower() == (col.table or "").lower():
            g.replace(new_col.copy())
    order = tree.args.get("order")
    if order:
        for o in order.expressions:
            if isinstance(o.this, exp.Column) and o.this.name.lower() in (col.name.lower(), x.lower()):
                o.this.replace(new_col.copy())
    literal = exp.Literal.number(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else exp.Literal.string(str(value))
    tree = tree.where(exp.EQ(this=exp.column(col.name, table=col.table or None, quoted=True), expression=literal))
    return tree.sql(dialect=_dialect()), child
