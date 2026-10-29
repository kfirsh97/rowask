"""Stage 4-5: measure the agent against the answer key.

For each question: run the agent, take the SQL it used, run that SQL and the expected SQL,
and compare the full results (see compare.py). Prints a score and saves every detail.

Run:
  python eval.py                      all 12 questions, with schema notes, current PROVIDER
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
    ap.add_argument("--model", help="pin a single Gemini model and disable fallback")
    args = ap.parse_args()

    os.environ["SCHEMA_NOTES"] = args.notes        # read by tools.get_schema() on every call
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
        rec = {"id": q["id"], "question": q["question"], "expected_sql": q["expected_sql"].strip()}
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
                ok = results_match(expected, got)
                rec.update(correct=ok, reason="" if ok else
                           f"different result: expected {len(expected)} rows, agent returned {len(got)}")
            except Exception as e:
                rec.update(correct=False, reason=f"agent SQL failed: {str(e)[:200]}")
        results.append(rec)
        mark = "PASS" if rec["correct"] else "FAIL"
        print(f"     {mark}  ({rec['steps']} tool calls, {rec['seconds']}s, {rec['model']})"
              + (f" - {rec['reason']}" if rec["reason"] else ""))

    correct = sum(1 for r in results if r["correct"] is True)
    errors = sum(1 for r in results if r["correct"] is None)
    answered = len(results) - errors
    timed = [r for r in results if "seconds" in r]
    summary = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "provider": args.provider,
        "schema_notes": args.notes,
        "score": f"{correct}/{answered}",
        "accuracy": round(correct / answered, 3) if answered else 0,
        "provider_errors": errors,
        "models_used": sorted({r["model"] for r in results if r.get("model")}),
        "avg_tool_calls": round(sum(r["steps"] for r in timed) / len(timed), 1) if timed else None,
        "avg_seconds": round(sum(r["seconds"] for r in timed) / len(timed), 1) if timed else None,
        "results": results,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M}_{args.provider}_notes-{args.notes}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nSCORE: {correct}/{answered} correct ({summary['accuracy']:.0%})"
          f" | avg {summary['avg_tool_calls']} tool calls, {summary['avg_seconds']}s per question"
          + (f"\n{errors} question(s) not scored - provider overloaded. Rerun them with --ids" if errors else ""))
    print(f"Details saved to {out}")


if __name__ == "__main__":
    main()
