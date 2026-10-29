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

Provider: set PROVIDER=gemini (default) or PROVIDER=claude in .env.
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from tools import get_schema, run_query

load_dotenv()

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
MAX_STEPS = 8
REQUEST_TIMEOUT_MS = 90_000      # give up on a single model call after 90 seconds
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


FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.1-flash-lite"]   # used only if the main model is overloaded
_working_model = None   # remembers the model that last worked, so a long run stops re-trying a busy one


def _is_temporary(e: Exception) -> bool:
    msg = str(e)
    return any(t in msg for t in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "overloaded",
                                  "504", "DEADLINE_EXCEEDED",
                                  "timed out", "Timeout", "timeout"))


def _generate(client, model, contents, config, retries=3):
    """Call the model, waiting and retrying on temporary errors (rate limit or high demand)."""
    for attempt in range(retries + 1):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as e:
            if _is_temporary(e) and attempt < retries:
                wait = min(10 * (attempt + 1), 45)
                print(f"   ({model} is busy - retrying in {wait}s)")
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


def make_client():
    """Gemini client with a hard timeout per request, and the SDK's own hidden retries turned off
    (our _generate does the retrying, so every wait is visible on screen)."""
    key = os.environ["GEMINI_API_KEY"].strip().strip("'\"").strip()
    return genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=REQUEST_TIMEOUT_MS,
        retry_options=types.HttpRetryOptions(attempts=1),
    ))


def _run_gemini(question, client, verbose):
    """Agent loop on Gemini. Returns (answer, steps, last_sql, model_used)."""
    client = client or make_client()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[TOOLS],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # we run the loop ourselves
        # temperature left at the model default: newer Gemini models are tuned for it
        thinking_config=types.ThinkingConfig(thinking_level=os.environ.get("THINKING_LEVEL", "LOW")),
    )
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=question)])]
    steps, last_sql, answer = [], None, None

    model = MODEL
    for step in range(1, MAX_STEPS + 1):
        if step == 1:
            # If the main model is overloaded, switch before the conversation starts.
            # (We never switch mid-conversation: a model may not accept another model's turns.)
            global _working_model
            order = [MODEL] + FALLBACK_MODELS
            if _working_model in order:          # start from the model that worked last time
                order.remove(_working_model)
                order.insert(0, _working_model)
            for i, candidate in enumerate(order):
                try:
                    if verbose:
                        print(f"  ... thinking ({candidate})", flush=True)
                    # one quick retry on the main model, then move on to a less busy one
                    response = _generate(client, candidate, contents, config, retries=1 if i == 0 else 2)
                    model = _working_model = candidate
                    break
                except Exception as e:
                    if not _is_temporary(e) or i == len(order) - 1:
                        raise
                    print(f"   ({candidate} unavailable - switching to {order[i + 1]})")
        else:
            if verbose:
                print(f"  ... thinking ({model})", flush=True)
            # mid-conversation we cannot switch models, so be more patient here
            response = _generate(client, model, contents, config, retries=5)
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

    return answer, steps, last_sql, model


CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

CLAUDE_TOOLS = [
    {"name": d.name, "description": d.description, "input_schema": d.parameters_json_schema}
    for d in TOOLS.function_declarations
]


def _run_claude(question, client, verbose):
    """The same agent loop on Claude. Returns (answer, steps, last_sql, model_used).
    Same tools, same prompt, same safety - only the API format differs."""
    if client is None:
        import anthropic
        key = os.environ["ANTHROPIC_API_KEY"].strip().strip("'\"").strip()
        client = anthropic.Anthropic(api_key=key, timeout=REQUEST_TIMEOUT_MS / 1000, max_retries=3)
    messages = [{"role": "user", "content": question}]
    steps, last_sql, answer = [], None, None
    for step in range(1, MAX_STEPS + 1):
        if verbose:
            print(f"  ... thinking ({CLAUDE_MODEL})", flush=True)
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=2048, system=SYSTEM_PROMPT,
            tools=CLAUDE_TOOLS, messages=messages,
        )
        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            answer = "".join(b.text for b in response.content if b.type == "text")
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for call in calls:
            args = dict(call.input or {})
            result = call_tool(call.name, args)
            if call.name == "run_query" and "error" not in result:
                last_sql = args.get("sql")
            steps.append({"step": step, "tool": call.name, "args": args, "result": _summary(result)})
            if verbose:
                shown = args.get("sql", "").replace("\n", " ")[:110]
                print(f"  step {step}: {call.name} {shown}\n           -> {_summary(result)}")
            results.append({"type": "tool_result", "tool_use_id": call.id,
                            "content": json.dumps(result, ensure_ascii=False),
                            "is_error": "error" in result})
        messages.append({"role": "user", "content": results})
    else:
        answer = f"Stopped after {MAX_STEPS} steps without a final answer."
    return answer, steps, last_sql, CLAUDE_MODEL


def ask(question: str, client=None, verbose=True, provider=None) -> dict:
    """Run the agent for one question on the chosen provider ("gemini" or "claude", from .env PROVIDER).
    Returns the answer, the last SQL and every step, and writes them to the log."""
    provider = (provider or os.environ.get("PROVIDER", "gemini")).lower()
    started = time.time()
    run = _run_claude if provider == "claude" else _run_gemini
    answer, steps, last_sql, model = run(question, client, verbose)
    # The query that actually produced the answer is the one the model shows in its ```sql block.
    # The last successful query can be a side check (e.g. "are there NULL dates?"), so prefer the block.
    shown = re.findall(r"```sql\s*(.*?)```", answer or "", flags=re.S | re.I)
    final_sql = shown[-1].strip() if shown else last_sql

    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "question": question,
        "answer": answer,
        "final_sql": final_sql,
        "steps": steps,
        "seconds": round(time.time() - started, 1),
    }
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / f"{datetime.now():%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def list_models():
    client = make_client()
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
        provider = os.environ.get("PROVIDER", "gemini").lower()
        print(f"\n[{CLAUDE_MODEL if provider == 'claude' else MODEL}] {q}")
        r = ask(q)
        print(f"\n{r['answer']}\n\n({r['model']}, {len(r['steps'])} tool calls, {r['seconds']}s)")
        if questions is not None and not questions:
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        if "404" in str(e) and "model" in str(e).lower():
            sys.exit(f"Model '{MODEL}' is not available to your key. Run: python agent.py --models, "
                     "then set GEMINI_MODEL=<a flash model from the list> in .env")
        if "credit balance" in str(e).lower():
            sys.exit("Anthropic: your API credit balance is empty. API usage is billed separately from a "
                     "Claude.ai subscription - add credits at https://console.anthropic.com (Billing).")
        if "authentication_error" in str(e) or "invalid x-api-key" in str(e).lower():
            sys.exit("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env (no quotes).")
        if "API_KEY_INVALID" in str(e) or "API key not valid" in str(e):
            sys.exit("Gemini rejected the API key. Create a new one at https://aistudio.google.com/apikey, "
                     "put it in .env as GEMINI_API_KEY=... (no quotes), then run: python check_setup.py")
        raise
