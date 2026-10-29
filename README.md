# SQL Agent 🤖 – ask the Porsche database in plain language

An AI agent that answers business questions about the [Porsche sales database](../README.md) — in Hebrew or English.
It reads the schema, writes T-SQL, runs it, checks its own result, fixes its own errors, and answers with the query it used,
so every answer can be verified.

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

**Two model providers**, same tools, prompt and safety: Gemini (`PROVIDER=gemini`) or Claude (`PROVIDER=claude`).

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

```bash
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # add your API key and the DB password
# create the read-only user: run setup/01_create_reader.sql as sa
python check_setup.py          # DB connection, read-only check, API key check
python agent.py "Who were the top 3 sellers by revenue?"
python -m pytest tests         # 37 tests, no database or API key needed
```

## Files

| File | Purpose |
|---|---|
| `agent.py` | The agent loop, prompt, providers, retries and logging |
| `tools.py` | `get_schema`, `run_query`, the SQL safety validator and the business notes |
| `eval.py` · `eval_questions.py` · `compare.py` | Answer key, evaluation runner and result comparison |
| `db.py` · `check_setup.py` · `setup/` | Database connection, setup check, read-only user |
| `tests/` | Safety, agent loop (with a fake model), comparison and evaluation tests |

## Web interface

```bash
streamlit run app.py           # opens http://localhost:8501
DEMO=1 streamlit run app.py    # replays two recorded runs - no database or API key needed
```

Ask in Hebrew or English and watch each step as it happens: the schema read, every query with its row count,
then the answer, the result table and the SQL behind it. The sidebar switches between Claude and Gemini and
turns the business notes on or off.
