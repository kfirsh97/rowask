"""Uploading your own files on the web page: cleaning, Hebrew, Excel quirks, and the session-only database."""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db  # noqa: E402
import dbprofile  # noqa: E402
import tools  # noqa: E402
import upload  # noqa: E402


def excel_bytes(df, tmp_path):
    path = tmp_path / "x.xlsx"
    df.to_excel(path, index=False)
    return path.read_bytes()


def rows(up, sql):
    return sqlite3.connect(up.path).execute(sql).fetchall()


def test_excel_export_is_cleaned_like_the_superstore_script(tmp_path):
    """The same problems the manual pandas clean-up fixed: names, Excel dates, ZIP codes as 77095.0, curly quotes."""
    df = pd.DataFrame({
        "Order Date": [datetime(2024, 1, 3), None, datetime(2024, 2, 10)],
        "Country/Region": ["United States", None, "Canada"],
        "Postal Code": [77095.0, None, float("nan")],
        "Product Name": ["Message Book, 5” X 4”", None, "  Avery 508 "],
        "Sub-Category": ["Paper", None, "Labels"],
        "Sales": [16.448, None, 11.784],
        "Quantity": [2.0, None, 3.0],
    })
    up = upload.build([("Sample - Superstore.xlsx", excel_bytes(df, tmp_path))], tmp_path / "up")
    assert up.tables == {"Sample_Superstore": 2}                      # the empty row is gone
    assert rows(up, "SELECT Order_Date, Country_Region, Postal_Code, Product_Name, Sub_Category, Sales, Quantity "
                    "FROM Sample_Superstore ORDER BY Order_Date") == [
        ("2024-01-03", "United States", "77095", 'Message Book, 5" X 4"', "Paper", 16.448, 2),
        ("2024-02-10", "Canada", None, "Avery 508", "Labels", 11.784, 3)]
    report = "\n".join(up.report)
    assert "Postal_Code: whole numbers stored as decimals" in report and "Order_Date: dates" in report
    assert "1 completely empty row(s) dropped" in report
    assert "2 cell(s) trimmed or had curly quotes" in report             # the curly-quote name and " Avery 508 "


def test_hebrew_csv_from_excel_on_windows(tmp_path):
    csv = ("שם לקוח,תאריך,סכום,מיקוד,אחוז הנחה\n"
           "דנה כהן,25/12/2024,\"₪1,250.50\",0123456,10%\n"
           "אבי לוי,03/01/2025,₪300,5250000,0%\n").encode("cp1255")
    up = upload.build([("לקוחות.csv", csv)], tmp_path / "up")
    assert "windows-1255" in up.report[0]
    assert rows(up, 'SELECT * FROM "לקוחות"') == [("דנה כהן", "2024-12-25", 1250.5, "0123456", 0.1),
                                                 ("אבי לוי", "2025-01-03", 300.0, "5250000", 0.0)]


def test_ambiguous_dates_are_reported(tmp_path):
    up = upload.build([("d.csv", b"when,n\n03/04/2024,1\n05/06/2024,2\n")], tmp_path / "up")
    assert rows(up, "SELECT when_ FROM d ORDER BY n") == [("2024-04-03",), ("2024-06-05",)]   # SQL word renamed
    assert any("read as day/month" in r for r in up.report)


def test_clean_file_is_left_alone(tmp_path):
    up = upload.build([("t.csv", b"id,name,price\n1,a,2.5\n2,b,3\n")], tmp_path / "up")
    assert up.report == ["t.csv: read as UTF-8 text, comma-separated", "t - already clean, nothing changed"]


def test_semicolon_csv_and_two_files_make_two_tables(tmp_path):
    up = upload.build([("a.csv", b"x;y\n1;2\n"), ("b.csv", b"z\n3\n")], tmp_path / "up")
    assert up.tables == {"a": 1, "b": 1} and rows(up, "SELECT y FROM a") == [(2,)]


def test_sqlite_upload_is_used_as_it_is(tmp_path):
    src = Path(__file__).resolve().parents[1] / "data" / "chinook.sqlite"
    up = upload.build([("music.db", src.read_bytes())], tmp_path / "up")
    assert up.tables["Track"] == 3503 and "used as it is" in up.report[0]
    with pytest.raises(ValueError, match="not a SQLite database"):
        upload.build([("fake.db", b"hello")], tmp_path / "up2")


def test_unsupported_file_type(tmp_path):
    with pytest.raises(ValueError, match="unsupported file type"):
        upload.build([("notes.pdf", b"%PDF")], tmp_path / "up")


def test_description_and_examples_for_the_agent(tmp_path):
    csv = b"Order_Date,Region,Sales\n2024-01-03,West,10\n2025-02-01,East,20.5\n2025-03-01,West,5\n"
    up = upload.build([("orders.csv", csv)], tmp_path / "up")
    notes = up.profile["notes"]
    assert "Order_Date: date as 'YYYY-MM-DD' text, from 2024-01-03 to 2025-03-01" in notes
    assert "Region: one of 'East', 'West'" in notes and "Sales: number from 5.0 to 20.5" in notes
    assert up.profile["examples"][0] == "What is the total Sales by Region?"


def test_upload_is_for_this_session_only(tmp_path):
    """The page sets the upload for its own session; everyone else keeps the default database."""
    up = upload.build([("orders.csv", b"a,b\n1,2\n")], tmp_path / "up")
    db.SESSION_URL.set(up.url)
    dbprofile.SESSION_PROFILE.set(up.profile)
    try:
        assert "- orders(a integer, b integer)" in tools.get_schema() and "COLUMN NOTES" in tools.get_schema()
        assert dbprofile.load_profile()["name"] == "upload"
    finally:
        db.SESSION_URL.set(None)
        dbprofile.SESSION_PROFILE.set(None)
    assert dbprofile.load_profile()["name"] != "upload"
