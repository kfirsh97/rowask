"""Upload your own data: clean CSV / Excel files, run SQL scripts (any dialect - see sqlimport.py), or take a
SQLite file as it is; build a database from them, and describe it for the agent.

The cleaning makes general the steps of a manual pandas clean-up of the Superstore Excel export:
  - column names: spaces, slashes, dashes and other symbols -> "_" (Hebrew letters are kept)
  - dates -> 'YYYY-MM-DD' text, the form SQLite sorts, compares and groups correctly
  - codes that Excel turned into decimals (ZIP 77095 -> 77095.0) -> back to whole numbers, as text for code columns
  - curly quotes and long dashes -> plain ones; spaces trimmed; empty cells -> NULL
  - numbers stored as text ("$1,234.50", "45%") -> numbers; floating-point noise rounded
  - completely empty rows and columns dropped
Unlike that script, text is NOT converted to plain ASCII: that would delete every Hebrew letter in a file.
Every change is reported, so the person who uploaded the file sees exactly what was done to it.
"""
import csv
import io
import re
import shutil
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

TABLE_EXTENSIONS = {".csv", ".txt", ".tsv", ".xlsx", ".xlsm", ".xls"}
SQLITE_EXTENSIONS = {".sqlite", ".sqlite3", ".db"}
SQL_EXTENSIONS = {".sql"}
DIALECT_NAMES = {"tsql": "SQL Server (T-SQL)", "mysql": "MySQL", "postgres": "PostgreSQL", "sqlite": "SQLite / standard SQL"}
MAX_NOTES_CHARS = 8000          # the data description sent to the model with the schema

PLAIN = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'",
                       "–": "-", "—": "-", " ": " ", "‏": "", "‎": ""})
NUMBER = re.compile(r"^[\s$€£₪]*-?[\s$€£₪]*(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?\s*%?$")
LEADING_ZERO = re.compile(r"^0\d")
DATE_LIKE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")
CODE_NAME = re.compile(r"(zip|postal|post_code|phone|mobile|fax|_id$|^id_|^id$|code|מיקוד|טלפון|נייד|מספר|ת_?ז)",
                       re.I)


# Column or table names that are SQL words break queries unless quoted ("Order", "Group", "Select"...).
SQL_WORDS = {"select", "from", "where", "group", "order", "by", "having", "limit", "offset", "join", "on", "as",
             "and", "or", "not", "null", "is", "in", "like", "between", "case", "when", "then", "else", "end",
             "union", "all", "distinct", "table", "index", "key", "values", "insert", "update", "delete",
             "create", "drop", "primary", "foreign", "references", "default", "check", "to", "with", "into",
             "set", "exists", "inner", "outer", "left", "right", "full", "cross", "desc", "asc", "user"}


@dataclass
class Upload:
    path: Path                                    # the SQLite file built from the upload
    tables: dict = field(default_factory=dict)    # table name -> row count
    report: list = field(default_factory=list)    # human-readable list of what was cleaned
    profile: dict = field(default_factory=dict)   # title, description, notes, examples for the agent and the page

    @property
    def url(self) -> str:
        return f"sqlite:///{self.path}"


# ------------------------------------------------------------------ reading
def _hebrew_share(text: str) -> float:
    """Share of Hebrew letters among the non-ASCII characters of a text."""
    other = [ch for ch in text if ord(ch) > 127]
    return sum("\u05d0" <= ch <= "\u05ea" for ch in other) / len(other) if other else 0.0


def _decode(data: bytes) -> tuple:
    try:
        return data.decode("utf-8-sig"), "UTF-8"
    except UnicodeDecodeError:
        pass
    # Excel on Hebrew Windows saves "CSV" as windows-1255. General detectors often miss it on small files,
    # so check it first: if nearly every non-ASCII character comes out as a Hebrew letter, that's the one.
    try:
        text = data.decode("cp1255")
        if _hebrew_share(text) >= 0.8:
            return text, "windows-1255 (Hebrew)"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data).best()
        if best is not None:
            return str(best), best.encoding
    except ImportError:
        pass
    return data.decode("cp1252", errors="replace"), "windows-1252"


