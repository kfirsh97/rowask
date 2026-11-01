"""Analyses beyond SQL: forecast, comparing groups, correlation and unusual values.

The agent never writes or runs code here. It picks one analysis from a fixed list and names the columns of a query
it wrote; the query goes through the same read-only checks as any other, and the statistics are code written and
tested in advance. On a public site that matters: an agent that ran its own Python could be talked into running
anyone's. It also keeps the numbers honest - the model reports a p-value, it doesn't estimate one.

Every result has a plain-language `summary` (what the agent tells the user), the numbers behind it, and `warnings`
when the data is too thin to trust the result.
"""
import math
import re

import numpy as np
import pandas as pd

KINDS = ("forecast", "compare_groups", "correlation", "outliers")
ALPHA = 0.05
MAX_FORECAST = 24


class AnalysisError(ValueError):
    """The analysis can't run on this data - the message says why, so the agent can fix its query."""


def _frame(sql: str) -> pd.DataFrame:
    import tools
    cols, rows = tools.run_query_all(sql, max_rows=100_000)
    if not rows:
        raise AnalysisError("The query returned no rows.")
    return pd.DataFrame([list(r) for r in rows], columns=cols)


def _column(df: pd.DataFrame, name: str, role: str) -> str:
    if not name:
        raise AnalysisError(f"Name the {role} column.")
    found = {c.lower(): c for c in df.columns}.get(str(name).lower())
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
    text = labels.astype(str).str.strip()
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
    raise AnalysisError("A forecast needs one row per month (2025-03) or per year (2025). Group the query by month "
                        "or year, e.g. strftime('%Y-%m', date) in SQLite or FORMAT(date, 'yyyy-MM') in SQL Server.")


def forecast(df, x, y, periods=3):
    x, y = _column(df, x, "date"), _column(df, y, "value")
    periods = max(1, min(int(periods or 3), MAX_FORECAST))
    kind, idx = _periods(df[x])
    values = _numbers(df, y)
    s = pd.Series(values.to_numpy(dtype=float), index=idx).groupby(level=0).sum().sort_index()
    full = pd.period_range(s.index.min(), s.index.max(), freq=s.index.freq)
    warnings = []
    if len(full) != len(s):
        warnings.append(f"{len(full) - len(s)} {kind}(s) had no rows and were counted as 0.")
        s = s.reindex(full, fill_value=0.0)
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
    sigma = float(np.sqrt(np.mean(resid ** 2)))
    future = pd.period_range(s.index.max() + 1, periods=periods, freq=s.index.freq)
    rows = []
    for h, (p, v) in enumerate(zip(future, ahead), 1):
        band = 1.96 * sigma * math.sqrt(h)                     # ~95%, widening the further ahead
        rows.append({"period": str(p), "forecast": round(float(v), 2),
                     "low": round(float(v) - band, 2), "high": round(float(v) + band, 2)})
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
    data = pd.DataFrame({"g": df[g].astype(str), "v": _numbers(df, v)}).dropna()
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
    if len(groups) == 2:
        (na, a), (nb, b) = groups.items()
        t = stats.ttest_ind(a, b, equal_var=False)
        u = stats.mannwhitneyu(a, b, alternative="two-sided")
        pooled = math.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
        d = (a.mean() - b.mean()) / pooled if pooled else 0.0
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
    strength = "very weak" if abs(r) < 0.1 else "weak" if abs(r) < 0.3 else "moderate" if abs(r) < 0.5 else "strong"
    direction = "goes up" if r > 0 else "goes down"
    sig = pr.pvalue < ALPHA
    summary = (f"{strength.capitalize()} {'positive' if r > 0 else 'negative'} relationship between {x} and {y} "
               f"(Pearson r = {r:.2f}, {_p(pr.pvalue)}; Spearman rho = {rho:.2f}; {len(data):,} rows): as {x} "
               f"rises, {y} {direction}" + ("" if sig else ", though this could be chance") +
               ". A correlation shows the two move together, not that one causes the other.")
    return {"kind": "correlation", "x": x, "y": y, "n": len(data), "pearson_r": round(r, 4), "pearson_p": float(pr.pvalue),
            "spearman_rho": round(rho, 4), "spearman_p": float(sr.pvalue), "significant": bool(sig),
            "summary": summary, "warnings": [] if len(data) >= 30 else [f"Only {len(data)} rows."]}


# ------------------------------------------------------------------ unusual values
def outliers(df, label, value):
    lab, val = _column(df, label, "label"), _column(df, value, "value")
    data = pd.DataFrame({"label": df[lab].astype(str), "value": _numbers(df, val)}).dropna()
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
    except ValueError as e:                                  # the query was refused by the read-only checks
        return {"error": re.sub(r"^Query rejected: ", "Query rejected: ", str(e))}
    except Exception as e:
        return {"error": f"The analysis failed: {str(getattr(e, 'orig', None) or e)[:300]}"}
