"""The SQL agent: answers business questions about a database - the Porsche sales database by default,
or any other one you point it at (see db.py and profiles/).

How it works (the "agent loop"):
  1. The model gets the question plus two tools: get_schema and run_query.
  2. Each turn, the model either calls a tool or gives a final answer.
  3. We run the tool and send the result back - including SQL errors,
     so the model can read the error and fix its own query.
  4. The loop stops at a final answer, or after MAX_STEPS (so it can never loop forever).
Every step is written to logs/ so you can see exactly what the agent did and why.

Run:
  python agent.py "Who was the best seller in 2022?"
  python agent.py            (interactive chat - follow-up questions see the earlier turns)
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

SYSTEM_PROMPT = """You are a careful data analyst for {business}.
The data lives in this database: {dialect_hint}

How to work:
- Call get_schema first to see the tables, columns and foreign keys. Never guess a column name.
  On a large database it lists the tables only - call it again with the tables you need to see their columns.
- Write SQL for this database's dialect (see above).
- Call run_query to run your query. If you get an error, read it, fix the query and try again.
- Check that the result makes sense before answering (empty result? impossible numbers?
  duplicated rows from a bad JOIN?). If it looks wrong, investigate with another query.
- Only state numbers that came from a query result. Never invent data.
- If the question cannot be answered from this database, say so plainly.

Conversation:
- This is a chat. Earlier messages are the conversation so far - use them to understand
  follow-ups like "and in 2023?", "only Turbo models" or "why is that?", and build on the
  earlier query instead of starting from scratch.
- Not every message needs a query. Greetings, thanks, "what data do you have?" or "explain
  that query" can be answered directly (call get_schema if you need to see what the database holds).
- If a question is genuinely ambiguous, ask one short clarifying question instead of guessing.
- Stay on this database and data analysis; politely decline unrelated requests.

Final answer:
- Answer in the same language as the question, short and clear, with the key numbers.
- If you ran a query for this message, show the final SQL query you used after the answer,
  in a ```sql code block. Do not add a SQL block for a query you did not run in this turn.

Charts:
- When the user asks for a chart, graph, plot, dashboard item or anything visual ("גרף", "תרשים", "ויזואלי") -
  or the answer is a trend over time or a comparison across several groups - add a ```chart block after the
  SQL block, naming columns of your final query:
  {{"type": "bar" | "line" | "area" | "stacked_bar" | "grouped_bar" | "scatter" | "stat",
   "x": "<column>", "y": "<one numeric column>", "color": "<column that splits the series, or null>"}}
  line/area for change over time, bar for comparing groups, grouped/stacked bar for two groupings,
  stat for a single number. One measure only - never two measures with different scales on one chart.
