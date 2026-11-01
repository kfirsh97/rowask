"""Analyses beyond SQL: forecast, comparing groups, correlation and unusual values.

The agent never writes or runs code here. It picks one analysis from a fixed list and names the columns of a query
it wrote; the query goes through the same read-only checks as any other, and the statistics are code written and
tested in advance. On a public site that matters: an agent that ran its own Python could be talked into running
anyone's. It also keeps the numbers honest - the model reports a p-value, it doesn't estimate one.

Every result has a plain-language `summary` (what the agent tells the user), the numbers behind it, and `warnings`
when the data is too thin to trust the result.
"""
import math

import numpy as np
import pandas as pd

KINDS = ("forecast", "compare_groups", "correlation", "outliers")
ALPHA = 0.05
MAX_FORECAST = 24


class AnalysisError(ValueError):
    """The analysis can't run on this data - the message says why, so the agent can fix its query."""


MAX_ROWS = 100_000


def _frame(sql: str) -> pd.DataFrame:
    import tools
    cols, rows = tools.run_query_all(sql, max_rows=MAX_ROWS + 1)
    if not rows:
        raise AnalysisError("The query returned no rows.")
    if len(rows) > MAX_ROWS:
        raise AnalysisError(f"The query returned more than {MAX_ROWS:,} rows. Narrow it (a period, a sample of "
                            "groups) - the analysis would otherwise see only part of the data.")
    return pd.DataFrame([list(r) for r in rows], columns=cols)


def _column(df: pd.DataFrame, name: str, role: str) -> str:
    if not name:
        raise AnalysisError(f"Name the {role} column.")
    cols = [str(c) for c in df.columns]
    if name in cols:                                    # exact first: Sales and sales can both be columns
        return name
    same = [c for c in cols if c.lower() == str(name).lower()]
    found = same[0] if len(same) == 1 else None
    if len(same) > 1:
        raise AnalysisError(f"'{name}' matches more than one column ({', '.join(same)}) - use the exact name.")
    if not found:
        raise AnalysisError(f"The query has no column '{name}' (it has: {', '.join(map(str, df.columns))}).")
    return found


def _numbers(df, col) -> pd.Series:
    v = pd.to_numeric(df[col], errors="coerce")
    if v.notna().sum() == 0:
        raise AnalysisError(f"Column '{col}' has no numbers.")
    return v


