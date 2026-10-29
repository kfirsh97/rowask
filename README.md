# RowAsk 🤖 – ask your database in plain language

**RowAsk** is an AI agent that answers business questions about a database — in Hebrew or English. It was built on
the [Porsche sales database](../README.md) and works on any other one (or on your own CSV and Excel files).
It reads the schema, writes the SQL, runs it, checks its own result, fixes its own errors, and answers with the query
it used, so every answer can be verified.

```
$ python agent.py "כמה מכירות היו בכל שנה?"
  step 1: get_schema            -> schema (3,185 chars)
  step 2: run_query SELECT YEAR(Sold_Date) AS Sale_Year, COUNT(*) ... -> 4 rows
  step 3: run_query SELECT COUNT(*) FROM Sales WHERE Sold_Date IS NULL -> 1 rows   (self-check: no sales lost)
מספר המכירות לפי שנה: 2020: 191 · 2021: 1,232 · 2022: 1,628 · 2023: 1,503
```

## How it works

A hand-written agent loop (~100 lines, no agent framework), so every decision is visible:

1. The model gets the question and two tools: **`get_schema`** (tables, columns, foreign keys, business notes)
   and **`run_query`** (one read-only SELECT, max 50 rows back).
2. Each turn the model either calls a tool or answers. SQL errors are sent back to it, so it corrects itself.
3. The loop stops at an answer, or after **8 steps** — it can never loop forever.
4. Every step is logged to `logs/` (question, each tool call, result, final SQL, time).
5. **It's a conversation.** The last 6 question/answer pairs go back to the model with each new message, so
   follow-ups work ("how many sales in 2022?" → "and in 2023?" → "only Turbo models"). Only the text of earlier
   turns is resent, not their tool calls, which keeps requests small. Messages that need no data
   ("thanks", "explain that query") get a direct reply without touching the database.

**Two model providers**, same tools, prompt and safety: Gemini (`PROVIDER=gemini`) or Claude (`PROVIDER=claude`).

**Any database.** Nothing in the agent is tied to Porsche: it reads the structure of whatever database it is
connected to, and the SQL dialect (T-SQL, PostgreSQL, SQLite, MySQL) is told to the model from the connection.
What it knows *about* a database – what the business is, units and codes, example questions – lives in a small
profile file (`profiles/*.toml`). Tested here on three databases: Porsche on SQL Server and on SQLite, and the public
Chinook music-store database on SQLite and PostgreSQL. See [SETUP.md → Your own database](SETUP.md#your-own-database).

## Safety – three layers

| Layer | What it stops |
|---|---|
| Database user with `db_datareader` only | Any write, even if every other check failed |
| `validate_query()` before the database | Anything that is not a single SELECT: `DELETE`, `DROP`, `SELECT INTO`, `EXEC`, stacked statements, keywords hidden in comments — while allowing those words inside strings |
| Limits | 30 s per query, 50 rows to the model, 8 steps per question, 90 s per model call |

## Reliability

The free Gemini tier is often overloaded. The agent retries with growing waits, and if the main model stays busy,
switches to a fallback model **before** the conversation starts (never mid-conversation, since a model may not accept
another model's turns). The SDK's hidden retries are disabled, so every wait is visible.

## Measuring accuracy

Talking to an agent proves nothing — so it is scored against an **answer key**: 12 business questions, each with the
query I wrote for the original course project (graded 100). For each question the agent's SQL and the expected SQL
are both executed and their **full results compared** (row order and extra columns ignored; whole numbers must match exactly).

```
python eval.py --notes off     # schema only
python eval.py --notes on      # schema + business notes (currency, units, value formats)
```

Results (Claude Sonnet 5.5, October 2026):

| Setup | Score | Avg tool calls | Avg time per question |
|---|---|---|---|
| Schema only | 11 / 12 | 3.6 | 14.0 s |
| Schema + business notes | **12 / 12** | **3.0** | **10.0 s** |

With the business notes (currency, units, value formats, how to compute a car's age) the agent answered every
question correctly, with fewer queries and faster. A fair caveat: this is one run of 12 questions, so the
difference between the two rows is a signal, not proof — repeated runs and more questions would tighten it.

Results with full details are saved in `eval_results/`.

## Run it

**Full step-by-step setup, from an empty Mac to a running chat: [SETUP.md](SETUP.md)**
(SQL Server in Docker, loading the data, the read-only user, keys, and a troubleshooting table).

Short version, once the database is running and loaded:

```bash
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # add your API key and the read-only user's password
python check_setup.py          # DB connection, read-only check, API key check
python -m streamlit run app.py # web chat on http://localhost:8501
python -m pytest tests         # 78 tests (3 need a PostgreSQL server), no API key needed
```

## Files

| File | Purpose |
|---|---|
| `agent.py` | The agent loop, prompt, providers, retries and logging |
| `tools.py` | `get_schema`, `run_query`, the SQL safety validator and the business notes |
| `eval.py` · `eval_questions.py` · `compare.py` | Answer key, evaluation runner and result comparison |
| `app.py` | The web chat (Streamlit) |
| `db.py` | Connects to any database (SQLAlchemy); read-only sessions, 30-second query limit, dialect hints for the model |
| `dbprofile.py` · `profiles/` | Per-database knowledge: business line, notes, example questions |
| `upload.py` | Uploads on the web page: reading CSV/Excel, cleaning, building the session's database, describing it |
| `data/` | SQLite copies: Porsche (built by `setup/build_sqlite.py`) and the Chinook sample database |
| `check_setup.py` · `setup/` · `SETUP.md` | Setup check, read-only user, SQLite builder, CSV importer, setup guide |
| `tests/` | Safety, agent loop (with a fake model), real-database tests on SQLite and PostgreSQL, comparison and evaluation |

## Web interface

```bash
python -m streamlit run app.py           # opens http://localhost:8501
DEMO=1 python -m streamlit run app.py    # replays two recorded runs - no database or API key needed
```

A chat: ask in Hebrew or English, then keep going with follow-up questions – the agent remembers the
conversation, and **New conversation** in the sidebar starts over. **Stop** ends a question mid-way: the agent
runs in the background, checks before every model call, and makes no more calls once stopped.

**Upload your own data** in the sidebar – CSV, Excel (every sheet becomes a table) or a SQLite file. CSV and Excel
are cleaned on the way in and the page lists every change: SQL-safe column names, dates as `YYYY-MM-DD`, codes that
Excel turned into `77095.0` back to `77095`, `"$1,234"` and `"45%"` as numbers, curly quotes made plain, empty rows
dropped. Hebrew files saved by Excel on Windows are detected and read correctly. The agent also gets a description of
every column (values, ranges, dates), so it can query data it has never seen. Uploads are for that browser session
only – other people using the same page keep the default database. Watch each step as it happens: the schema read, every query with its row count,
then the answer, the result table and the SQL behind it. The sidebar switches between Claude and Gemini and
turns the business notes on or off.