def read_tables(name: str, data: bytes) -> tuple:
    """Return ({table_name: raw DataFrame}, note about how it was read). Text files are read as all-text
    columns, so nothing (like a leading zero) is lost before cleaning decides each column's type."""
    ext = Path(name).suffix.lower()
    stem = clean_name(Path(name).stem) or "data"
    if ext in (".csv", ".txt", ".tsv"):
        text, encoding = _decode(data)
        try:
            sep = "\t" if ext == ".tsv" else csv.Sniffer().sniff(text[:20_000], delimiters=",;\t|").delimiter
        except csv.Error:
            sep = ","
        df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False, skip_blank_lines=True)
        shown = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}.get(sep, sep)
        return {stem: df}, f"{name}: read as {encoding} text, {shown}-separated"
    if ext in (".xlsx", ".xlsm", ".xls"):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=object, header=0)
        sheets = {k: v for k, v in sheets.items() if not v.dropna(how="all").empty}
        if len(sheets) == 1:
            return {stem: next(iter(sheets.values()))}, f"{name}: Excel, 1 sheet"
        return ({f"{stem}_{clean_name(k)}": v for k, v in sheets.items()},
                f"{name}: Excel, {len(sheets)} sheets -> one table each")
    raise ValueError(f"{name}: unsupported file type. Upload CSV, Excel (.xlsx/.xls) or a SQLite database.")


# ------------------------------------------------------------------ cleaning
def clean_name(name) -> str:
    name = unicodedata.normalize("NFKC", str(name)).translate(PLAIN).strip()
    name = re.sub(r"\W+", "_", name).strip("_")
    if name.lower() in SQL_WORDS:
        return f"{name}_"
    return f"c_{name}" if name[:1].isdigit() else name


def _plain(v):
    if isinstance(v, str):
        v = unicodedata.normalize("NFKC", v).translate(PLAIN).strip()
        return v or None
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return None
    return v


def _is_whole(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and float(x).is_integer()


def _to_number(s: str):
    pct = s.endswith("%")
    n = float(re.sub(r"[\s$€£₪,%]", "", s))
    n = n / 100 if pct else n
    return int(n) if n.is_integer() and not pct and "." not in s else n


def _dates(values: pd.Series, column: str, report: list):
    """Parse a text column of dates. 3/4/2024 is ambiguous - decide day/month order from values like 25/12."""
    text = values.astype(str)
    parts = text.str.extract(r"^(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}")
    dayfirst = False
    if parts.notna().all(axis=None):
        first, second = parts[0].astype(int), parts[1].astype(int)
        if (first > 12).any() and not (second > 12).any():
            dayfirst = True
        elif not (first > 12).any() and not (second > 12).any():
            dayfirst = True
            report.append(f"{column}: dates like 03/04/2024 could be either order - read as day/month")
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=dayfirst, format="mixed")
    return parsed


def _format_dates(parsed: pd.Series) -> pd.Series:
    has_time = ((parsed.dt.hour != 0) | (parsed.dt.minute != 0) | (parsed.dt.second != 0)).any()
    out = parsed.dt.strftime("%Y-%m-%d %H:%M:%S" if has_time else "%Y-%m-%d")
    return out.astype(object).where(parsed.notna(), None)


