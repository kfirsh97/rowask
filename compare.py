"""Decide whether the agent's query result matches the expected result ("execution accuracy").

Rules, chosen so a correct answer is not failed for cosmetic reasons:
  - Row order is ignored (unless you sort, any order is a valid answer).
  - Duplicate rows are ignored (a list of sellers is the same list with or without repeats).
  - Extra columns are fine: every EXPECTED column must match some column in the agent's result,
    and the rows must still line up when we look only at those matched columns.
  - Numbers match if they are equal, or within 1 when one side is a decimal
    (SQL Server's AVG of whole numbers drops the fraction; the agent may keep it).
"""
from itertools import permutations


def _norm(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return str(v).strip()


def _same(a, b):
    a, b = _norm(a), _norm(b)
    if isinstance(a, float) and isinstance(b, float):
        if a.is_integer() and b.is_integer():
            return a == b
        return abs(a - b) <= 1.0
    return a == b


def _key(row):
    """Exact-match key; whole-number floats are written as ints so 5 and 5.0 hash the same."""
    return tuple(int(v) if isinstance(v, float) and v.is_integer() else v for v in map(_norm, row))


def _rows_match(expected, got):
    """Same set of rows, with fuzzy number comparison.
    Exact matches are removed with a hash lookup first (fast even for thousands of rows);
    only the few leftovers get the slower fuzzy comparison."""
    exp = set(map(_key, expected))
    remaining = set(map(_key, got))
    if len(exp) != len(remaining):
        return False
    common = exp & remaining
    exp, remaining = list(exp - common), list(remaining - common)
    for row in exp:
        for i, cand in enumerate(remaining):
            if all(_same(x, y) for x, y in zip(row, cand)):
                remaining.pop(i)
                break
        else:
            return False
    return True


def results_match(expected_rows, got_rows):
    """True if got_rows contains the expected answer (see rules above)."""
    if not expected_rows:
        return not got_rows
    if not got_rows:
        return False
    n_exp, n_got = len(expected_rows[0]), len(got_rows[0])
    if n_got < n_exp:
        return False
    # Try every way of picking n_exp of the agent's columns, in order, and see if one lines up.
    for cols in permutations(range(n_got), n_exp):
        projected = [tuple(r[c] for c in cols) for r in got_rows]
        if _rows_match(expected_rows, projected):
            return True
    return False
