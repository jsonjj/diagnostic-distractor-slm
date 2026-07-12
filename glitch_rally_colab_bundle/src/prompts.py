"""Prompt + I/O schema shared by data prep, generation, litmus, and eval.

v4 adds a show-the-work `computation` field to every distractor target:
    {"distractors": [{"misconception": str, "computation": str, "answer": str} x3]}
The order matters (misconception -> computation -> answer): the model first names the
error, then shows the exact arithmetic that error produces for THIS question, then the
answer that arithmetic evaluates to. This supervises the misconception->answer binding
directly and makes consistency programmatically checkable (see src.consistency).

Backward compatibility is preserved: `SYSTEM_PROMPT_LEGACY` is the original v1-v3 prompt
(no computation), `build_assistant` only emits `computation` when a distractor carries it,
and `parse_distractors` reads both old ({misconception, answer}) and new
({misconception, computation, answer}) shapes.
"""
from __future__ import annotations

import json
from typing import Dict, List

# --- v4 system prompt: show-the-work targets (misconception -> computation -> answer) ---
SYSTEM_PROMPT = (
    "You are an expert middle-school mathematics assessment writer. Given a "
    '"Number" strand math question and its correct answer, produce exactly three '
    "diagnostic distractors (wrong answers) for a multiple-choice version.\n\n"
    "For each distractor provide, in this order:\n"
    "- misconception: the specific student misconception or procedural error.\n"
    "- computation: the exact arithmetic a student with THAT misconception performs on "
    "THIS question, written as a plain expression that ends in '= <answer>' "
    "(e.g. \"0.4 \u00f7 0.2 = 2\"). Use only digits, + - \u00d7 \u00f7, parentheses, "
    "decimals, and fractions a/b.\n"
    "- answer: the value the computation evaluates to. It MUST equal the computation's result.\n\n"
    "Rules:\n"
    "- Exactly 3 distractors, each tagged to a distinct misconception.\n"
    "- Each answer is exactly what a student making that misconception would compute "
    "(numerically consistent with the misconception and its shown computation).\n"
    "- The three answers must all be different, and none may equal the correct answer.\n\n"
    "Respond with ONLY a JSON object, no prose, in this exact schema:\n"
    '{"distractors": [{"misconception": "<short misconception>", "computation": "<arithmetic> = <value>", '
    '"answer": "<value>"}, {"misconception": "...", "computation": "...", "answer": "..."}, '
    '{"misconception": "...", "computation": "...", "answer": "..."}]}'
)

# --- legacy system prompt: the exact v1-v3 contract (no computation field) ---
SYSTEM_PROMPT_LEGACY = (
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
    """Serialize distractors to the target JSON string.

    Emits `computation` (between misconception and answer) only for distractors that
    carry a non-empty one, so legacy v1-v3 targets remain byte-for-byte identical.
    """
    out = []
    for d in distractors:
        item = {"misconception": d["misconception"]}
        comp = d.get("computation")
        if comp:
            item["computation"] = comp
        item["answer"] = d["answer"]
        out.append(item)
    return json.dumps({"distractors": out}, ensure_ascii=False)


def parse_distractors(text: str) -> List[Dict]:
    """Best-effort parse of a model response into [{misconception, computation, answer}].

    Backward-compatible: targets/predictions that lack `computation` default it to "".
    Always exposes `misconception` and `answer` so existing eval metrics keep working.
    """
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
                    "computation": str(d.get("computation", "")).strip(),
                    "answer": str(d.get("answer", "")).strip(),
                }
            )
        return out
    except Exception:
        return []