- The app draws the chart from the real result of that query, so never put numbers in the chart block.
  Make the query return exactly the rows the chart needs, sorted sensibly (time ascending, or largest first)."""



def system_prompt() -> str:
    """The prompt for the active database: its business line (from the profile) and its SQL dialect."""
    import db
    from dbprofile import load_profile
    return SYSTEM_PROMPT.format(business=load_profile()["business"], dialect_hint=db.dialect_hint())


HISTORY_TURNS = 6   # how many earlier question/answer pairs the model sees (keeps requests small)

TOOLS = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="get_schema",
        description="Returns the tables with their columns and data types, the foreign keys between tables, "
                    "and business notes. On a large database it lists the tables only; pass `tables` to get "
                    "the columns of specific tables.",
        parameters_json_schema={"type": "object", "properties": {
            "tables": {"type": "array", "items": {"type": "string"},
                       "description": "Optional: only describe these tables."}}},
    ),
    types.FunctionDeclaration(
        name="run_query",
        description="Runs ONE read-only SELECT query (in this database's SQL dialect) and returns up to 50 rows, "
                    "or an error message.",
        parameters_json_schema={
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single SELECT query."}},
            "required": ["sql"],
        },
    ),
])


def call_tool(name: str, args: dict) -> dict:
    """Run the tool the model asked for. Unknown tools return an error instead of crashing."""
    if name == "get_schema":
        tables = args.get("tables")
        return {"schema": get_schema(tables if isinstance(tables, list) and tables else None)}
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


def _history_pairs(history):
    """The last HISTORY_TURNS finished turns as (question, answer) text pairs.
    Only the text goes back to the model - not the earlier tool calls - which keeps every
    request small and lets either provider continue a conversation the other one started."""
    pairs = [(h.get("question"), h.get("answer")) for h in (history or [])]
    return [(q, a) for q, a in pairs if q and a and a != STOPPED][-HISTORY_TURNS:]


def make_client():
    """Gemini client with a hard timeout per request, and the SDK's own hidden retries turned off
    (our _generate does the retrying, so every wait is visible on screen)."""
    key = os.environ["GEMINI_API_KEY"].strip().strip("'\"").strip()
    return genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=REQUEST_TIMEOUT_MS,
        retry_options=types.HttpRetryOptions(attempts=1),
    ))


STOPPED = "Stopped - you stopped this question before the agent finished."


def _run_gemini(question, client, verbose, on_step=None, history=None, should_stop=None):
    """Agent loop on Gemini. Returns (answer, steps, last_sql, model_used)."""
    client = client or make_client()
    config = types.GenerateContentConfig(
        system_instruction=system_prompt(),
        tools=[TOOLS],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # we run the loop ourselves
        # temperature left at the model default: newer Gemini models are tuned for it
        thinking_config=types.ThinkingConfig(thinking_level=os.environ.get("THINKING_LEVEL", "LOW")),
    )
    contents = []
    for q, a in _history_pairs(history):
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=q)]))
        contents.append(types.Content(role="model", parts=[types.Part.from_text(text=a)]))
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))
    steps, last_sql, answer = [], None, None

    model = MODEL
    for step in range(1, MAX_STEPS + 1):
        if should_stop and should_stop():      # checked before every model call: a stop costs no further API calls
            answer = STOPPED
            break
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
            if on_step:
                on_step(steps[-1])          # lets a UI show each step live
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


def _run_claude(question, client, verbose, on_step=None, history=None, should_stop=None):
    """The same agent loop on Claude. Returns (answer, steps, last_sql, model_used).
    Same tools, same prompt, same safety - only the API format differs."""
    if client is None:
        import anthropic
        key = os.environ["ANTHROPIC_API_KEY"].strip().strip("'\"").strip()
        client = anthropic.Anthropic(api_key=key, timeout=REQUEST_TIMEOUT_MS / 1000, max_retries=3)
    messages = []
    for q, a in _history_pairs(history):
        messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    messages.append({"role": "user", "content": question})
    steps, last_sql, answer = [], None, None
    system = system_prompt()
    for step in range(1, MAX_STEPS + 1):
        if should_stop and should_stop():
            answer = STOPPED
            break
        if verbose:
            print(f"  ... thinking ({CLAUDE_MODEL})", flush=True)
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=2048, system=system,
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
            if on_step:
                on_step(steps[-1])          # lets a UI show each step live
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


def ask(question: str, client=None, verbose=True, provider=None, on_step=None, history=None,
        should_stop=None) -> dict:
    """Run the agent for one question on the chosen provider ("gemini" or "claude", from .env PROVIDER).
    history: earlier turns of the same chat, as [{"question": ..., "answer": ...}], oldest first.
    should_stop: optional function, checked before every model call; when it returns True the agent stops.
    Returns the answer, the last SQL and every step, and writes them to the log."""
    provider = (provider or os.environ.get("PROVIDER", "gemini")).lower()
    started = time.time()
    run = _run_claude if provider == "claude" else _run_gemini
    answer, steps, last_sql, model = run(question, client, verbose, on_step, history, should_stop)
    stopped = answer == STOPPED
    # The query that actually produced the answer is the one the model shows in its ```sql block.
    # The last successful query can be a side check (e.g. "are there NULL dates?"), so prefer the block.
    shown = re.findall(r"```sql\s*(.*?)```", answer or "", flags=re.S | re.I)
    final_sql = None if stopped else (shown[-1].strip() if shown else last_sql)
    import charts
    chart, answer = charts.parse_spec(answer)        # the chart block is for the app, not for the reader

    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "question": question,
        "history_turns": len(_history_pairs(history)),
        "answer": answer,
        "final_sql": final_sql,
        "chart": chart,
        "steps": steps,
        "seconds": round(time.time() - started, 1),
        "stopped": stopped,
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
    history = []                       # interactive mode is a conversation: follow-ups see earlier turns
    while True:
        q = questions.pop() if questions else input("\nQuestion (empty to quit): ").strip()
        if not q:
            break
        provider = os.environ.get("PROVIDER", "gemini").lower()
        print(f"\n[{CLAUDE_MODEL if provider == 'claude' else MODEL}] {q}")
        r = ask(q, history=history)
        history.append(r)
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
