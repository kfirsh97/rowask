"""The SQL agent: answers business questions about the Porsche database.

How it works (the "agent loop"):
  1. The model gets the question plus two tools: get_schema and run_query.
  2. Each turn, the model either calls a tool or gives a final answer.
  3. We run the tool and send the result back - including SQL errors,
     so the model can read the error and fix its own query.
  4. The loop stops at a final answer, or after MAX_STEPS (so it can never loop forever).
Every step is written to logs/ so you can see exactly what the agent did and why.

Run:
  python agent.py "Who was the best seller in 2022?"
  python agent.py            (interactive mode)
  python agent.py --models   (list the Gemini models your key can use)
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from tools import get_schema, run_query

load_dotenv()

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_STEPS = 8
LOG_DIR = Path(__file__).parent / "logs"

SYSTEM_PROMPT = """You are a careful data analyst for a Porsche car-sales business.
The data lives in a Microsoft SQL Server database (T-SQL dialect).

How to work:
- Call get_schema first to see the tables, columns and foreign keys. Never guess a column name.
- Write T-SQL: use TOP, not LIMIT; use YEAR(date_column) for years.
- Call run_query to run your query. If you get an error, read it, fix the query and try again.
- Check that the result makes sense before answering (empty result? impossible numbers?
  duplicated rows from a bad JOIN?). If it looks wrong, investigate with another query.
- Only state numbers that came from a query result. Never invent data.
- If the question cannot be answered from this database, say so plainly.

Final answer:
- Answer in the same language as the question, short and clear, with the key numbers.
- After the answer, show the final SQL query you used, in a ```sql code block."""

TOOLS = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="get_schema",
        description="Returns all tables with their columns and data types, and the foreign keys between tables.",
        parameters_json_schema={"type": "object", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="run_query",
        description="Runs ONE read-only T-SQL SELECT query and returns up to 50 rows, or an error message.",
        parameters_json_schema={
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single T-SQL SELECT query."}},
            "required": ["sql"],
        },
    ),
])


def call_tool(name: str, args: dict) -> dict:
    """Run the tool the model asked for. Unknown tools return an error instead of crashing."""
    if name == "get_schema":
        return {"schema": get_schema()}
    if name == "run_query":
        return run_query(args.get("sql", ""))
    return {"error": f"Unknown tool: {name}"}


def _generate(client, contents, config, retries=3):
    """Call the model, waiting and retrying if the free tier's rate limit is hit."""
    for attempt in range(retries + 1):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config)
        except Exception as e:
            if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and attempt < retries:
                wait = 20 * (attempt + 1)
                print(f"   (rate limit - waiting {wait}s)")
                time.sleep(wait)
                continue
            raise


def _summary(result: dict) -> str:
    if "error" in result:
        return f"ERROR: {result['error'][:120]}"
    if "schema" in result:
        return f"schema ({len(result['schema']):,} chars)"
    more = " (truncated)" if result.get("truncated") else ""
    return f"{result['row_count']} rows{more}"


def ask(question: str, client=None, verbose=True) -> dict:
    """Run the agent loop for one question. Returns the answer, the last SQL and every step."""
    client = client or genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[TOOLS],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # we run the loop ourselves
        temperature=0,
    )
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=question)])]
    steps, last_sql, answer = [], None, None
    started = time.time()

    for step in range(1, MAX_STEPS + 1):
        response = _generate(client, contents, config)
        calls = response.function_calls or []

        if not calls:                          # no tool call means the model is done
            answer = response.text
            break

        contents.append(response.candidates[0].content)   # keep the model's turn in the history
        parts = []
        for call in calls:
            args = dict(call.args or {})
            result = call_tool(call.name, args)
            if call.name == "run_query" and "error" not in result:
                last_sql = args.get("sql")
            steps.append({"step": step, "tool": call.name, "args": args, "result": _summary(result)})
            if verbose:
                shown = args.get("sql", "").replace("\n", " ")[:110]
                print(f"  step {step}: {call.name} {shown}\n           -> {_summary(result)}")
            parts.append(types.Part.from_function_response(name=call.name, response=result))
        contents.append(types.Content(role="user", parts=parts))
    else:
        answer = f"Stopped after {MAX_STEPS} steps without a final answer."

    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL,
        "question": question,
        "answer": answer,
        "final_sql": last_sql,
        "steps": steps,
        "seconds": round(time.time() - started, 1),
    }
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / f"{datetime.now():%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def list_models():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if not actions or "generateContent" in actions:
            print(m.name)


def main():
    if "--models" in sys.argv:
        return list_models()
    questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else None
    while True:
        q = questions.pop() if questions else input("\nQuestion (empty to quit): ").strip()
        if not q:
            break
        print(f"\n[{MODEL}] {q}")
        r = ask(q)
        print(f"\n{r['answer']}\n\n({len(r['steps'])} tool calls, {r['seconds']}s)")
        if questions is not None and not questions:
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        if "API_KEY_INVALID" in str(e) or "API key not valid" in str(e):
            sys.exit("Gemini rejected the API key. Create a new one at https://aistudio.google.com/apikey, "
                     "put it in .env as GEMINI_API_KEY=... (no quotes), then run: python check_setup.py")
        raise
