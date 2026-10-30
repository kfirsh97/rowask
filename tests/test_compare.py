"""Tests for the answer checker - it must not fail correct answers or pass wrong ones."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compare import results_match  # noqa: E402


def test_same_rows_different_order():
    assert results_match([(2020, 191), (2021, 1232)], [(2021, 1232), (2020, 191)])


def test_extra_column_is_fine():
    assert results_match([(7,)], [("Wob", 7)])


def test_extra_columns_in_any_position():
    assert results_match([(1, 10.0), (2, 20.0)], [("x", 20.0, 2), ("y", 10.0, 1)])


def test_avg_rounding_is_tolerated():
    assert results_match([(5, 101234)], [(5, 101234.67)])


def test_wrong_count_fails():
    assert not results_match([(41,)], [(40,)])


def test_missing_row_fails():
    assert not results_match([(1,), (2,), (3,)], [(1,), (2,)])


def test_extra_row_fails():
    assert not results_match([(1,), (2,)], [(1,), (2,), (3,)])


def test_columns_must_line_up_row_by_row():
    # Same values per column, but paired wrongly: seller 1 gets seller 2's total.
    assert not results_match([(1, 100), (2, 200)], [(1, 200), (2, 100)])


def test_duplicates_ignored():
    assert results_match([(1,), (2,)], [(1,), (1,), (2,)])


def test_empty():
    assert results_match([], [])
    assert not results_match([(1,)], [])


def test_large_results_are_fast():
    import time
    rows = [(f"WP0{i:08d}", i % 3000) for i in range(5000)]
    t = time.time()
    assert results_match([(r[0],) for r in rows], [(r[1], r[0], "x") for r in reversed(rows)])
    assert time.time() - t < 2


def test_rounded_decimals_match_to_their_precision():
    assert results_match([(17.21882697783464,)], [(17.2,)])
    assert results_match([(17.21882697783464,)], [(17.22,)])
    assert not results_match([(2.6141178035449184,)], [(2.7,)])


def test_fraction_is_not_a_percentage():
    assert not results_match([(17.21882697783464,)], [(0.1722,)])


def test_numbers_written_as_text():
    assert results_match([(12,)], [("12",)])
    assert results_match([(2025, 613933.58)], [("2025", 613933.58)])
