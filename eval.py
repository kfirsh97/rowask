"""Stage 4-5: measure the agent against the answer key of the active profile (answer_keys/<profile>.py).

For each question: run the agent, take the SQL it used, run that SQL and the expected SQL,
and compare the full results (see compare.py). Prints a score and saves every detail.
Porsche (SQL Server): 12 questions from the original project. Superstore (SQLite): 16 questions, half of them
traps - questions where a wrong query still returns a plausible number.

Run:
  python eval.py                      every question, with schema notes, current PROVIDER
  DB_PROFILE=superstore python eval.py   the Superstore answer key
  python eval.py --notes off          same, without the business notes (the "before" score)
  python eval.py --ids 1,8,11         only some questions
  python eval.py --provider claude    choose the provider for this run
  python eval.py --model gemini-3.5-flash   pin ONE model, no fallback (fair before/after comparison)

A question whose run fails because the provider is overloaded is retried once after a pause.
If it still fails it is reported as ERROR - not as a wrong answer - and left out of the accuracy,
because it says nothing about the agent's reasoning.
"""
import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from compare import results_match
import answer_keys

RESULTS_DIR = Path(__file__).parent / "eval_results"


def top_row_answers(expected, got, sql) -> bool:
    """A "which one is the highest" question answered with a ranked list: the agent showed the top 3 states, or all
    12 months by sales, and the first row is the answer. Accepted only for a one-row answer and a query that sorts."""
    return len(expected) == 1 and len(got) > 1 and bool(re.search(r"\border\s+by\b", sql or "", re.I)) \
        and results_match(expected, got[:1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="comma-separated question ids, e.g. 1,2,3")
    ap.add_argument("--notes", choices=["on", "off"], default="on", help="business notes in the schema")
    ap.add_argument("--provider", choices=["gemini", "claude"], default=os.environ.get("PROVIDER", "claude"))
    ap.add_argument("--pause", type=float, default=5, help="seconds between questions (free-tier rate limits)")
    ap.add_argument("--model", help="pin a single Gemini model and disable fallback")
    args = ap.parse_args()

    os.environ["SCHEMA_NOTES"] = args.notes        # read by tools.get_schema() on every call
    import db
    from dbprofile import load_profile
    profile = load_profile()["name"]
    key = answer_keys.load(profile)
    if key is None:
        raise SystemExit(f"Profile '{profile}' has no answer key. Write one: copy answer_keys/superstore.py to "
                         f"answer_keys/{profile}.py and fill in questions with known answers.")
    dialect, QUESTIONS = key
    if db.dialect() != dialect:
        where = "SQL Server - see SETUP.md" if dialect == "mssql" else dialect
        raise SystemExit(f"The '{profile}' answer key is written for {where}; the agent is now on {db.describe()}. "
                         "Run the evaluation on the database the key was written for.")
    import agent
    from agent import _is_temporary
    if args.model:
        agent.MODEL, agent.FALLBACK_MODELS = args.model, []
    from tools import run_query_all

    questions = QUESTIONS
    if args.ids:
        wanted = {int(x) for x in args.ids.split(",")}
        questions = [q for q in QUESTIONS if q["id"] in wanted]

    print(f"Evaluating {len(questions)} questions | provider={args.provider} | schema notes={args.notes}\n")
    results = []
    for n, q in enumerate(questions, 1):
        if n > 1:
            time.sleep(args.pause)
        print(f"[{q['id']:>2}] {q['question'][:70]}...")
        rec = {"id": q["id"], "question": q["question"], "expected_sql": q["expected_sql"].strip(),
               "trap": q.get("trap")}
        try:
            _, expected = run_query_all(q["expected_sql"])
        except Exception as e:
            rec.update(correct=False, reason=f"answer key query failed: {e}")
            results.append(rec); print("     KEY ERROR -", e); continue

        r = None
        for attempt in (1, 2):
            try:
                r = agent.ask(q["question"], verbose=False, provider=args.provider)
                break
            except Exception as e:
                if _is_temporary(e) and attempt == 1:
                    print("     provider overloaded - retrying this question in 60s")
                    time.sleep(60)
                    continue
                if not _is_temporary(e) and not any(x.get("model") for x in results):
                    # Nothing has worked yet and this is not a passing overload: it is a setup problem
                    # (missing package, bad key, no credit). Stop now instead of failing all 12 questions.
                    raise SystemExit(f"\nSetup problem, stopping the run: {e}\nRun: python check_setup.py")
                kind = "ERROR" if _is_temporary(e) else "FAIL"
                rec.update(correct=None if kind == "ERROR" else False,
                           reason=f"{'provider overloaded' if kind == 'ERROR' else 'agent crashed'}: {str(e)[:160]}")
                print(f"     {kind} - {rec['reason'][:120]}")
        if r is None:
            results.append(rec); continue

        rec.update(model=r["model"], steps=len(r["steps"]), seconds=r["seconds"],
                   agent_sql=r["final_sql"], answer=r["answer"])
        if not r["final_sql"]:
            rec.update(correct=False, reason="agent gave no SQL")
        else:
            try:
                _, got = run_query_all(r["final_sql"])
                ok, note = results_match(expected, got), ""
                if not ok and top_row_answers(expected, got, r["final_sql"]):
                    ok, note = True, f"answer in the first row of an ordered list of {len(got)}"
                rec.update(correct=ok, note=note, reason="" if ok else
                           f"different result: expected {len(expected)} rows, agent returned {len(got)}")
            except Exception as e:
                rec.update(correct=False, reason=f"agent SQL failed: {str(e)[:200]}")
        results.append(rec)
        mark = "PASS" if rec["correct"] else "FAIL"
        print(f"     {mark}  ({rec['steps']} tool calls, {rec['seconds']}s, {rec['model']})"
              + (f" - {rec['reason']}" if rec["reason"] else "") + (f" - {rec['note']}" if rec.get("note") else ""))

    correct = sum(1 for r in results if r["correct"] is True)
    errors = sum(1 for r in results if r["correct"] is None)
    answered = len(results) - errors
    timed = [r for r in results if "seconds" in r]
    traps = [r for r in results if r.get("trap") and r["correct"] is not None]
    summary = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "profile": profile,
        "provider": args.provider,
        "schema_notes": args.notes,
        "score": f"{correct}/{answered}",
        "accuracy": round(correct / answered, 3) if answered else 0,
        "traps": f"{sum(1 for r in traps if r['correct'])}/{len(traps)}" if traps else None,
        "provider_errors": errors,
        "models_used": sorted({r["model"] for r in results if r.get("model")}),
        "avg_tool_calls": round(sum(r["steps"] for r in timed) / len(timed), 1) if timed else None,
        "avg_seconds": round(sum(r["seconds"] for r in timed) / len(timed), 1) if timed else None,
        "results": results,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M}_{profile}_{args.provider}_notes-{args.notes}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nSCORE: {correct}/{answered} correct ({summary['accuracy']:.0%})"
          f" | avg {summary['avg_tool_calls']} tool calls, {summary['avg_seconds']}s per question"
          + (f"\nTrap questions: {summary['traps']} correct" if traps else "")
          + (f"\n{errors} question(s) not scored - provider overloaded. Rerun them with --ids" if errors else ""))
    print(f"Details saved to {out}")


if __name__ == "__main__":
    main()
