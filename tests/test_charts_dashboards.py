"""Charts (form chosen from the data, never numbers from the model) and dashboards (pinned queries, live)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import charts  # noqa: E402
import dashboards  # noqa: E402
import db  # noqa: E402
import dbprofile  # noqa: E402


@pytest.mark.parametrize("cols, rows, expected", [
    (["Year", "Profit"], [(2023, 1.0), (2024, 2.0), (2025, 3.0)], {"type": "line", "x": "Year", "y": "Profit"}),
    (["Month", "Sales"], [("2024-01", 1), ("2024-02", 2)], {"type": "line", "x": "Month", "y": "Sales"}),
    (["Sub_Category", "Profit"], [("Tables", -5), ("Phones", 9)], {"type": "bar", "x": "Sub_Category", "y": "Profit"}),
    (["Region", "Segment", "Sales"], [("W", "A", 1), ("W", "B", 2), ("E", "A", 3), ("E", "B", 4)],
     {"type": "grouped_bar", "x": "Region", "y": "Sales", "color": "Segment"}),
    (["State", "Region", "Sales"], [("TX", "C", 1), ("CA", "W", 2), ("NY", "E", 3)],
     {"type": "bar", "x": "State", "y": "Sales", "color": "Region"}),      # each bar has one group: no grouping
    (["Total"], [(2326534.35,)], {"type": "stat", "y": "Total"}),
    (["Discount", "Margin"], [(0.1, 0.2), (0.2, 0.1)], {"type": "scatter", "x": "Discount", "y": "Margin"}),
    (["Name", "City"], [("Dana", "Haifa")], None),                          # nothing to measure: a table, not a chart
])
def test_form_follows_the_data(cols, rows, expected):
    spec = charts.auto_spec(cols, rows)
    assert (None if spec is None else {k: v for k, v in spec.items() if v is not None}) == expected


def test_model_spec_is_kept_only_if_it_names_real_columns():
    assert charts.check_spec({"type": "line", "x": "Year", "y": "Profit"}, ["Year", "Profit"])["type"] == "line"
    assert charts.check_spec({"type": "line", "x": "Year", "y": "Revenue"}, ["Year", "Profit"]) is None
    assert charts.check_spec({"type": "pie", "x": "a", "y": "b"}, ["a", "b"]) is None


def test_chart_block_is_taken_out_of_the_answer():
    spec, text = charts.parse_spec('Up 20%.\n```sql\nSELECT 1\n```\n```chart\n{"type": "bar", "x": "a", "y": "b"}\n```')
    assert spec == {"type": "bar", "x": "a", "y": "b"} and "```chart" not in text and "```sql" in text
    assert charts.parse_spec("no chart here") == (None, "no chart here")


def test_more_than_eight_series_fold_into_other():
    rows = [(2024, f"s{i}", i) for i in range(12)]
    chart = charts.build({"type": "line", "x": "Year", "y": "v", "color": "s"}, ["Year", "s", "v"], rows)
    spec = charts.style(chart).to_dict()
    scale = next(l["encoding"]["color"]["scale"] for l in spec["layer"] if "color" in l.get("encoding", {}))
    assert len(scale["domain"]) == 8 and scale["domain"][-1] == "Other" and scale["range"][-1] == charts.OTHER


def test_numbers_are_compact_but_exact_below_ten_thousand():
    assert [charts.compact(v) for v in (5111, 12_900, 2_326_534.35, 0.25)] == ["5,111", "12.9K", "2.3M", "0.25"]


@pytest.fixture
def porsche_sqlite(monkeypatch, tmp_path):
    for k in ("DATABASE_URL", "DB_USER", "DB_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(dashboards, "DIR", tmp_path / "dashboards")
    dbprofile.load_profile.cache_clear()
    db._engine_for.cache_clear()
    yield


def test_dashboard_is_saved_and_runs_live(porsche_sqlite):
    items = [dashboards.new_item("Sales per year", "SELECT CAST(strftime('%Y', Sold_Date) AS INTEGER) AS Year, "
                                 "COUNT(*) AS Sales FROM Sales GROUP BY 1 ORDER BY 1"),
             dashboards.new_item("Cars", "SELECT COUNT(*) AS Cars FROM CARS"),
             dashboards.new_item("Broken", "SELECT nope FROM Sales")]
    dashboards.save("porsche", items)
    loaded = dashboards.load("porsche")
    assert [i["title"] for i in loaded] == ["Sales per year", "Cars", "Broken"]
    results = {i["id"]: dashboards.run(i) for i in loaded}
    first, second, third = (results[i["id"]] for i in loaded)
    assert first["spec"]["type"] == "line" and first["rows"][-1] == (2023, 1503)
    assert second["spec"]["type"] == "stat" and second["rows"] == [(4554,)]
    assert "no such column" in third["error"]


def test_exported_page_is_standalone_and_escapes_titles(porsche_sqlite):
    items = [dashboards.new_item("<script>alert(1)</script> sales", "SELECT CAST(strftime('%Y', Sold_Date) AS INTEGER) "
                                 "AS Year, COUNT(*) AS Sales FROM Sales GROUP BY 1"),
             dashboards.new_item("Cars", "SELECT COUNT(*) AS Cars FROM CARS")]
    results = {i["id"]: dashboards.run(i) for i in items}
    html = dashboards.export_html("Porsche dashboard", "Live", items, results)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert 'class="stat-value">4,554<' in html and "vega-lite@6.4.1" in html
    specs = json.loads(html.split("const SPECS = ")[1].split(";\n")[0])
    assert len(specs) == 1 and next(iter(specs.values()))["data"]


# ------------------------------------------------------------------ drill-down
import drill  # noqa: E402
import tools  # noqa: E402


@pytest.fixture
def superstore(monkeypatch):
    monkeypatch.setenv("DB_PROFILE", "superstore")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/superstore.sqlite")
    monkeypatch.delenv("DB_USER", raising=False)
    dbprofile.load_profile.cache_clear()
    db._engine_for.cache_clear()
    drill._children.cache_clear()
    if not (Path(__file__).resolve().parents[1] / "data" / "superstore.sqlite").exists():
        pytest.skip("build data/superstore.sqlite first (setup/csv_to_sqlite.py) - the data is not in the repo")
    yield
    dbprofile.load_profile.cache_clear()
    db._engine_for.cache_clear()


def test_hierarchy_is_found_in_the_data(superstore):
    """States nest in regions and cities in states; a cross-cutting dimension (Segment) is never a next level."""
    q = "SELECT {x}, SUM(Sales) AS Sales FROM superstore GROUP BY {x}"
    assert drill.next_level(q.format(x="Region"), "Region")[2] == "State_Province"
    assert drill.next_level(q.format(x="State_Province"), "State_Province")[2] == "City"
    assert drill.next_level(q.format(x="Category"), "Category")[2] == "Sub_Category"
    assert drill.next_level(q.format(x="City"), "City") is None            # the bottom
    assert drill.next_level("SELECT COUNT(*) AS n FROM superstore", "n") is None


def test_drilled_numbers_add_up_to_the_clicked_bar(superstore):
    q = "SELECT Region, SUM(Sales) AS Sales FROM superstore WHERE Segment = 'Consumer' GROUP BY 1 ORDER BY 2 DESC"
    sql, child = drill.rewrite(q, "Region", "West")
    assert child == "State_Province" and "Segment = 'Consumer'" in sql   # the original filter is kept
    _, states = tools.run_query_all(sql)
    _, west = tools.run_query_all("SELECT SUM(Sales) FROM superstore WHERE Segment = 'Consumer' AND Region = 'West'")
    assert abs(sum(r[1] for r in states) - west[0][0]) < 0.01
    sql2, child2 = drill.rewrite(sql, "State_Province", "California")
    assert child2 == "City" and tools.run_query_all(sql2)[1][0][0] == "Los Angeles"


def test_clicked_value_is_quoted_not_injected(superstore):
    sql, _ = drill.rewrite("SELECT Region, COUNT(*) AS n FROM superstore GROUP BY Region", "Region", "x' OR '1'='1")
    assert tools.run_query_all(sql)[1] == []                               # treated as a value, matches nothing


def test_drill_across_a_join(monkeypatch):
    for k in ("DATABASE_URL", "DB_USER", "DB_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    dbprofile.load_profile.cache_clear()
    db._engine_for.cache_clear()
    q = ("SELECT z.State, SUM(s.Sold_Price) AS Revenue FROM Customers c JOIN ZipCodes z ON c.ZipCodeID = z.ZipCodeID "
         "JOIN Sales s ON c.ID_NUMBER = s.ID_NUMBER GROUP BY z.State ORDER BY Revenue DESC")
    sql, child = drill.rewrite(q, "State", "California")
    assert child == "City" and tools.run_query_all(sql)[1][0][0] == "San Francisco"
