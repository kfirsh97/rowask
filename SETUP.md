# Setup guide – from zero to chatting with the database

Three ways to run it, from fastest to fullest:

| | Database | Needs | Time |
|---|---|---|---|
| **[Quick start](#quick-start-no-database-server)** | SQLite copy of the Porsche database, in this repo | Python + an API key | 3 minutes |
| **[Full setup](#full-setup-sql-server)** | The original SQL Server database, in Docker | + Docker | 15 minutes |
| **[Your own database](#your-own-database)** | PostgreSQL, SQL Server, SQLite, MySQL… | + its address and a read-only user | 5 minutes |

## Quick start (no database server)

`data/porsche.sqlite` is a complete copy of the Porsche database in one file, so nothing else is needed:

```bash
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

In `.env`, add your API key (see [step 4](#4-add-your-keys)) and **delete the five `DB_...` lines** – with no
database settings, the agent uses the SQLite copy. Then:

```bash
python check_setup.py              # should list 18 tables and say the connection is read-only
python -m streamlit run app.py
```

Want to see it on a database it was never built for? Set `DB_PROFILE=chinook` in `.env` and restart:
a public music-store sample database (also in `data/`).

## Full setup (SQL Server)

This walks through everything on a Mac (Intel or Apple Silicon). Windows and Linux work the same way;
only the Docker install differs. Plan on about 15 minutes.

## What you need

| | |
|---|---|
| **Docker Desktop** | Runs SQL Server, which has no native macOS version. [Download](https://www.docker.com/products/docker-desktop/) |
| **Python 3.10+** | `python3 --version` to check |
| **An API key** | Gemini (free tier): [aistudio.google.com/apikey](https://aistudio.google.com/apikey) · or Claude (paid credits): [console.anthropic.com](https://console.anthropic.com) |

> **Apple Silicon (M1/M2/M3/M4):** the SQL Server image is built for Intel. In Docker Desktop → Settings → General,
> turn on **"Use Rosetta for x86_64/amd64 emulation on Apple Silicon"**. The `--platform linux/amd64` flag below does the rest.

## 1. Start SQL Server

Pick an admin password (at least 8 characters, with upper case, lower case, a number and a symbol):

```bash
docker run --platform linux/amd64 -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=YourStrong!Passw0rd" \
  -p 1433:1433 --name sqlserver -d mcr.microsoft.com/mssql/server:2022-latest
```

Give it about 20 seconds to start. After a restart of your computer, you only need `docker start sqlserver`.

## 2. Load the database

From the **repository root** (the folder that contains `sql/` and `agent/`), copy the scripts into the
container and run them in order. Replace the password with yours.

```bash
docker cp sql sqlserver:/tmp/sql
docker cp agent/setup sqlserver:/tmp/setup

SQLCMD="docker exec sqlserver /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P YourStrong!Passw0rd -C"
$SQLCMD -i /tmp/sql/01_schema.sql           # creates the Porsche database and its 18 tables
$SQLCMD -i /tmp/sql/02_seed_data.sql        # loads ~58,000 rows - takes a minute or two
$SQLCMD -i /tmp/setup/01_create_reader.sql  # the read-only user the agent connects with
```

Quick check – this should print a number above 4,000:

```bash
$SQLCMD -d Porsche -Q "SELECT COUNT(*) FROM Sales"
```

> If you get `sqlcmd: no such file or directory`, your image has the older tools path:
> use `/opt/mssql-tools/bin/sqlcmd` and drop the `-C`.
>
> Prefer a GUI? Connect [Azure Data Studio](https://learn.microsoft.com/azure-data-studio/) or VS Code's
> SQL Server extension to `localhost,1433` as `sa`, and run the same three files in the same order.

The read-only user's password is set in `agent/setup/01_create_reader.sql` (`Reader!Passw0rd`).
To change it, edit that line before running the file, and use the same value in `.env` below.

## 3. Install the agent

```bash
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Using Anaconda?** If your prompt shows `(base)` next to `(.venv)`, run `conda deactivate` first.
> Otherwise the `streamlit` command can come from Anaconda's older copy instead of this project's, which
> shows up as `module 'streamlit' has no attribute 'pills'`. Running `python -m streamlit` (as below)
> always uses the project's own copy.

## 4. Add your keys

```bash
cp .env.example .env
```

Open `.env` and fill in:

```ini
PROVIDER=gemini                 # or claude
GEMINI_API_KEY=your-key         # no quotes
ANTHROPIC_API_KEY=              # only if PROVIDER=claude
DB_PASSWORD=Reader!Passw0rd     # the read-only user's password from step 2
```

`.env` is in `.gitignore`, so your keys never reach GitHub. Never paste a key into code, an issue or a chat.

## 5. Check everything

```bash
python check_setup.py
```

It checks the database connection, that the agent's user really is read-only, and that your API key works.
Fix anything it reports before going on.

## 6. Run it

**Web chat** (opens http://localhost:8501):

```bash
python -m streamlit run app.py
```

Ask a question, then keep going with follow-ups – the agent remembers the conversation:

> כמה מכירות היו ב-2022? → ומה לגבי 2023? → רק דגמי טורבו → תסביר לי את השאילתה

**No database or key yet?** `DEMO=1 python -m streamlit run app.py` replays two recorded runs.

**Command line:**

```bash
python agent.py "Who were the top 3 sellers by revenue?"   # one question
python agent.py                                            # interactive chat, remembers follow-ups
```

**Tests and accuracy:**

```bash
python -m pytest tests     # no database or API key needed
python eval.py             # scores the agent on 12 questions with known answers
```

## Your own database

The agent reads the structure of whatever database it is connected to, so it works on any database
[SQLAlchemy](https://docs.sqlalchemy.org/en/20/core/engines.html) can reach. Two lines in `.env`:

```ini
DB_PROFILE=generic
DATABASE_URL=postgresql://agent_reader:password@db.example.com:5432/shop
```

| Database | `DATABASE_URL` | Driver |
|---|---|---|
| PostgreSQL | `postgresql://user:password@host:5432/dbname` | included |
| SQL Server | `mssql://user:password@host:1433/dbname` | included |
| SQLite | `sqlite:///path/to/file.db` (relative to `agent/`, or absolute) | built in |
| MySQL / MariaDB | `mysql://user:password@host:3306/dbname` | `pip install pymysql` |

Special characters in the password must be URL-encoded (`!` → `%21`, `@` → `%40`, `#` → `%23`).

**Use a read-only user.** The agent only lets single SELECT queries through, SQLite files are opened read-only
and PostgreSQL sessions start read-only – but a user that cannot write is the layer that does not depend on
this code. `python check_setup.py` tries a write and tells you if it got through. For PostgreSQL:

```sql
CREATE ROLE agent_reader LOGIN PASSWORD 'choose-one';
GRANT CONNECT ON DATABASE shop TO agent_reader;
GRANT USAGE ON SCHEMA public TO agent_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_reader;
```

**Make it accurate with a profile.** `generic` works, but the agent only sees table and column names. A profile
tells it what they mean – on the Porsche database, these notes took accuracy from 11/12 to 12/12. Copy
`profiles/chinook.toml` to `profiles/<name>.toml`, set `DB_PROFILE=<name>`, and fill in:

- `business` – one line: what the business is ("an online furniture store")
- `notes` – currency and units, how values are coded (`status = 3` means cancelled), which date is which,
  how to compute common metrics
- `examples` – four questions shown as buttons on the chat page
- `title` and `description` – the chat page header

### From CSV or Excel files

**Easiest: upload them on the web page.** In the sidebar, under *Your data*, drop in CSV or Excel files (or a SQLite
file). They are cleaned, loaded and described for the agent, and the page shows what was changed. Nothing to set in
`.env`; remove the files to go back to the default database.

To keep a file as a permanent database instead: Save it as CSV (Excel: File → Save As → CSV UTF-8) and turn it into a
SQLite database – one table per file, column types detected from the data, codes with leading zeros kept as text:

```bash
python setup/csv_to_sqlite.py data/superstore.csv                    # -> data/superstore.sqlite
python setup/csv_to_sqlite.py orders.csv customers.csv --out data/shop.sqlite
```

Then in `.env`: `DATABASE_URL=sqlite:///data/shop.sqlite` and `DB_PROFILE=generic` (or your own profile).
`profiles/superstore.toml` is a ready-made profile for the public Superstore sample sales data: put
`superstore.csv` in `data/`, run the first command above, and set `DB_PROFILE=superstore` (with no `DATABASE_URL`).

Good to know: with more than 40 tables, the agent first sees just the table names and then asks for the
columns of the tables it needs. On PostgreSQL it reads the `public` schema. The 12-question answer key
(`eval.py`) is written for the Porsche database on SQL Server – for your own database, write your own questions
and expected queries to measure it.

## Troubleshooting

| You see | What to do |
|---|---|
| `Can't reach the database` / `Login failed` | `docker start sqlserver`, wait 20 seconds, try again. Check `DB_PASSWORD` in `.env`. |
| `module 'streamlit' has no attribute 'pills'` | An old Streamlit is running. `conda deactivate`, then `pip install -r requirements.txt` and use `python -m streamlit run app.py`. |
| `Gemini rejected the API key` | Create a new key at aistudio.google.com/apikey, put it in `.env` without quotes, run `python check_setup.py`. |
| `Model '...' is not available to your key` | `python agent.py --models`, then set `GEMINI_MODEL=` in `.env` to a flash model from the list. |
| `The Anthropic account has no API credit` | API usage is billed separately from a Claude.ai subscription. Add credit, or switch to Gemini in the sidebar. |
| `port is already allocated` on `docker run` | Something already uses port 1433 – often an older `sqlserver` container: `docker start sqlserver` instead. |
| Gemini is slow or `503 / overloaded` | Free-tier models get busy. The agent retries and switches models by itself; give it a moment. |
| `No profile '...'` | `DB_PROFILE` in `.env` names a file that isn't in `profiles/`. Use `porsche`, `chinook`, `generic`, or add your own. |
| `SQLite file not found` | The path in `DATABASE_URL` is relative to the `agent` folder. For the Porsche copy: `python setup/build_sqlite.py` rebuilds it. |
| `The driver for mysql is not installed` | `pip install pymysql` |
| `Query stopped after 30 seconds` | The model gets this back and writes a cheaper query. Very large tables may need indexes or a summary view. |