def clean_frame(df: pd.DataFrame, table: str) -> tuple:
    """Clean one table. Returns (clean DataFrame, list of what changed)."""
    report = []
    df = df.copy()

    # column names
    names, seen = [], {}
    for i, c in enumerate(df.columns):
        n = "" if str(c).startswith("Unnamed:") else clean_name(c)
        n = n or f"column_{i + 1}"
        if n in seen:
            seen[n] += 1
            n = f"{n}_{seen[n]}"
        seen.setdefault(n, 1)
        names.append(n)
    renamed = [(str(a), b) for a, b in zip(df.columns, names) if str(a) != b]
    if renamed:
        shown = ", ".join(f"'{a}' -> {b}" for a, b in renamed[:4])
        report.append(f"{len(renamed)} column name(s) made SQL-safe ({shown}{', ...' if len(renamed) > 4 else ''})")
    df.columns = names

    # cells: plain quotes and dashes, trimmed text, empty -> NULL
    texts = df.apply(lambda col: col.map(lambda v: v if isinstance(v, str) else None))
    df = df.apply(lambda col: col.map(_plain)).astype(object)
    df = df.where(pd.notna(df), None)
    changed = int(((texts.notna()) & (texts != df)).sum().sum())       # text cells whose content changed
    if changed:
        report.append(f"{changed:,} cell(s) trimmed or had curly quotes / long dashes made plain")

    # empty rows and columns
    rows0, cols0 = df.shape
    df = df.dropna(how="all").dropna(axis=1, how="all")
    if df.shape[0] < rows0:
        report.append(f"{rows0 - df.shape[0]:,} completely empty row(s) dropped")
    if df.shape[1] < cols0:
        report.append(f"{cols0 - df.shape[1]} completely empty column(s) dropped")
    df = df.reset_index(drop=True)

    # each column's type
    for c in df.columns:
        present = df[c].dropna()
        if present.empty:
            continue
        kinds = {type(v) for v in present}

        if kinds <= {datetime, date, pd.Timestamp}:                       # real dates (Excel)
            df[c] = _format_dates(pd.to_datetime(df[c]))
            report.append(f"{c}: dates written as YYYY-MM-DD")
            continue

        if kinds <= {int, float}:                                          # numbers (Excel)
            if all(_is_whole(v) for v in present):
                if CODE_NAME.search(c):
                    df[c] = df[c].map(lambda v: None if v is None else str(int(v)))
                    if float in kinds:
                        report.append(f"{c}: whole numbers stored as decimals (77095.0) -> text without .0 "
                                      "(it is a code or ID, not an amount)")
                else:
                    df[c] = pd.array([None if v is None else int(v) for v in df[c]], dtype="Int64")
            else:
                df[c] = df[c].map(lambda v: None if v is None else round(float(v), 6)).astype("Float64")
            continue

        if kinds != {str}:                                                 # a mix of text and numbers (Excel)
            df[c] = df[c].map(lambda v: None if v is None else
                              str(int(v)) if _is_whole(v) else str(v))
            present = df[c].dropna()

        text = present.astype(str)
        if text.map(lambda s: bool(NUMBER.match(s)) and any(ch.isdigit() for ch in s)).all() and \
                not text.map(lambda s: bool(LEADING_ZERO.match(s.strip()))).any() and not CODE_NAME.search(c):
            styled = text.str.contains(r"[$€£₪,%]").any()
            df[c] = df[c].map(lambda v: None if v is None else _to_number(str(v)))
            nums = df[c].dropna()
            if all(isinstance(v, int) for v in nums):
                df[c] = pd.array(list(df[c]), dtype="Int64")
            else:
                df[c] = df[c].map(lambda v: None if v is None else round(float(v), 6)).astype("Float64")
            if styled:
                report.append(f"{c}: numbers written as text ($, commas, %) -> numbers"
                              + (" (percentages as fractions: 45% -> 0.45)" if text.str.endswith("%").any() else ""))
            continue

        if text.map(lambda s: bool(DATE_LIKE.match(s))).mean() >= 0.9:
            parsed = _dates(text, c, report)
            if parsed.notna().mean() >= 0.9:
                full = pd.Series([None] * len(df), dtype=object)
                full[present.index] = _format_dates(parsed).values
                bad = int(parsed.isna().sum())
                if bad or (full[present.index].astype(str).values != text.values).any():   # silent if already ISO
                    report.append(f"{c}: dates written as YYYY-MM-DD"
                                  + (f" ({bad} unreadable value(s) left empty)" if bad else ""))
                df[c] = full

    dups = int(df.duplicated().sum())
    if dups:
        report.append(f"{dups:,} row(s) are exact duplicates of another row - kept, so totals match the file")
    return df, [f"{table} - {r}" for r in report]


