"""Setup check: which database, can we reach it, is the connection really read-only, and are the API keys good?

Run:  python check_setup.py
"""
import os
import sys

from sqlalchemy import func, inspect, select, table

import db
from dbprofile import load_profile


def main():
    try:
        print(f"Profile: {load_profile()['name']}  |  Database: {db.describe()}")
        conn = db.connect()
    except (db.SetupError, FileNotFoundError, ValueError) as e:
        sys.exit(f"Setup problem: {e}")

    # 1. Row count per table - proves the connection works and the data is loaded.
    names = inspect(conn).get_table_names()
    print(f"Connected. {len(names)} tables:")
    for name in names[:60]:
        n = conn.execute(select(func.count()).select_from(table(name))).scalar()
        print(f"  {name:<28} {n:>9,}")
    if len(names) > 60:
        print(f"  ... and {len(names) - 60} more")
    conn.close()

    # 2. The agent's connection must NOT be able to write (straight to the database, past the agent's own checks).
    try:
        db.fetch("CREATE TABLE _write_test (x INT)", 1)
        print("\nWARNING: this connection can write to the database! Connect with a read-only user (see SETUP.md).")
        with db.engine().begin() as c:                  # some databases commit table creation at once
            c.exec_driver_sql("DROP TABLE _write_test")
    except Exception:
        print("\nGood: the connection is read-only (a write was blocked).")

    check_gemini()
    check_claude()
def check_gemini():
    # Gemini key: present, not the placeholder, and accepted by Google.
    # We never print a key - only its length and first 4 characters, to spot paste mistakes.
    raw = os.environ.get("GEMINI_API_KEY", "")
    key = raw.strip().strip("'\"").strip()
    if not key or key == "your-key-here":
        print("Gemini API key: not set")
        return
    print(f"Gemini API key: length {len(key)}, starts with '{key[:4]}'")
    try:
        from google import genai
        client = genai.Client(api_key=key)      # keep a reference, or the client closes too early
        next(iter(client.models.list()))
        print("Gemini API key: valid (Google accepted it)")
    except Exception as e:
        print("Gemini API key: REJECTED by Google -", str(e)[:160])


def check_claude():
    raw = os.environ.get("ANTHROPIC_API_KEY", "")
    key = raw.strip().strip("'\"").strip()
    if not key:
        print("Anthropic API key: not set")
        return
    print(f"Anthropic API key: length {len(key)}, starts with '{key[:7]}'")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        client.messages.create(model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=5,
                               messages=[{"role": "user", "content": "ping"}])
        print("Anthropic API key: valid, and the account has credit")
    except Exception as e:
        print("Anthropic API key: problem -", str(e)[:200])
    print("Active provider:", os.environ.get("PROVIDER", "gemini"))

if __name__ == "__main__":
    main()
