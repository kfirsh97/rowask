"""CSV -> SQLite import (setup/csv_to_sqlite.py): types, leading zeros, encodings, and a clear error for bad files."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup"))
import csv_to_sqlite  # noqa: E402


def test_types_are_inferred_and_leading_zeros_kept(tmp_path):
    src = tmp_path / "My Orders.csv"
    src.write_text('Order ID,Zip,Qty,Price,Note,Date\n'
                   'A-1,01810,2,9.5,"Book, ""large""",2024-01-03\n'
                   'A-2,77095,,12,,2024-02-10\n', encoding="utf-8")
    out = tmp_path / "shop.sqlite"
    csv_to_sqlite.main([str(src), "--out", str(out)])
    db = sqlite3.connect(out)
    cols = {r[1]: r[2] for r in db.execute("PRAGMA table_info(My_Orders)")}
    assert cols == {"Order_ID": "TEXT", "Zip": "TEXT", "Qty": "INTEGER", "Price": "REAL", "Note": "TEXT", "Date": "TEXT"}
    assert db.execute("SELECT Zip, Qty, Price, Note FROM My_Orders ORDER BY Order_ID").fetchall() == [
        ("01810", 2, 9.5, 'Book, "large"'), ("77095", None, 12.0, None)]


def test_old_excel_latin1_file(tmp_path):
    src = tmp_path / "c.csv"
    src.write_bytes("name,city\nJosé,Montréal\n".encode("latin-1"))
    table, n, _, encoding = csv_to_sqlite.load(sqlite3.connect(":memory:"), src)
    assert (table, n, encoding) == ("c", 1, "latin-1")


def test_ragged_file_is_rejected_with_the_line_number(tmp_path):
    src = tmp_path / "bad.csv"
    src.write_text("a,b\n1,2\n3\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="line 3"):
        csv_to_sqlite.load(sqlite3.connect(":memory:"), src)