# ------------------------------------------------------------------ describing the data for the agent
def describe(con: sqlite3.Connection, tables: list) -> str:
    lines = []
    for t in tables:
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        lines.append(f"- {t}: {n:,} rows.")
        for _, col, kind, *_ in con.execute(f'PRAGMA table_info("{t}")'):
            q = f'"{col}"'
            distinct = con.execute(f'SELECT COUNT(DISTINCT {q}) FROM "{t}"').fetchone()[0]
            nulls = con.execute(f'SELECT COUNT(*) FROM "{t}" WHERE {q} IS NULL').fetchone()[0]
            empty = f", {nulls:,} empty" if nulls else ""
            if kind in ("INTEGER", "REAL"):
                lo, hi = con.execute(f'SELECT MIN({q}), MAX({q}) FROM "{t}"').fetchone()
                lines.append(f"  {col}: number from {lo} to {hi}{empty}")
                continue
            lo, hi = con.execute(f'SELECT MIN({q}), MAX({q}) FROM "{t}"').fetchone()
            if lo and DATE_LIKE.match(str(lo)) and re.match(r"^\d{4}-\d{2}-\d{2}", str(lo)):
                lines.append(f"  {col}: date as 'YYYY-MM-DD' text, from {lo} to {hi}{empty}")
            elif distinct <= 15:
                vals = [r[0] for r in con.execute(f'SELECT DISTINCT {q} FROM "{t}" WHERE {q} IS NOT NULL ORDER BY 1')]
                lines.append(f"  {col}: one of " + ", ".join(repr(str(v)[:40]) for v in vals) + empty)
            else:
                sample = [r[0] for r in con.execute(f'SELECT {q} FROM "{t}" WHERE {q} IS NOT NULL LIMIT 2')]
                lines.append(f"  {col}: text, {distinct:,} different values, e.g. "
                             + ", ".join(repr(str(v)[:40]) for v in sample) + empty)
    notes = "\n".join(lines)
    if len(notes) > MAX_NOTES_CHARS:
        notes = notes[:MAX_NOTES_CHARS].rsplit("\n", 1)[0] + "\n  ... (description shortened - call get_schema for the rest)"
    return notes


def suggest_examples(con: sqlite3.Connection, tables: list) -> list:
    """Starter questions from the shape of the data: a number to add up, a category to split it by, a date."""
    t = max(tables, key=lambda x: con.execute(f'SELECT COUNT(*) FROM "{x}"').fetchone()[0])
    cols = list(con.execute(f'PRAGMA table_info("{t}")'))
    money = re.compile(r"(sales|revenue|amount|price|total|profit|cost|income|value|סכום|מחיר|הכנסה|רווח|מכירות)", re.I)
    nums = [c[1] for c in cols if c[2] in ("INTEGER", "REAL") and not CODE_NAME.search(c[1])]
    num = next((c for c in nums if money.search(c)), nums[0] if nums else None)
    cats, dates = [], []
    for c in cols:
        if c[2] in ("INTEGER", "REAL"):
            continue
        q = f'"{c[1]}"'
        d = con.execute(f'SELECT COUNT(DISTINCT {q}), MIN({q}) FROM "{t}"').fetchone()
        if d[1] and re.match(r"^\d{4}-\d{2}-\d{2}", str(d[1])):
            dates.append(c[1])
        elif 2 <= d[0] <= 30:
            cats.append(c[1])
    ex = []
    if num and cats:
        ex.append(f"What is the total {num} by {cats[0]}?")
    if num and dates:
        ex.append(f"How did {num} change from year to year?")
    if num and len(cats) > 1:
        ex.append(f"Which {cats[1]} has the highest average {num}?")
    ex.append(f"What does the {t} table contain, and how many rows does it have?")
    return ex[:4]


