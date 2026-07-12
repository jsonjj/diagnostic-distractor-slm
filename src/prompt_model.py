"""Interactively prompt the fine-tuned distractor model (loads the HF adapter on the base).

Run on a GPU box (Colab/Kaggle) or any CUDA machine. On CPU it works but is slow.

Usage:
  python -m src.prompt_model                                   # interactive: type questions
  python -m src.prompt_model --q "What is 0.2 + 0.15?" --a 0.35 --topic "Adding and Subtracting with Decimals"
  python -m src.prompt_model --adapter j2ampn/qwen3-4b-distractor-lora-v7

Each answer is scored live with the hardened consistency checker so you see which distractors
actually check out.
"""
from __future__ import annotations

import argparse
import json

from .prompts import SYSTEM_PROMPT, build_user, parse_distractors
from .consistency import computation_consistent

BASE = "unsloth/Qwen3-4B-bnb-4bit"
ADAPTER = "j2ampn/qwen3-4b-distractor-lora-v7"


def load(adapter=ADAPTER):
    from unsloth import FastLanguageModel
    model, tok = FastLanguageModel.from_pretrained(adapter, max_seq_length=2048, load_in_4bit=True)
    FastLanguageModel.for_inference(model)
    return model, tok


def generate(model, tok, question, correct, topic):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user(question, correct, topic)}]
    ids = tok.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True,
                                  enable_thinking=False, return_tensors="pt").to(model.device)
    out = model.generate(input_ids=ids, max_new_tokens=512, do_sample=False)
    return tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True)


def show(txt, question):
    ds = parse_distractors(txt)
    if not ds:
        print("  (no parseable JSON)\n  raw:", txt[:300]); return
    for d in ds:
        cons = computation_consistent(d.get("computation", ""), d.get("answer", ""), question)
        mark = "✓" if cons is True else ("·" if cons is None else "✗")
        print(f"  {mark} {d.get('answer',''):<10} | {d.get('misconception','')[:55]} | {d.get('computation','')}")
    print("  (✓ = computation grounded+consistent, ✗ = fails, · = no checkable computation)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--adapter", default=ADAPTER)
    ap.add_argument("--q"); ap.add_argument("--a"); ap.add_argument("--topic", default="Number")
    args = ap.parse_args()

    print(f"loading {args.adapter} on {BASE} ...")
    model, tok = load(args.adapter)
    print("ready.\n")

    if args.q:
        print(f"Q: {args.q}  (correct: {args.a}, topic: {args.topic})")
        show(generate(model, tok, args.q, args.a, args.topic), args.q)
        return

    print("Interactive. Enter a question, its correct answer, and topic. Ctrl-C to quit.\n")
    while True:
        try:
            q = input("Question: ").strip()
            a = input("Correct answer: ").strip()
            t = input("Topic (e.g. 'Multiplying Fractions'): ").strip() or "Number"
            print()
            show(generate(model, tok, q, a, t), q)
            print()
        except (KeyboardInterrupt, EOFError):
            print("\nbye"); break


if __name__ == "__main__":
    main()
