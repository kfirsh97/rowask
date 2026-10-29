# Setup guide – from zero to chatting with the database

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
