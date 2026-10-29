"""Stage 4-5: measure the agent against the answer key.

For each question: run the agent, take the SQL it used, run that SQL and the expected SQL,
and compare the full results (see compare.py). Prints a score and saves every detail.

Run:
  python eval.py                      all 12 questions, with schema notes, current PROVIDER
  python eval.py --notes off          same, without the business notes (the "before" score)
  python eval.py --ids 1,8,11         only some questions
  python eval.py --provider claude    choose the provider for this run
"""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

from compare import results_match
from eval_questions import QUESTIONS

RESULTS_DIR = Path(__file__).parent / "eval_results"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="comma-separated question ids, e.g. 1,2,3")
    ap.add_argument("--notes", choices=["on", "off"], default="on", help="business notes in the schema")
    ap.add_argument("--provider", choices=["gemini", "claude"], default=os.environ.get("PROVIDER", "gemini"))
    ap.add_argument("--pause", type=float, default=5, help="seconds between questions (free-tier rate limits)")
    args = ap.parse_args()

    os.environ["SCHEMA_NOTES"] = args.notes        # read by tools.get_schema() on every call
    from agent import ask                          # imported after setting the env var
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
        rec = {"id": q["id"], "question": q["question"], "expected_sql": q["expected_sql"].strip()}
        try:
            _, expected = run_query_all(q["expected_sql"])
        except Exception as e:
            rec.update(correct=False, reason=f"answer key query failed: {e}")
            results.append(rec); print("     KEY ERROR -", e); continue

        try:
            r = ask(q["question"], verbose=False, provider=args.provider)
        except Exception as e:
            rec.update(correct=False, reason=f"agent crashed: {str(e)[:200]}")
            results.append(rec); print("     FAIL - agent crashed:", str(e)[:120]); continue

        rec.update(model=r["model"], steps=len(r["steps"]), seconds=r["seconds"],
                   agent_sql=r["final_sql"], answer=r["answer"])
        if not r["final_sql"]:
            rec.update(correct=False, reason="agent gave no SQL")
        else:
            try:
                _, got = run_query_all(r["final_sql"])
                ok = results_match(expected, got)
                rec.update(correct=ok, reason="" if ok else
                           f"different result: expected {len(expected)} rows, agent returned {len(got)}")
            except Exception as e:
                rec.update(correct=False, reason=f"agent SQL failed: {str(e)[:200]}")
        results.append(rec)
        mark = "PASS" if rec["correct"] else "FAIL"
        print(f"     {mark}  ({rec['steps']} tool calls, {rec['seconds']}s, {rec['model']})"
              + (f" - {rec['reason']}" if rec["reason"] else ""))

    correct = sum(r["correct"] for r in results)
    timed = [r for r in results if "seconds" in r]
    summary = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "provider": args.provider,
        "schema_notes": args.notes,
        "score": f"{correct}/{len(results)}",
        "accuracy": round(correct / len(results), 3) if results else 0,
        "avg_tool_calls": round(sum(r["steps"] for r in timed) / len(timed), 1) if timed else None,
        "avg_seconds": round(sum(r["seconds"] for r in timed) / len(timed), 1) if timed else None,
        "results": results,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M}_{args.provider}_notes-{args.notes}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nSCORE: {correct}/{len(results)} correct ({summary['accuracy']:.0%})"
          f" | avg {summary['avg_tool_calls']} tool calls, {summary['avg_seconds']}s per question")
    print(f"Details saved to {out}")


if __name__ == "__main__":
    main()
