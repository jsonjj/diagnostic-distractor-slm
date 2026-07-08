"""Teacher-generate + verify show-the-work computations for the real Eedi seed (v4).

The 79 distinct-misconception real SFT records carry only {misconception, answer} per
distractor. Here we ask the teacher (Claude Sonnet 5 via TrueFoundry) for the exact
arithmetic a student with each stated misconception performs on that question to reach
that answer, then VERIFY each programmatically (parse the computation, evaluate its
left-hand side, require it to equal the record's answer). A record is kept ONLY if all
three computations verify -- which doubles as a quality filter (the data audit found
~17% of these reals are numerically inconsistent, so ~60-70 of 79 are expected to
survive).

Output (data/processed/real_train_seed_v4.jsonl): the surviving reals in the same SFT
chat schema, with a `computation` added to every distractor and the v4 SYSTEM_PROMPT.

Cost-aware: 1 call per record + up to MAX_RETRIES retries per record => <= ~3x #records
API calls worst case (typically far fewer; most verify on the first try). Idempotent:

  python -m src.real_computations            # generate (skips if output already exists)
  python -m src.real_computations --force    # regenerate even if output exists
  python -m src.real_computations --limit N  # only first N records (cheap smoke run)
  python -m src.real_computations --workers K
"""
from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from .config import DATA_PROCESSED
from .consistency import computation_consistent, eval_computation, to_value
from .prompts import SYSTEM_PROMPT, build_assistant, build_user

REAL_V4_PATH = DATA_PROCESSED / "real_train_seed_v4.jsonl"
MAX_RETRIES = 2  # attempts = 1 + MAX_RETRIES; keeps total calls <= ~3x #records

_TEACHER_SYSTEM = (
    "You are a mathematics teacher who models student misconceptions precisely. For a "
    "multiple-choice question you are given the correct answer and three wrong options, "
    "each tagged with the specific misconception that produces it. For each wrong option "
    "you write the exact arithmetic a student with that misconception performs on THIS "
    "question to arrive at THAT wrong answer."
)


def _load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _parse_user(u: str):
    """Recover (question, correct, topic) from a build_user() string."""
    body = u.split("Question: ", 1)[-1]
    body, topic = body.rsplit("\nTopic: ", 1)
    question, correct = body.rsplit("\nCorrect answer: ", 1)
    return question.strip(), correct.strip(), topic.strip()


def _extract_json_obj(text):
    text = (text or "").strip()
    try:
        return json.loads(text[text.index("{") : text.rindex("}") + 1])
    except Exception:
        return None


def _prompt(question, correct, topic, distractors, retry_note=""):
    lines = "\n".join(
        f'{i + 1}. misconception: "{d["misconception"]}"  ->  student answer: "{d["answer"]}"'
        for i, d in enumerate(distractors)
    )
    return (
        f"Question: {question}\nCorrect answer: {correct}\nTopic: {topic}\n\n"
        "The three wrong options below are each the value a student with the stated "
        f"misconception computes:\n{lines}\n\n"
        "For EACH option, write the exact arithmetic that student performs on THIS question "
        "to reach THAT answer, as a single expression ending in '= <that answer>'. Use ONLY "
        "digits, + - \u00d7 \u00f7, parentheses, decimals, and fractions written as a/b. No words, "
        "no units, no percent signs, no variables. Each expression MUST evaluate to exactly the "
        f"given student answer.{retry_note}\n"
        'Return ONLY compact JSON: {"computations": ["expr = ans", "expr = ans", "expr = ans"]} '
        "with exactly 3 strings in the SAME order as the options."
    )


def _verify(computations, distractors):
    """Return list of clean 'lhs = answer' strings if all 3 verify, else None."""
    if not isinstance(computations, list) or len(computations) != len(distractors):
        return None
    out = []
    for comp, d in zip(computations, distractors):
        if computation_consistent(comp, d["answer"]) is not True:
            return None
        lhs = str(comp).split("=", 1)[0].strip()
        out.append(f"{lhs} = {d['answer']}")
    return out


