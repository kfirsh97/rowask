"""Charts for query results - drawn from the real rows, never from numbers the model writes.

The model may suggest an encoding (a small JSON "chart spec": type, x, y, color); otherwise auto_spec() picks
the form from the shape of the result. Both follow the data-viz method this project uses:
  - the form comes from the data's job: a trend -> line, a comparison -> bar, one number -> a stat tile
  - one y-axis only, never two; sequential/one color for a single series
  - categorical colors in a fixed, validated order (scripts/validate_palette.js, light mode, card surface
    #F7F8F5: all checks pass; three hues sit below 3:1 contrast, so every chart ships with a legend for
    2+ series and a table view of the same rows)
  - color follows the entity (sorted domain), never its rank; past 8 series the tail folds into "Other"
  - thin bars (<= 24px, 4px rounded data end), 2px lines, >= 8px markers with a 2px surface ring,
    hairline solid gridlines, values labelled selectively, a tooltip on every mark
  - pie only for part of a whole: at most 6 slices (the rest folds into "Other"), never with negative values
  - maps and heatmaps encode magnitude: one hue, light -> dark (the palette's sequential blue), never a rainbow
compatible() lists the forms a result can honestly take, for the chart-type switcher on the page.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

import altair as alt
import pandas as pd

SURFACE = "#F7F8F5"                       # the chart card
INK, INK_2, MUTED = "#22282A", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
OTHER = "#b8b6ae"                         # de-emphasis gray for the folded tail
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
FONT = "Hanken Grotesk, Heebo, system-ui, -apple-system, Segoe UI, sans-serif"
TYPES = {"bar", "line", "area", "stacked_bar", "grouped_bar", "stacked_100", "scatter", "stat", "pie", "map",
         "heatmap"}
LABELS = {"bar": "Bar", "line": "Line", "area": "Area", "pie": "Pie", "map": "Map", "heatmap": "Heatmap",
          "grouped_bar": "Grouped", "stacked_bar": "Stacked", "stacked_100": "100%", "scatter": "Scatter",
          "stat": "Number"}
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]   # palette steps 100-700
NO_DATA = "#e4e5e0"                       # map shapes with no value
MAX_SLICES = 6
GEO_DIR = Path(__file__).parent / "geo"
STATE_CODES = dict(zip(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
    "OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR".split(),
    ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
     "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas",
     "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
     "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
     "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
     "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
     "West Virginia", "Wisconsin", "Wyoming", "Puerto Rico"]))
COUNTRY_ALIASES = {
    "usa": "United States of America", "us": "United States of America", "united states": "United States of America",
    "u.s.": "United States of America", "u.s.a.": "United States of America", "america": "United States of America",
    "uk": "United Kingdom", "great britain": "United Kingdom", "england": "United Kingdom", "britain": "United Kingdom",
    "czech republic": "Czechia", "russian federation": "Russia", "korea, republic of": "South Korea",
    "republic of korea": "South Korea", "bosnia and herzegovina": "Bosnia and Herz.",
    "central african republic": "Central African Rep.", "democratic republic of the congo": "Dem. Rep. Congo",
    "dominican republic": "Dominican Rep.", "equatorial guinea": "Eq. Guinea", "south sudan": "S. Sudan",
    "ivory coast": "Côte d'Ivoire", "cote d'ivoire": "Côte d'Ivoire", "holland": "Netherlands",
    "the netherlands": "Netherlands", "uae": "United Arab Emirates", "swaziland": "eSwatini",
    "eswatini": "eSwatini", "north macedonia": "Macedonia", "turkiye": "Turkey", "türkiye": "Turkey",
    "ישראל": "Israel", "ארצות הברית": "United States of America", "ארה\"ב": "United States of America"}

MAX_SERIES = 8
MAX_BARS = 25
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
YEAR_NAME = re.compile(r"(year|yr|שנה)", re.I)


# ------------------------------------------------------------------ reading the result
def frame(columns, rows) -> pd.DataFrame:
    df = pd.DataFrame([list(r) for r in rows], columns=columns)
    for c in df.columns:
        if df[c].dtype == object:
            converted = pd.to_numeric(df[c], errors="coerce")
            if converted.notna().sum() == df[c].notna().sum() and df[c].notna().any():
                df[c] = converted
    return df


def _numeric(df, c) -> bool:
    return pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])


def _is_year(df, c) -> bool:
    if not _numeric(df, c):
        return False
    v = df[c].dropna()
    return bool(len(v)) and (v % 1 == 0).all() and v.between(1900, 2100).all() and \
        (bool(YEAR_NAME.search(str(c))) or v.nunique() == len(v))


def _is_time(df, c) -> bool:
    if _is_year(df, c):
        return True
    v = df[c].dropna().astype(str)
    return bool(len(v)) and v.map(lambda s: bool(ISO_DATE.match(s)) or bool(re.match(r"^\d{4}-\d{2}$", s))).all()


# ------------------------------------------------------------------ maps
@lru_cache(maxsize=2)
def _topo(kind: str) -> dict:
    return json.loads((GEO_DIR / ("us-states.json" if kind == "us" else "world-countries.json")).read_text())


@lru_cache(maxsize=2)
def _geo_names(kind: str) -> dict:
    feature = "states" if kind == "us" else "countries"
    return {g["properties"]["name"].lower(): g["properties"]["name"] for g in _topo(kind)["objects"][feature]["geometries"]}


def geo_match(values):
    """Which map a column's values fit: ('us' | 'world', {value: shape name}) - or None.
    Needs at least 3 places and 60% of the values found on the map (state codes like CA, country aliases)."""
    vals = [v for v in pd.Series(values).dropna().astype(str).unique()]
    if len(vals) < 3:
        return None
    best = None
    for kind in ("us", "world"):
        names = _geo_names(kind)
        found = {}
        for v in vals:
            key = v.strip().lower()
            if kind == "us" and v.strip().upper() in STATE_CODES:
                found[v] = STATE_CODES[v.strip().upper()]
            elif key in names:
                found[v] = names[key]
            elif kind == "world" and key in COUNTRY_ALIASES:
                found[v] = COUNTRY_ALIASES[key]
        share = len(found) / len(vals)
        if share >= 0.6 and (best is None or share > best[2]):
            best = (kind, found, share)
    return (best[0], best[1]) if best else None


# ------------------------------------------------------------------ choosing the form
def compatible(spec, columns, rows) -> list:
    """The chart forms this result can honestly take - for the chart-type switcher. The current form first."""
    if not spec or spec.get("type") == "stat" or not rows:
        return [spec["type"]] if spec else []
    df = frame(columns, rows)
    x, y, color = spec.get("x"), spec.get("y"), spec.get("color")
    if spec["type"] == "scatter":
        return ["scatter"]
    if x not in df.columns or y not in df.columns or not _numeric(df, y):
        return [spec["type"]]
    out = []
    if _is_time(df, x):
        out += ["line", "area", "bar"] + (["stacked_bar", "heatmap"] if color else [])
    elif color and color != x and not (df.groupby(x)[color].nunique() <= 1).all():
        out += ["grouped_bar", "stacked_bar", "stacked_100", "heatmap"]
    else:
        out += ["bar"]
        if (df[y].dropna() >= 0).all() and 2 <= df[x].nunique() <= 30:
            out.append("pie")
        if geo_match(df[x]):
            out.append("map")
    return [spec["type"]] + [t for t in out if t != spec["type"]] if spec["type"] in out else out


def auto_spec(columns, rows):
    """Pick a chart from the shape of the result, or None when a table says it better."""
    if not rows or not columns:
        return None
    df = frame(columns, rows)
    times = [c for c in df.columns if _is_time(df, c)]
    nums = [c for c in df.columns if _numeric(df, c) and c not in times]
    cats = [c for c in df.columns if c not in nums and c not in times]
    if len(df) == 1 and nums:
        return {"type": "stat", "y": nums[0]}
    if not nums:
        return None
    y = nums[0]
    if times:
        color = next((c for c in cats if df[c].nunique() <= 30), None)
        return {"type": "line", "x": times[0], "y": y, "color": color}
    if cats:
        x = cats[0]
        if len(cats) > 1 and df[cats[1]].nunique() <= 30:
            group = cats[1]
            if (df.groupby(x)[group].nunique() <= 1).all():      # each bar belongs to one group (state -> region)
                return {"type": "bar", "x": x, "y": y, "color": group}
            if df[x].nunique() * df[group].nunique() <= 40:       # few enough bars to set side by side
                return {"type": "grouped_bar", "x": x, "y": y, "color": group}
            return {"type": "stacked_bar", "x": x, "y": y, "color": group}
        return {"type": "bar", "x": x, "y": y}
    if len(nums) >= 2:
        return {"type": "scatter", "x": nums[0], "y": nums[1]}
    return None


def check_spec(spec, columns):
    """Keep a model-suggested spec only if it names real columns of the result; otherwise None."""
    if not isinstance(spec, dict) or spec.get("type") not in TYPES:
        return None
    for key in ("x", "y", "color"):
        if spec.get(key) is not None and spec[key] not in columns:
            return None
    if spec["type"] != "stat" and not (spec.get("x") and spec.get("y")):
        return None
    if spec["type"] == "stat" and not spec.get("y"):
        return None
    return {k: spec.get(k) for k in ("type", "x", "y", "color", "title") if spec.get(k) is not None}


def parse_spec(text):
    """The ```chart {...}``` block a model may add to its answer -> (spec dict or None, answer without it)."""
    m = re.search(r"```chart\s*(\{.*?\})\s*```", text or "", flags=re.S | re.I)
    if not m:
        return None, text
    try:
        spec = json.loads(m.group(1))
    except json.JSONDecodeError:
        spec = None
    return spec, (text[:m.start()] + text[m.end():]).strip()


# ------------------------------------------------------------------ drawing
def compact(v) -> str:
    """1,284 stays exact; 12.9K / $4.2M style from 10,000 up."""
    if hasattr(v, "item"):                       # numpy numbers -> plain Python
        v = v.item()
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    a = abs(v)
    if a < 10_000:
        return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}"
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= size:
            return f"{v / size:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}"


def _fold(df, color, y):
    """More than 8 series: keep the 7 largest, sum the rest into 'Other' - never a 9th hue."""
    if color is None or df[color].nunique() <= MAX_SERIES:
        return df, sorted(df[color].dropna().astype(str).unique()) if color else []
    keep = df.groupby(color)[y].sum().abs().nlargest(MAX_SERIES - 1).index
    df = df.copy()
    df[color] = df[color].where(df[color].isin(keep), "Other")
    others = [c for c in df.columns if c not in (color, y)]
    df = df.groupby(others + [color], dropna=False, as_index=False)[y].sum()
    return df, sorted(str(k) for k in keep) + ["Other"]


def _scale(domain):
    colors = [OTHER if d == "Other" else CATEGORICAL[i] for i, d in enumerate(domain)]
    return alt.Scale(domain=domain, range=colors)


def _y_format(series):
    m = series.abs().max() if len(series) else 0
    return "~s" if m >= 10_000 else ",.4~f"


def _label_expr(field, fmt):
    """Text for a value label: 458K / 1.2M / 2,763 - three significant figures, never 457.688k."""
    v = f"datum['{field}']"
    if fmt == "~s":
        return f"replace(replace(format({v}, '.3~s'), 'k', 'K'), 'G', 'B')"
    return f"format({v}, ',.4~f')"


def _axis(fmt, **kw):
    """Axis ticks: clean compact numbers - 0 / 20K / 1.5M (d3's ~s would say 0k, 20k, 1.5G for billions)."""
    if fmt == "~s":
        return alt.Axis(labelExpr="datum.value == 0 ? '0' : replace(replace(format(datum.value, '~s'), 'k', 'K'), 'G', 'B')", **kw)
    return alt.Axis(format=fmt, **kw)


def _x_encoding(df, x, horizontal=False):
    if _is_year(df, x) or (df[x].dtype == object and df[x].astype(str).str.match(r"^\d{4}-\d{2}$").all()):
        return alt.X(f"{x}:O", title=x, axis=alt.Axis(labelAngle=0))
    if _is_time(df, x):
        return alt.X(f"{x}:T", title=x)
    return alt.X(f"{x}:N", title=x)


DRILL = "drill"        # name of the click selection on a drillable bar chart


def build(spec, columns, rows, height=300, drillable=False):
    """An Altair chart for this spec and result, or a ('stat', label, value) tuple for one number.
    drillable: bars carry a click selection on their category (read by the page to zoom into it)."""
    df = frame(columns, rows)
    kind, x, y, color = spec["type"], spec.get("x"), spec.get("y"), spec.get("color")
    if kind == "stat":
        return ("stat", y, df[y].iloc[0] if len(df) else None)
    df = df.dropna(subset=[y])
    if not _numeric(df, y):
        return None
    tip = [alt.Tooltip(f"{c}:Q", format=",.2f") if _numeric(df, c) else alt.Tooltip(f"{c}:N")
           for c in df.columns if c in {x, y, color}]
    yfmt = _y_format(df[y])
    df, domain = _fold(df, color, y)
    color_enc = alt.Color(f"{color}:N", scale=_scale(domain), legend=alt.Legend(orient="top", title=None)) \
        if color else alt.value(CATEGORICAL[0])

    pick = alt.selection_point(name=DRILL, fields=[x], on="click", clear=False) if drillable and x else None
    hand = {"cursor": "pointer"} if drillable else {}

    if kind in ("pie", "map", "heatmap"):        # a form the rows can't take (a negative slice, no places): bars
        out = _pie(df, x, y, pick, hand) if kind == "pie" else _map(df, x, y, pick, hand, height) if kind == "map" \
            else _heatmap(df, x, y, color, yfmt, height)
        return out if out is not None else build({**spec, "type": "grouped_bar" if color else "bar"},
                                                  columns, rows, height, drillable)
    if kind == "stacked_100" and color:
        n = df[x].nunique()
        bar = alt.Chart(df).mark_bar(size=min(24, max(6, 300 // max(n, 1))), stroke=SURFACE, strokeWidth=2,
                                     cornerRadiusEnd=0, **hand).encode(
            x=_x_encoding(df, x) if _is_time(df, x) else alt.X(f"{x}:N", title=x, axis=alt.Axis(labelAngle=0)),
            y=alt.Y(f"sum({y}):Q", stack="normalize", title=f"Share of {y}", axis=alt.Axis(format="%")),
            color=color_enc, tooltip=tip)
        return (bar.add_params(pick) if pick is not None else bar).properties(height=height)

    if kind in ("bar", "grouped_bar", "stacked_bar"):
        if kind == "bar" and len(df) > MAX_BARS:
            df = df.nlargest(MAX_BARS, y)
        cats = df[x].astype(str)
        horizontal = kind == "bar" and (df[x].nunique() > 6 or cats.str.len().max() > 14) \
            and not _is_time(df, x)
        n = df[x].nunique()
        base = alt.Chart(df)
        if horizontal:
            bar = base.mark_bar(cornerRadiusEnd=4, size=min(24, max(8, 260 // max(n, 1))), **hand).encode(
                y=alt.Y(f"{x}:N", sort="-x", title=None),
                x=alt.X(f"{y}:Q", title=y, axis=_axis(yfmt)),
                color=color_enc, tooltip=tip)
            if pick is not None:
                bar = bar.add_params(pick)
            label = base.transform_calculate(_label=_label_expr(y, yfmt)).mark_text(
                align="left", dx=4, color=INK_2, fontSize=11).encode(
                y=alt.Y(f"{x}:N", sort="-x"), x=f"{y}:Q", text="_label:N")
            height = max(height, 26 * n + 40)
            return (bar + label if n <= 15 else bar).properties(height=height)
        xenc = _x_encoding(df, x)
        if not _is_time(df, x):
            xenc = alt.X(f"{x}:N", title=x, sort="-y" if kind == "bar" else "ascending",
                         axis=alt.Axis(labelAngle=0 if cats.str.len().max() <= 10 else -35))
        mark = dict(cornerRadiusEnd=4)
        if kind == "stacked_bar":
            mark.update(stroke=SURFACE, strokeWidth=2)          # the 2px surface gap between segments
        enc = dict(x=xenc, y=alt.Y(f"{y}:Q", title=y, axis=_axis(yfmt)), color=color_enc, tooltip=tip)
        if kind == "grouped_bar" and color:
            enc["xOffset"] = alt.XOffset(f"{color}:N", sort=domain)
        if kind == "stacked_bar" and color:
            enc["y"] = alt.Y(f"sum({y}):Q", title=y, axis=_axis(yfmt))
        bars_per_band = len(domain) if kind == "grouped_bar" and color else 1
        bar = alt.Chart(df).mark_bar(size=min(24, max(6, 300 // max(n * bars_per_band, 1))), **mark, **hand).encode(**enc)
        if pick is not None:
            bar = bar.add_params(pick)
        if kind == "bar" and n <= 12:
            label = alt.Chart(df).transform_calculate(_label=_label_expr(y, yfmt)).mark_text(
                dy=-8, color=INK_2, fontSize=11).encode(x=xenc, y=f"{y}:Q", text="_label:N")
            return (bar + label).properties(height=height)
        return bar.properties(height=height)

    if kind in ("line", "area"):
        xenc = _x_encoding(df, x)
        base = alt.Chart(df).encode(x=xenc, y=alt.Y(f"{y}:Q", title=y, axis=_axis(yfmt)),
                                    color=color_enc, tooltip=tip)
        line = base.mark_line(strokeWidth=2, strokeCap="round", strokeJoin="round")
        layers = [line]
        if kind == "area" and not color:
            layers.insert(0, base.mark_area(opacity=0.1))
        if len(df) <= 40:
            layers.append(base.mark_point(size=64, filled=True, opacity=1, stroke=SURFACE, strokeWidth=2))
        return alt.layer(*layers).properties(height=height)

    if kind == "scatter":
        return alt.Chart(df).mark_point(size=64, filled=True, opacity=0.85, stroke=SURFACE, strokeWidth=2).encode(
            x=alt.X(f"{x}:Q", title=x), y=alt.Y(f"{y}:Q", title=y, axis=_axis(yfmt)),
            color=color_enc, tooltip=tip).properties(height=height)
    return None


def _pie(df, x, y, pick, hand):
    """Part of a whole: a donut with at most 6 slices (the rest folds into 'Other'), the total in the middle."""
    df = df.groupby(x, as_index=False)[y].sum()
    if (df[y] < 0).any():
        return None                                      # a negative slice has no meaning - use a bar
    df = df.sort_values(y, ascending=False)
    if len(df) > MAX_SLICES:
        tail = df.iloc[MAX_SLICES - 1:][y].sum()
        df = pd.concat([df.iloc[:MAX_SLICES - 1], pd.DataFrame({x: ["Other"], y: [tail]})], ignore_index=True)
    df[x] = df[x].astype(str)
    total = df[y].sum()
    df["_share"] = df[y] / total if total else 0
    domain = sorted(v for v in df[x] if v != "Other") + (["Other"] if "Other" in set(df[x]) else [])
    base = alt.Chart(df).encode(
        theta=alt.Theta(f"{y}:Q", stack=True), order=alt.Order(f"{y}:Q", sort="descending"),
        color=alt.Color(f"{x}:N", scale=_scale(domain), legend=alt.Legend(orient="right", title=None)),
        tooltip=[alt.Tooltip(f"{x}:N"), alt.Tooltip(f"{y}:Q", format=",.2f"), alt.Tooltip("_share:Q", title="Share", format=".1%")])
    arcs = base.mark_arc(innerRadius=70, outerRadius=120, stroke=SURFACE, strokeWidth=2, **hand)
    if pick is not None:
        arcs = arcs.add_params(pick)
    labels = base.transform_filter("datum._share >= 0.05").mark_text(radius=138, color=INK_2, fontSize=11).encode(
        text=alt.Text("_share:Q", format=".0%"))
    middle = alt.Chart(pd.DataFrame({"t": [compact(total)], "s": [y]}))
    center = middle.mark_text(fontSize=22, fontWeight=600, color=INK, dy=-6).encode(text="t:N") + \
        middle.mark_text(fontSize=11, color=INK_2, dy=14).encode(text="s:N")
    return alt.layer(arcs, labels, center).properties(height=300)


def _map(df, x, y, pick, hand, height):
    """Magnitude by place: US states or world countries, one hue light -> dark; places with no value stay gray."""
    match = geo_match(df[x])
    if not match:
        return None
    kind, found = match
    df = df.copy()
    df["_geo"] = df[x].astype(str).map(found)
    df = df.dropna(subset=["_geo"]).groupby(["_geo", x], as_index=False)[y].sum()
    feature = "states" if kind == "us" else "countries"
    topo = alt.InlineData(values=_topo(kind), format=alt.DataFormat(type="topojson", feature=feature))
    projection = "albersUsa" if kind == "us" else "equalEarth"
    land = alt.Chart(topo).mark_geoshape(fill=NO_DATA, stroke=SURFACE, strokeWidth=0.75).project(projection)
    shapes = alt.Chart(topo).mark_geoshape(stroke=SURFACE, strokeWidth=0.75, **hand).transform_lookup(
        lookup="properties.name", from_=alt.LookupData(df, "_geo", [x, y])
    ).transform_filter(f"isValid(datum['{y}'])").encode(
        color=alt.Color(f"{y}:Q", title=y, scale=alt.Scale(range=[SEQUENTIAL[0], SEQUENTIAL[-1]]),
                        legend=alt.Legend(orient="bottom", format=_y_format(df[y]), gradientLength=220)),
        tooltip=[alt.Tooltip(f"{x}:N"), alt.Tooltip(f"{y}:Q", format=",.2f")]).project(projection)
    if pick is not None:
        shapes = shapes.add_params(pick)
    return alt.layer(land, shapes).properties(height=max(height, 320))


def _heatmap(df, x, y, color, yfmt, height):
    """Two groupings and a measure: one cell each, darker = more (sequential, one hue)."""
    if not color:
        return None
    rows_axis = alt.Y(f"{color}:N", title=None)
    cols_axis = _x_encoding(df, x) if _is_time(df, x) and not _is_year(df, x) else alt.X(f"{x}:O" if _is_year(df, x) else f"{x}:N", title=x, axis=alt.Axis(labelAngle=0))
    rect = alt.Chart(df).mark_rect(stroke=SURFACE, strokeWidth=2, cornerRadius=2).encode(
        x=cols_axis, y=rows_axis,
        color=alt.Color(f"sum({y}):Q", title=y, scale=alt.Scale(range=[SEQUENTIAL[0], SEQUENTIAL[-1]]),
                        legend=alt.Legend(orient="bottom", format=yfmt, gradientLength=220)),
        tooltip=[alt.Tooltip(f"{x}:N"), alt.Tooltip(f"{color}:N"), alt.Tooltip(f"sum({y}):Q", title=y, format=",.2f")])
    return rect.properties(height=max(height, 28 * df[color].nunique() + 60))


def style(chart):
    """The chart chrome: card surface, hairline solid grid, recessive axes, text in ink - not in series colors."""
    return chart.configure(background=SURFACE, font=FONT, padding=12) \
        .configure_view(stroke=None) \
        .configure_axis(gridColor=GRID, gridWidth=1, gridDash=[], domainColor=AXIS, tickColor=AXIS,
                        labelColor=INK_2, titleColor=INK_2, labelFontSize=11, titleFontSize=11,
                        titleFontWeight=500) \
        .configure_axisX(grid=False) \
        .configure_legend(labelColor=INK_2, labelFontSize=11, symbolType="circle") \
        .configure_title(color=INK, fontSize=13, anchor="start")


def stat_html(label, value, caption="") -> str:
    """A stat tile: label, value (compact, proportional figures), optional caption."""
    from html import escape
    return (f'<div class="stat"><div class="stat-label">{escape(str(label))}</div>'
            f'<div class="stat-value">{escape(compact(value) if hasattr(value, "real") else str(value))}</div>'
            + (f'<div class="stat-caption">{escape(caption)}</div>' if caption else "") + "</div>")
