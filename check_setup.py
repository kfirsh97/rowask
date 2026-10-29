"""Stage 1 check: can we reach the database, is the user really read-only, and is the API key set?

Run:  python check_setup.py
"""
import os

from db import connect


def main():
    conn = connect()
    cur = conn.cursor()

    # 1. Row count per table - proves the connection works and the data is loaded.
    cur.execute("""
        SELECT t.name AS table_name, SUM(p.rows) AS row_count
        FROM sys.tables t
        JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
        GROUP BY t.name
        ORDER BY row_count DESC
    """)
    rows = cur.fetchall()
    print(f"Connected. {len(rows)} tables:")
    for r in rows:
        print(f"  {r['table_name']:<28} {r['row_count']:>7,}")

    # 2. The agent's user must NOT be able to write.
    try:
        cur.execute("CREATE TABLE _write_test (x INT)")
        conn.commit()
        print("\nWARNING: the user can write to the database! Use the read-only user.")
        cur.execute("DROP TABLE _write_test")
        conn.commit()
    except Exception:
        print("\nGood: the user is read-only (write was blocked).")

    conn.close()

    # 3. API key: present, not the placeholder, and accepted by Google.
    #    We never print the key - only its length and first 4 characters, to spot paste mistakes.
    raw = os.environ.get("GEMINI_API_KEY", "")
    key = raw.strip().strip("'\"").strip()
    if not key or key == "your-key-here":
        print("Gemini API key: MISSING - put your real key in .env")
        return
    print(f"Gemini API key: length {len(key)}, starts with '{key[:4]}'"
          + (", had quotes or spaces around it (ignored)" if key != raw else ""))
    try:
        from google import genai
        client = genai.Client(api_key=key)      # keep a reference, or the client closes too early
        next(iter(client.models.list()))
        print("Gemini API key: valid (Google accepted it)")
    except Exception as e:
        print("Gemini API key: REJECTED by Google -", str(e)[:160])

if __name__ == "__main__":
    main()