def _process(rec, chat):
    """Generate + verify computations for one real record. Returns (sft_or_None, n_calls, detail)."""
    question, correct, topic = _parse_user(rec["user"])
    distractors = json.loads(rec["assistant"]).get("distractors", [])
    calls = 0
    verified = None
    retry_note = ""
    for _ in range(1 + MAX_RETRIES):
        usr = _prompt(question, correct, topic, distractors, retry_note)
        calls += 1
        try:
            out = chat(
                [{"role": "system", "content": _TEACHER_SYSTEM}, {"role": "user", "content": usr}],
                max_tokens=400,
            )
        except Exception as e:  # noqa: BLE001
            retry_note = ""
            _ = e
            continue
        obj = _extract_json_obj(out)
        comps = obj.get("computations") if isinstance(obj, dict) else None
        verified = _verify(comps, distractors)
        if verified is not None:
            break
        # nudge the retry with the exact targets it must hit
        bad = ", ".join(f'#{i + 1} must equal {d["answer"]}' for i, d in enumerate(distractors))
        retry_note = f" Your previous attempt did not evaluate correctly for every option ({bad})."
    if verified is None:
        return None, calls, {"id": rec.get("meta", {}).get("id"), "kept": False}
    sft = {
        "system": SYSTEM_PROMPT,
        "user": rec["user"],
        "assistant": build_assistant(
            [
                {"misconception": d["misconception"], "computation": c, "answer": d["answer"]}
                for d, c in zip(distractors, verified)
            ]
        ),
        "meta": rec.get("meta", {}),
    }
    return sft, calls, {"id": rec.get("meta", {}).get("id"), "kept": True}


def generate_real_v4(limit: int = 0, workers: int = 8, verbose: bool = True):
    """Teacher-generate + verify computations for the distinct-misconception real seed.

    Returns (survivors: list[sft], stats: dict). Requires TFY_API_KEY (imported lazily).
    """
    from .generate import load_real_distinct  # local import avoids a circular import at module load
    from .tfy_client import chat

    reals, _total = load_real_distinct()
    if limit:
        reals = reals[:limit]

    survivors = [None] * len(reals)
    total_calls = 0
    lock = threading.Lock()
    done = 0

    def work(i_rec):
        i, rec = i_rec
        sft, calls, detail = _process(rec, chat)
        return i, sft, calls, detail

    if verbose:
        print(f"Teacher-generating computations for {len(reals)} distinct-misconception reals "
              f"({workers} workers, <= ~{len(reals) * (1 + MAX_RETRIES)} calls max)...", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, sft, calls, detail in ex.map(work, list(enumerate(reals))):
            survivors[i] = sft
            with lock:
                total_calls += calls
                done += 1
            if verbose and done % 20 == 0:
                print(f"  {done}/{len(reals)} processed", flush=True)

    kept = [s for s in survivors if s is not None]
    stats = {
        "candidates": len(reals),
        "survivors": len(kept),
        "dropped": len(reals) - len(kept),
        "api_calls": total_calls,
    }
    return kept, stats


def ensure_real_v4(regenerate: bool = False):
    """Return the surviving real-with-computation SFT records, generating + caching if needed."""
    if REAL_V4_PATH.exists() and not regenerate:
        return _load_jsonl(REAL_V4_PATH)
    kept, stats = generate_real_v4()
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    with open(REAL_V4_PATH, "w", encoding="utf-8") as f:
        for rec in kept:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"real v4: kept {stats['survivors']}/{stats['candidates']} "
          f"(dropped {stats['dropped']}; {stats['api_calls']} API calls) -> {REAL_V4_PATH.name}")
    return kept


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="regenerate even if the cache exists")
    ap.add_argument("--limit", type=int, default=0, help="only process the first N records (smoke)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if REAL_V4_PATH.exists() and not args.force and not args.limit:
        recs = _load_jsonl(REAL_V4_PATH)
        print(f"{REAL_V4_PATH} already exists with {len(recs)} records. Use --force to regenerate.")
        return

    kept, stats = generate_real_v4(limit=args.limit, workers=args.workers)
    if not args.limit:
        DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
        with open(REAL_V4_PATH, "w", encoding="utf-8") as f:
            for rec in kept:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("\n=== real computations (teacher + verify) ===")
    print(f"candidates (distinct-misconception reals): {stats['candidates']}")
    print(f"survivors (all 3 computations verified)  : {stats['survivors']} "
          f"({100 * stats['survivors'] / stats['candidates']:.1f}%)")
    print(f"dropped (>=1 unverifiable computation)    : {stats['dropped']}")
    print(f"total API calls                          : {stats['api_calls']}")
    if not args.limit:
        print(f"  -> {REAL_V4_PATH}")


if __name__ == "__main__":
    main()
