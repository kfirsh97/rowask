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