def _p(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"


# ------------------------------------------------------------------ forecast
def _periods(labels: pd.Series):
    """('month' | 'year', sortable period index) for labels like 2025-03, 2025, or dates on the 1st of a month."""
    text = labels.astype(str).str.strip().str.replace(r"^((?:19|20)\d\d)\.0+$", r"\1", regex=True)   # 2023.0
    if text.str.fullmatch(r"(19|20)\d\d").all():
        return "year", pd.PeriodIndex(text.astype(int), freq="Y")
    if text.str.fullmatch(r"(19|20)\d\d-\d\d").all():
        return "month", pd.PeriodIndex(text, freq="M")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dates = pd.to_datetime(text, errors="coerce")
    if dates.notna().all() and (dates.dt.day == 1).all():
        return "month", pd.PeriodIndex(dates, freq="M")
    raise AnalysisError(f"A forecast needs one row per month (2025-03) or per year (2025); the period column has "
                        f"values like {', '.join(text.head(3))}. Group the query by month or year, e.g. "
                        "strftime('%Y-%m', date) in SQLite, FORMAT(date, 'yyyy-MM') in SQL Server, "
                        "to_char(date, 'YYYY-MM') in PostgreSQL.")


def forecast(df, x, y, periods=3):
    x, y = _column(df, x, "date"), _column(df, y, "value")
    periods = max(1, min(int(periods or 3), MAX_FORECAST))
    kind, idx = _periods(df[x])
    values = _numbers(df, y)
    s = pd.Series(values.to_numpy(dtype=float), index=idx)
    if s.isna().any():                  # an empty value is not a zero: refuse rather than invent a drop
        missing = ", ".join(str(p) for p in s[s.isna()].index[:5])
        raise AnalysisError(f"{y} is empty for {missing}. Fill those {kind}s in the query (COALESCE(..., 0) if zero "
                            "is really right) or leave them out of the range.")
    s = s.groupby(level=0).sum().sort_index()
    full = pd.period_range(s.index.min(), s.index.max(), freq=s.index.freq)
    warnings = []
    if len(full) != len(s):
        gaps = ", ".join(str(p) for p in full.difference(s.index)[:5])
        raise AnalysisError(f"The history has gaps ({gaps}): those {kind}s have no row. If nothing happened then, "
                            "return them with 0; otherwise forecast from a range without gaps.")
    need = 6 if kind == "month" else 4
    if len(s) < need:
        raise AnalysisError(f"A forecast needs at least {need} {kind}s of history; the query has {len(s)}.")
    seasonal = kind == "month" and len(s) >= 24
    method = "Holt-Winters exponential smoothing (trend + 12-month seasonality)" if seasonal else \
        "Holt's exponential smoothing (trend)"
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        model = ExponentialSmoothing(s.to_numpy(), trend="add", seasonal="add" if seasonal else None,
                                     seasonal_periods=12 if seasonal else None,
                                     initialization_method="estimated").fit()
        fitted, ahead = model.fittedvalues, model.forecast(periods)
    except Exception:
        method = "linear trend"
        t = np.arange(len(s))
        slope, intercept = np.polyfit(t, s.to_numpy(), 1)
        fitted = intercept + slope * t
        ahead = intercept + slope * np.arange(len(s), len(s) + periods)
    resid = s.to_numpy() - np.asarray(fitted)
    # The spread of the model's misses on the history, with a floor: a near-perfect fit would otherwise promise a
    # range of zero, which no real future has.
    sigma = max(float(np.sqrt(np.mean(resid ** 2))), 0.02 * float(np.mean(np.abs(s.to_numpy()))))
    never_negative = bool((s >= 0).all())
    future = pd.period_range(s.index.max() + 1, periods=periods, freq=s.index.freq)
    rows = []
    for h, (p, v) in enumerate(zip(future, ahead), 1):
        band = 1.96 * sigma * math.sqrt(h)                     # ~95%, widening the further ahead
        low = float(v) - band
        if never_negative:              # sales, counts: a history that never went below zero won't start now
            low = max(low, 0.0)
        rows.append({"period": str(p), "forecast": round(float(v), 2),
                     "low": round(low, 2), "high": round(float(v) + band, 2)})
    if len(s) < 2 * need:
        warnings.append(f"Only {len(s)} {kind}s of history - treat the forecast as a rough direction.")
    if not seasonal and kind == "month":
        warnings.append("Less than 24 months of history, so seasonality (e.g. a strong December) is not modelled.")
    first, last = rows[0], rows[-1]
    summary = (f"Forecast of {y} for the next {periods} {kind}{'s' if periods > 1 else ''} ({method}): "
               f"{first['period']} about {_fmt(first['forecast'])} (95% range {_fmt(first['low'])} to "
               f"{_fmt(first['high'])})" + (f", {last['period']} about {_fmt(last['forecast'])} (range "
                                            f"{_fmt(last['low'])} to {_fmt(last['high'])})" if periods > 1 else "") +
               f". Based on {len(s)} {kind}s from {s.index.min()} to {s.index.max()}.")
    history = [{"period": str(p), "actual": round(float(v), 2)} for p, v in s.items()]
    return {"kind": "forecast", "x": x, "y": y, "unit": kind, "method": method, "history": history,
            "forecast": rows, "summary": summary, "warnings": warnings}


# ------------------------------------------------------------------ compare groups
def compare_groups(df, group, value):
    from scipy import stats
    g, v = _column(df, group, "group"), _column(df, value, "value")
    data = pd.DataFrame({"g": df[g], "v": _numbers(df, v)})
    dropped = int(data.isna().any(axis=1).sum())          # no group, or no value: not an observation of anything
    data = data.dropna()
    data["g"] = data["g"].astype(str)
    groups = {k: s.to_numpy(dtype=float) for k, s in data.groupby("g")["v"]}
    if len(groups) < 2:
        raise AnalysisError("Comparing needs at least two groups. The query should return one row per observation "
                            "(e.g. per order) with its group, not one row per group.")
    if len(groups) > 20:
        raise AnalysisError(f"{len(groups)} groups is too many to compare at once - pick the ones that matter.")
    small = [k for k, a in groups.items() if len(a) < 3]
    if small:
        raise AnalysisError(f"Group(s) {', '.join(small)} have fewer than 3 rows. The query should return one row per "
                            "observation, not totals per group.")
    table = [{"group": k, "n": len(a), "mean": round(float(a.mean()), 2), "median": round(float(np.median(a)), 2),
              "std": round(float(a.std(ddof=1)), 2)} for k, a in groups.items()]
    arrays = list(groups.values())
    warnings = [f"Group {t['group']} has only {t['n']} rows." for t in table if t["n"] < 20]
    if dropped:
        warnings.append(f"{dropped:,} rows with no group or no value were left out.")
    if all(a.std() == 0 for a in arrays):                 # every group is constant: no test is needed or possible
        means = {k: float(a[0]) for k, a in groups.items()}
        same = len(set(means.values())) == 1
        summary = ("Every row has the same value - there is no difference to test." if same else
                   f"Within each group every row has the same {v} ({', '.join(f'{k} {_fmt(m)}' for k, m in means.items())}),"
                   " so the groups differ completely; a significance test doesn't apply.")
        return {"kind": "compare_groups", "group": g, "value": v, "groups": table, "tests": {},
                "significant": not same, "summary": summary, "warnings": warnings}
    if len(groups) == 2:
        (na, a), (nb, b) = groups.items()
        t = stats.ttest_ind(a, b, equal_var=False)
        u = stats.mannwhitneyu(a, b, alternative="two-sided")
        pooled = math.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
        d = (a.mean() - b.mean()) / pooled                    # pooled > 0 here: not every group is constant
        p_main, p_rank = float(t.pvalue), float(u.pvalue)
        tests = {"welch_t_test_p": p_main, "mann_whitney_p": p_rank, "cohens_d": round(float(d), 3)}
        diff = a.mean() - b.mean()
        rel = f" ({diff / abs(b.mean()):+.1%})" if b.mean() else ""
        size = "negligible" if abs(d) < 0.2 else "small" if abs(d) < 0.5 else "medium" if abs(d) < 0.8 else "large"
        head = (f"Average {v}: {na} {_fmt(a.mean())} vs {nb} {_fmt(b.mean())}, a difference of {_fmt(diff)}{rel}.")
    else:
        kw = stats.kruskal(*arrays)
        try:
            main = stats.alexandergovern(*arrays)          # ANOVA that does not assume equal variances
            p_main, name = float(main.pvalue), "alexander_govern_p"
        except Exception:
            main = stats.f_oneway(*arrays)
            p_main, name = float(main.pvalue), "anova_p"
        p_rank = float(kw.pvalue)
        tests = {name: p_main, "kruskal_wallis_p": p_rank}
        top = max(table, key=lambda r: r["mean"])
        low = min(table, key=lambda r: r["mean"])
        head = f"Average {v} ranges from {_fmt(low['mean'])} ({low['group']}) to {_fmt(top['mean'])} ({top['group']})."
        size = None
    if math.isnan(p_main) or math.isnan(p_rank):        # e.g. one group constant: use the test that is defined
        p_main = p_rank if math.isnan(p_main) else p_main
        p_rank = p_main if math.isnan(p_rank) else p_rank
    both = p_main < ALPHA and p_rank < ALPHA
    neither = p_main >= ALPHA and p_rank >= ALPHA
    verdict = ("The difference is statistically significant" if both else
               "The difference could well be chance - not statistically significant" if neither else
               "The evidence is mixed - one test finds a difference and the other doesn't")
    summary = (f"{head} {verdict} ({_p(p_main)} on the means, {_p(p_rank)} on the ranks, at the 5% level)" +
               (f"; the effect size is {size} (Cohen's d = {tests['cohens_d']})" if size else "") + ".")
    return {"kind": "compare_groups", "group": g, "value": v, "groups": table, "tests": tests,
            "significant": both, "summary": summary, "warnings": warnings}


# ------------------------------------------------------------------ correlation
def correlation(df, x, y):
    from scipy import stats
    x, y = _column(df, x, "first"), _column(df, y, "second")
    data = pd.DataFrame({"x": _numbers(df, x), "y": _numbers(df, y)}).dropna()
    if len(data) < 5:
        raise AnalysisError(f"A correlation needs at least 5 rows with both values; the query has {len(data)}.")
    if data["x"].nunique() < 2 or data["y"].nunique() < 2:
        raise AnalysisError("One of the columns has the same value in every row.")
    pr, sr = stats.pearsonr(data["x"], data["y"]), stats.spearmanr(data["x"], data["y"])
    r, rho = float(pr.statistic), float(sr.statistic)
    def word(c):
        return "very weak" if abs(c) < 0.1 else "weak" if abs(c) < 0.3 else "moderate" if abs(c) < 0.5 else "strong"
    # Pearson measures a straight line, Spearman any steady rise or fall; when they disagree, say both.
    lead = rho if abs(rho) > abs(r) else r
    direction = "goes up" if lead > 0 else "goes down"
    sig = min(pr.pvalue, sr.pvalue) < ALPHA
    shape = (f"{word(lead)} {'positive' if lead > 0 else 'negative'} relationship" if word(r) == word(rho) else
             f"{word(rho)} {'positive' if rho > 0 else 'negative'} relationship in rank order, {word(r)} as a straight "
             "line")
    summary = (f"{shape[0].upper() + shape[1:]} between {x} and {y} (Pearson r = {r:.2f}, {_p(pr.pvalue)}; Spearman "
               f"rho = {rho:.2f}, {_p(sr.pvalue)}; {len(data):,} rows): as {x} rises, {y} {direction}" +
               ("" if sig else ", though this could be chance") +
               ". A correlation shows the two move together, not that one causes the other.")
    return {"kind": "correlation", "x": x, "y": y, "n": len(data), "pearson_r": round(r, 4), "pearson_p": float(pr.pvalue),
            "spearman_rho": round(rho, 4), "spearman_p": float(sr.pvalue), "significant": bool(sig),
            "summary": summary, "warnings": [] if len(data) >= 30 else [f"Only {len(data)} rows."]}


# ------------------------------------------------------------------ unusual values
def outliers(df, label, value):
    lab, val = _column(df, label, "label"), _column(df, value, "value")
    data = pd.DataFrame({"label": df[lab].where(df[lab].notna(), "(no name)").astype(str),
                         "value": _numbers(df, val)}).dropna()
    if len(data) < 8:
        raise AnalysisError(f"Finding unusual values needs at least 8 rows; the query has {len(data)}.")
    med = float(data["value"].median())
    mad = float((data["value"] - med).abs().median())
    if mad > 0:
        data["score"] = 0.6745 * (data["value"] - med) / mad            # robust z-score (Iglewicz & Hoaglin)
        rule, flagged = "a robust z-score above 3.5 (median and MAD)", data[data["score"].abs() > 3.5]
    else:
        q1, q3 = data["value"].quantile([0.25, 0.75])
        iqr = q3 - q1 or 1.0
        data["score"] = (data["value"] - med) / iqr
        rule = "more than 3 IQRs from the middle"
        flagged = data[(data["value"] < q1 - 3 * iqr) | (data["value"] > q3 + 3 * iqr)]
    flagged = flagged.reindex(flagged["score"].abs().sort_values(ascending=False).index)
    items = [{"label": r.label, "value": round(float(r.value), 2), "direction": "high" if r.score > 0 else "low",
              "score": round(float(r.score), 1)} for r in flagged.head(20).itertuples()]
    if items:
        shown = ", ".join(f"{i['label']} ({_fmt(i['value'])}, {i['direction']})" for i in items[:5])
        summary = (f"{len(flagged)} of {len(data):,} rows are unusual by {rule}; the typical {val} is "
                   f"{_fmt(med)}. Most extreme: {shown}.")
    else:
        summary = f"No unusual values among {len(data):,} rows by {rule}; the typical {val} is {_fmt(med)}."
    return {"kind": "outliers", "label": lab, "value": val, "median": round(med, 2), "rule": rule,
            "count": len(flagged), "rows": len(data), "unusual": items, "summary": summary, "warnings": []}


# ------------------------------------------------------------------ entry point
def run(args: dict) -> dict:
    """Run one analysis from the agent's arguments. Returns the result, or {"error": ...} it can act on."""
    kind = (args.get("kind") or "").strip()
    if kind not in KINDS:
        return {"error": f"Unknown analysis '{kind}'. Choose one of: {', '.join(KINDS)}."}
    try:
        df = _frame(args.get("sql") or "")
        if kind == "forecast":
            return forecast(df, args.get("x"), args.get("y"), args.get("periods") or 3)
        if kind == "compare_groups":
            return compare_groups(df, args.get("group"), args.get("value"))
        if kind == "correlation":
            return correlation(df, args.get("x"), args.get("y"))
        return outliers(df, args.get("label"), args.get("value"))
    except AnalysisError as e:
        return {"error": str(e)}
    except Exception as e:
        import tools
        if isinstance(e, ValueError) and str(e).startswith("Query rejected"):   # refused by the read-only checks
            return {"error": str(e)}
        if type(e).__module__.split(".")[0] in ("sqlite3", "sqlalchemy", "pymssql", "psycopg", "pymysql"):
            return {"error": tools._clean_error(e)}                             # the database's own message
        return {"error": f"The analysis failed: {str(e)[:300]}"}