# ------------------------------------------------------------------ building the database
def build(files: list, out_dir: Path) -> Upload:
    """files: [(file name, bytes)]. Builds out_dir/data.sqlite and returns what the page and the agent need."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "data.sqlite"
    path.unlink(missing_ok=True)
    up = Upload(path=path)

    sqlite_files = [(n, d) for n, d in files if Path(n).suffix.lower() in SQLITE_EXTENSIONS]
    if sqlite_files:
        if len(files) > 1:
            raise ValueError("Upload a SQLite database on its own (it is used as it is), or CSV/Excel files.")
        name, data = sqlite_files[0]
        if not data.startswith(b"SQLite format 3\x00"):
            raise ValueError(f"{name} is not a SQLite database.")
        path.write_bytes(data)
        up.report.append(f"{name}: a SQLite database - used as it is, no cleaning")
    else:
        con = sqlite3.connect(path)
        scripts = sorted([(n, d) for n, d in files if Path(n).suffix.lower() in SQL_EXTENSIONS])   # 01_schema before 02_data
        if scripts:
            import sqlimport
            # one run over all scripts in file-name order: a schema file and a data file act as one script
            # (auto-numbered keys are set after the data is in, foreign keys after every table exists)
            text = "\n;\n".join(_decode(d)[0] for _, d in scripts)
            r = sqlimport.run_script(con, text)
            con.isolation_level = ""
            name = ", ".join(n for n, _ in scripts)
            line = f"{name}: {DIALECT_NAMES.get(r['dialect'], r['dialect'])} script, {r['statements']:,} statements run"
            if r["skipped"]:
                line += " - skipped (server-only): " + ", ".join(f"{n} {k}" for k, n in r["skipped"].items())
            up.report.append(line)
            if r["identities"]:
                up.report.append(f"auto-numbered keys given the same values the original server would give "
                                 f"({r['identities']} table(s))")
            if r["foreign_keys"]:
                up.report.append(f"{r['foreign_keys']} foreign key(s) added by ALTER TABLE were built into the tables, "
                                 "so the agent sees how they link")
            for err in r["errors"][:5]:
                up.report.append(f"could not run - {err}")
            if len(r["errors"]) > 5:
                up.report.append(f"... and {len(r['errors']) - 5} more statement(s) that could not run")
        for name, data in files:
            if Path(name).suffix.lower() in SQL_EXTENSIONS:
                continue
            frames, how = read_tables(name, data)
            up.report.append(how)
            for table, raw in frames.items():
                table = table if table not in up.tables else f"{table}_{len(up.tables) + 1}"
                df, changes = clean_frame(raw, table)
                if df.empty:
                    up.report.append(f"{table} - no data left after dropping empty rows, skipped")
                    continue
                df.to_sql(table, con, index=False)
                up.tables[table] = len(df)
                up.report += changes or [f"{table} - already clean, nothing changed"]
        con.commit()
        con.close()

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if not tables:
            raise ValueError("No tables with data were found in the upload.")
        up.tables = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        names = ", ".join(n for n, _ in files)
        up.profile = {
            "name": "upload",
            "title": "Your data",
            "description": f"Chat with {names} in Hebrew or English. The agent writes the SQL, checks its own result, "
                           "and shows the query behind every answer.",
            "business": f"the organisation the uploaded data comes from ({names}) - infer what it does from the tables",
            "notes": describe(con, tables),
            "examples": suggest_examples(con, tables),
            "database_url": f"sqlite:///{path}",
            "answer_key": False,
        }
    except sqlite3.DatabaseError as e:
        raise ValueError(f"Could not read the database: {e}")
    finally:
        con.close()
    return up


def remove(up: Upload):
    shutil.rmtree(up.path.parent, ignore_errors=True)
