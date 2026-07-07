"""Prompt + I/O schema shared by data prep, generation, litmus, and eval."""
from __future__ import annotations

import json
from typing import Dict, List

SYSTEM_PROMPT = (
    "You are an expert middle-school mathematics assessment writer. Given a "
    '"Number" strand math question and its correct answer, produce exactly three '
    "diagnostic distractors (wrong answers) for a multiple-choice version.\n\n"
    "Rules:\n"
    "- Each distractor must correspond to a common student misconception or "
    "procedural error for this type of question.\n"
    "- Each distractor must be exactly the value a student making that misconception "
    "would compute (numerically consistent with the stated misconception).\n"
    "- The three distractor values must all be different, none may equal the correct "
    "answer, and none may be an arbitrary or careless wrong number.\n\n"
    "Respond with ONLY a JSON object, no prose, in this exact schema:\n"
    '{"distractors": [{"misconception": "<short misconception>", "answer": "<value>"}, '
    '{"misconception": "...", "answer": "..."}, {"misconception": "...", "answer": "..."}]}'
)


def build_user(question: str, correct: str, topic: str) -> str:
    return f"Question: {question}\nCorrect answer: {correct}\nTopic: {topic}"


def build_assistant(distractors: List[Dict]) -> str:
    return json.dumps(
        {"distractors": [{"misconception": d["misconception"], "answer": d["answer"]} for d in distractors]},
        ensure_ascii=False,
    )


def parse_distractors(text: str) -> List[Dict]:
    """Best-effort parse of a model response into a list of {misconception, answer}."""
    text = (text or "").strip()
    try:
        start = text.index("{")
        end = text.rindex("}")
        obj = json.loads(text[start : end + 1])
        out = []
        for d in obj.get("distractors", []):
            out.append(
                {
                    "misconception": str(d.get("misconception", "")).strip(),
                    "answer": str(d.get("answer", "")).strip(),
                }
            )
        return out
    except Exception:
        return []
