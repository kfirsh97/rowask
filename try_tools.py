"""Stage 2 check: try both tools by hand.   Run:  python try_tools.py"""
import json

from tools import get_schema, run_query

schema = get_schema()
print(schema[:1500], "...\n" if len(schema) > 1500 else "\n")
print(f"(schema is {len(schema):,} characters)\n")

print("A normal query:")
print(json.dumps(run_query("SELECT TOP 3 * FROM Departments"), indent=2, ensure_ascii=False))

print("\nA broken query (the agent will get this error and fix it):")
print(run_query("SELECT * FROM Salez"))

print("\nA dangerous query (blocked before it reaches the database):")
print(run_query("DELETE FROM Sales"))
