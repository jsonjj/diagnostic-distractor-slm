"""Build a deterministic, source-blind local human-review package.

Sampling uses frozen question metadata only. Candidate outputs are joined after
selection, and source order is independently randomized per sampled item.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Mapping, Sequence


SAMPLE_SEED = 20260713
ORDER_SEED = 20260714
SAMPLE_SIZE = 24
RUBRIC_VERSION = "blinded-set-rubric-v1"

TOPIC_FAMILIES = {
    "whole-number operations & place value": {
        "Place Value",
        "Mental Addition and Subtraction",
        "Mental Multiplication and Division",
    },
    "fractions": {
        "Adding and Subtracting Fractions",
        "Simplifying Fractions",
        "Fractions of an Amount",
        "Equivalent Fractions",
        "Ordering Fractions",
        "Multiplying Fractions",
        "Dividing Fractions",
        "Converting Mixed Number and Improper Fractions",
    },
    "decimals": {
        "Ordering Decimals",
        "Rounding to Decimal Places",
        "Multiplying and Dividing with Decimals",
        "Adding and Subtracting with Decimals",
        "Converting between Fractions and Decimals",
    },
    "percentages & proportional conversion": {
        "Converting between Fractions and Percentages",
        "Percentages of an Amount",
        "Converting between Decimals and Percentages",
    },
    "negative numbers": {
        "Adding and Subtracting Negative Numbers",
        "Multiplying and Dividing Negative Numbers",
        "Ordering Negative Numbers",
    },
    "order, powers & roots": {
        "BIDMAS",
        "Laws of Indices",
        "Square Roots, Cube Roots, etc",
        "Squares, Cubes, etc",
    },
    "factors & multiples": {
        "Factors and Highest Common Factor",
        "Multiples and Lowest Common Multiple",
    },
    "rounding, estimation & standard form": {
        "Standard Form",
        "Rounding to Significant Figures",
        "Estimation",
        "Rounding to the Nearest Whole (10, 100, etc)",
    },
}

ISSUE_TYPES = (
    "mathematically_inconsistent",
    "correct_answer_collision",
    "duplicate",
    "nonsense",
)


def _stable_hash(*parts: object) -> str:
    payload = ":".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_jsonl(path: str | Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"expected JSON objects in {path}")
    return rows


def _topic_family(topic: object) -> str:
    topic_text = str(topic or "").strip()
    matches = [
        family for family, topics in TOPIC_FAMILIES.items() if topic_text in topics
    ]
    if len(matches) != 1:
        raise ValueError(f"topic does not map to exactly one review family: {topic_text!r}")
    return matches[0]


def _answer_shape(correct: object) -> str:
    value = str(correct or "").strip()
    folded = value.casefold()
    if not value:
        return "empty"
    if "![" in value:
        return "visual"
    if any(token in value for token in ("<", ">", "=")) and not re.fullmatch(
        r"-?\d+(?:\.\d+)?", value
    ):
        return "comparison"
    if any(token in folded for token in ("only", "neither", "both", "true")):
        return "categorical"
    if "%" in value or "£" in value or "pounds" in folded:
        return "percent-or-money"
    if "/" in value or "\\frac" in value:
        return "fraction-or-mixed"
    if re.fullmatch(r"-\d+", value):
        return "negative-integer"
    if re.fullmatch(r"-?\d+\.\d+(?:\.\.\.|ldots)?", value):
        return "decimal"
    if re.fullmatch(r"\d+", value):
        return "integer"
    return "compound-or-text"


def _challenge_score(row: Mapping[str, object]) -> int:
    question = str(row.get("question", ""))
    construct = str(row.get("construct", ""))
    combined = f"{question} {construct}".casefold()
    score = 0
    if "![" in question:
        score += 3
    if len(question) >= 180:
        score += 2
    elif len(question) >= 100:
        score += 1
    if re.search(r"\b(tom|katie|jo|paul|says|argu|conjecture)\b", combined):
        score += 2
    if re.search(
        r"\b(convert|order|estimate|round|missing|mixed|multi-step|"
        r"standard form|inequal|closest|ascending|descending|reciprocal)\b",
        combined,
    ):
        score += 2
    operator_count = len(
        re.findall(r"(?:[+\-×÷*/]|\\times|\\div|\\frac)", question)
    )
    if operator_count >= 3:
        score += 2
    elif operator_count >= 2:
        score += 1
    if re.search(r"(?:-\d|negative|below zero|through zero)", combined):
        score += 1
    if re.search(r"(?:\\frac|/|%|decimal|fraction|percentage)", combined):
        score += 1
    if _answer_shape(row.get("correct")) in {
        "visual",
        "categorical",
        "comparison",
        "compound-or-text",
    }:
        score += 2
    return score


def _three_chunks(rows: Sequence[dict]) -> list[list[dict]]:
    quotient, remainder = divmod(len(rows), 3)
    sizes = [
        quotient + (1 if index < remainder else 0) for index in range(3)
    ]
    chunks: list[list[dict]] = []
    start = 0
    for size in sizes:
        chunks.append(list(rows[start : start + size]))
        start += size
    return chunks


def select_sample(
    gold: Sequence[dict],
    *,
    sample_size: int = SAMPLE_SIZE,
    seed: int = SAMPLE_SEED,
) -> list[dict]:
    """Select 3 metadata-complexity bands from each of 8 topic families.

    Candidate outputs and automatic metric results are intentionally not inputs.
    """
    family_count = len(TOPIC_FAMILIES)
    if sample_size != family_count * 3:
        raise ValueError(
            f"stratified design requires {family_count * 3} items, got {sample_size}"
        )
    ids = [str(row.get("id", "")).strip() for row in gold]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("gold IDs must be non-empty and unique")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in gold:
        enriched = dict(row)
        enriched["review_family"] = _topic_family(row.get("topic"))
        enriched["challenge_score"] = _challenge_score(row)
        enriched["answer_shape"] = _answer_shape(row.get("correct"))
        grouped[enriched["review_family"]].append(enriched)

    missing = [
        family
        for family in TOPIC_FAMILIES
        if len(grouped.get(family, [])) < 3
    ]
    if missing:
        raise ValueError(f"families lack three sample candidates: {missing}")

    selected = []
    bands = ("lower", "middle", "upper")
    for family in TOPIC_FAMILIES:
        ranked = sorted(
            grouped[family],
            key=lambda row: (
                row["challenge_score"],
                _stable_hash(seed, "rank", family, row["id"]),
            ),
        )
        for band, chunk in zip(bands, _three_chunks(ranked), strict=True):
            if not chunk:
                raise ValueError(f"empty {band} challenge band for {family}")
            chosen = min(
                chunk,
                key=lambda row: _stable_hash(
                    seed,
                    "choose",
                    family,
                    band,
                    row["id"],
                ),
            )
            selected.append({**chosen, "challenge_band": band})

    return sorted(
        selected,
        key=lambda row: _stable_hash(seed, "review-order", row["id"]),
    )


def _prediction_index(
    rows: Sequence[dict],
    *,
    expected_ids: set[str],
    label: str,
) -> dict[str, dict]:
    ids = [str(row.get("id", "")).strip() for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError(f"{label} prediction IDs must be non-empty and unique")
    index = {row_id: row for row_id, row in zip(ids, rows, strict=True)}
    missing = sorted(expected_ids - set(index))
    if missing:
        raise ValueError(f"{label} is missing sampled IDs: {missing}")
    return index


def _public_distractors(prediction: Mapping[str, object]) -> list[dict]:
    distractors = prediction.get("distractors")
    if not isinstance(distractors, list):
        return []
    public = []
    for distractor in distractors:
        if not isinstance(distractor, dict):
            public.append(
                {
                    "misconception": "",
                    "computation": "",
                    "answer": str(distractor),
                }
            )
            continue
        public.append(
            {
                "misconception": str(distractor.get("misconception", "")),
                "computation": str(distractor.get("computation", "")),
                "answer": str(distractor.get("answer", "")),
            }
        )
    return public


def build_review_bundle(
    gold: Sequence[dict],
    predictions: Mapping[str, Sequence[dict]],
    *,
    sample_size: int = SAMPLE_SIZE,
    sample_seed: int = SAMPLE_SEED,
    order_seed: int = ORDER_SEED,
) -> tuple[list[dict], dict]:
    """Join two systems to the frozen sample and return public data + secret key."""
    source_labels = list(predictions)
    if len(source_labels) != 2 or len(set(source_labels)) != 2:
        raise ValueError("exactly two distinctly named prediction sources are required")
    sample = select_sample(gold, sample_size=sample_size, seed=sample_seed)
    sampled_ids = {str(row["id"]) for row in sample}
    indexes = {
        label: _prediction_index(
            predictions[label],
            expected_ids=sampled_ids,
            label=label,
        )
        for label in source_labels
    }

    public_items = []
    key_items = []
    for position, row in enumerate(sample, start=1):
        review_item_id = f"R{position:02d}"
        source_id = str(row["id"])
        first, second = source_labels
        if int(_stable_hash(order_seed, "candidate-order", source_id), 16) % 2:
            first, second = second, first
        public_items.append(
            {
                "review_item_id": review_item_id,
                "topic": str(row.get("topic", "")),
                "question_html": render_math_text(str(row.get("question", ""))),
                "correct_answer": str(row.get("correct", "")),
                "candidate_a": _public_distractors(indexes[first][source_id]),
                "candidate_b": _public_distractors(indexes[second][source_id]),
            }
        )
        key_items.append(
            {
                "review_item_id": review_item_id,
                "source_id": source_id,
                "topic": str(row.get("topic", "")),
                "review_family": row["review_family"],
                "challenge_band": row["challenge_band"],
                "challenge_score": row["challenge_score"],
                "answer_shape": row["answer_shape"],
                "candidate_a_source": first,
                "candidate_b_source": second,
            }
        )

    key = {
        "schema_version": "blinded-review-key-v1",
        "warning": (
            "OWNER ONLY — do not open until every reviewer has exported and "
            "returned final ratings."
        ),
        "sample_design": {
            "sample_size": sample_size,
            "sample_seed": sample_seed,
            "candidate_order_seed": order_seed,
            "rule": (
                "Map frozen questions into eight predeclared Number families; "
                "rank within each family using question/construct/correct-answer "
                "complexity only; split into lower/middle/upper thirds; choose "
                "one SHA-256-ranked item per family-band; independently assign "
                "candidate order per item using a second SHA-256 seed."
            ),
            "selection_uses_candidate_outputs": False,
            "family_distribution": dict(
                sorted(Counter(row["review_family"] for row in key_items).items())
            ),
            "challenge_distribution": dict(
                sorted(Counter(row["challenge_band"] for row in key_items).items())
            ),
            "answer_shape_distribution": dict(
                sorted(Counter(row["answer_shape"] for row in key_items).items())
            ),
            "topic_distribution": dict(
                sorted(Counter(row["topic"] for row in key_items).items())
            ),
        },
        "source_labels": source_labels,
        "items": key_items,
    }
    return public_items, key


_TEX_SYMBOLS = {
    "times": "×",
    "div": "÷",
    "cdot": "·",
    "pm": "±",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "neq": "≠",
    "equiv": "≡",
    "approx": "≈",
    "pounds": "£",
    "%": "%",
    "square": "□",
    "bigstar": "★",
    "ldots": "…",
    "quad": " ",
    "hspace": " ",
}
_WRAPPER_COMMANDS = {"mathbf", "mathrm", "text", "textbf", "operatorname"}


class _TexParser:
    def __init__(self, value: str):
        self.value = value
        self.position = 0

    def parse(self, stop: str | None = None) -> str:
        nodes: list[str] = []
        text_buffer = ""

        def flush_text() -> None:
            nonlocal text_buffer
            if text_buffer:
                nodes.append(f"<mtext>{html.escape(text_buffer)}</mtext>")
                text_buffer = ""

        while self.position < len(self.value):
            char = self.value[self.position]
            if stop and char == stop:
                break
            if char.isspace():
                text_buffer += " "
                self.position += 1
                continue
            flush_text()
            if char == "\\":
                node = self._command()
            elif char == "{":
                self.position += 1
                node = f"<mrow>{self.parse('}')}</mrow>"
                if self.position < len(self.value) and self.value[self.position] == "}":
                    self.position += 1
            elif char in "^_":
                self.position += 1
                exponent = self._atom()
                if nodes:
                    base = nodes.pop()
                    tag = "msup" if char == "^" else "msub"
                    node = f"<{tag}>{base}{exponent}</{tag}>"
                else:
                    node = f"<mo>{html.escape(char)}</mo>{exponent}"
            elif char.isdigit() or (
                char == "."
                and self.position + 1 < len(self.value)
                and self.value[self.position + 1].isdigit()
            ):
                node = self._number()
            elif char.isalpha():
                node = self._identifier()
            else:
                self.position += 1
                node = f"<mo>{html.escape(char)}</mo>"
            nodes.append(node)
        flush_text()
        return "".join(nodes)

    def _number(self) -> str:
        start = self.position
        while self.position < len(self.value) and (
            self.value[self.position].isdigit()
            or self.value[self.position] in ".,"
        ):
            self.position += 1
        return f"<mn>{html.escape(self.value[start:self.position])}</mn>"

    def _identifier(self) -> str:
        start = self.position
        while self.position < len(self.value) and self.value[self.position].isalpha():
            self.position += 1
        value = self.value[start:self.position]
        tag = "mi" if len(value) <= 2 else "mtext"
        return f"<{tag}>{html.escape(value)}</{tag}>"

    def _group(self) -> str:
        while self.position < len(self.value) and self.value[self.position].isspace():
            self.position += 1
        if self.position < len(self.value) and self.value[self.position] == "{":
            self.position += 1
            content = self.parse("}")
            if self.position < len(self.value) and self.value[self.position] == "}":
                self.position += 1
            return f"<mrow>{content}</mrow>"
        return self._atom()

    def _raw_group(self) -> str:
        while self.position < len(self.value) and self.value[self.position].isspace():
            self.position += 1
        if self.position >= len(self.value) or self.value[self.position] != "{":
            return ""
        self.position += 1
        depth = 1
        start = self.position
        while self.position < len(self.value) and depth:
            if self.value[self.position] == "{":
                depth += 1
            elif self.value[self.position] == "}":
                depth -= 1
                if depth == 0:
                    raw = self.value[start:self.position]
                    self.position += 1
                    return raw
            self.position += 1
        return self.value[start:]

    def _atom(self) -> str:
        while self.position < len(self.value) and self.value[self.position].isspace():
            self.position += 1
        if self.position >= len(self.value):
            return "<mrow></mrow>"
        if self.value[self.position] == "{":
            return self._group()
        if self.value[self.position] == "\\":
            return self._command()
        if self.value[self.position].isdigit():
            return self._number()
        char = self.value[self.position]
        self.position += 1
        tag = "mi" if char.isalpha() else "mo"
        return f"<{tag}>{html.escape(char)}</{tag}>"

    def _command(self) -> str:
        self.position += 1
        if self.position >= len(self.value):
            return "<mo>\\</mo>"
        if not self.value[self.position].isalpha():
            char = self.value[self.position]
            self.position += 1
            return f"<mo>{html.escape(_TEX_SYMBOLS.get(char, char))}</mo>"
        start = self.position
        while self.position < len(self.value) and self.value[self.position].isalpha():
            self.position += 1
        command = self.value[start:self.position]
        if command == "frac":
            numerator = self._group()
            denominator = self._group()
            return f"<mfrac>{numerator}{denominator}</mfrac>"
        if command == "sqrt":
            index = ""
            while self.position < len(self.value) and self.value[self.position].isspace():
                self.position += 1
            if self.position < len(self.value) and self.value[self.position] == "[":
                self.position += 1
                start = self.position
                while self.position < len(self.value) and self.value[self.position] != "]":
                    self.position += 1
                index = self.value[start:self.position]
                if self.position < len(self.value):
                    self.position += 1
            radicand = self._group()
            if index:
                index_math = _TexParser(index).parse()
                return f"<mroot>{radicand}<mrow>{index_math}</mrow></mroot>"
            return f"<msqrt>{radicand}</msqrt>"
        if command in _WRAPPER_COMMANDS:
            return self._group()
        if command == "color":
            self._raw_group()
            return ""
        if command in {"left", "right"}:
            return ""
        if command in {"begin", "end"}:
            environment = self._raw_group()
            return f"<mtext>{html.escape(environment)}</mtext>"
        if command == "fbox":
            return self._group()
        if command in _TEX_SYMBOLS:
            symbol = _TEX_SYMBOLS[command]
            if command == "hspace":
                self._raw_group()
            return f"<mo>{html.escape(symbol)}</mo>"
        return f"<mtext>{html.escape(command)}</mtext>"


def _tex_to_mathml(tex: str, *, display: bool) -> str:
    content = _TexParser(tex.strip()).parse()
    display_value = "block" if display else "inline"
    return (
        '<math xmlns="http://www.w3.org/1998/Math/MathML" '
        f'display="{display_value}">'
        f"<mrow>{content}</mrow></math>"
    )


def render_math_text(value: str) -> str:
    """Render Markdown image descriptions and common TeX as offline MathML."""
    visual_blocks: list[str] = []

    def visual_replacement(match: re.Match[str]) -> str:
        index = len(visual_blocks)
        alt = re.sub(r"\s+", " ", match.group(1)).strip()
        visual_blocks.append(
            '<aside class="visual-prompt"><strong>Visual prompt:</strong> '
            f"{html.escape(alt)}</aside>"
        )
        return f"\x00VISUAL{index}\x00"

    text = re.sub(r"!\[(.*?)\]\(\)", visual_replacement, value, flags=re.DOTALL)
    pattern = re.compile(r"\\\((.*?)\\\)|\\\[(.*?)\\\]", flags=re.DOTALL)
    output = []
    cursor = 0
    for match in pattern.finditer(text):
        plain = html.escape(text[cursor : match.start()]).replace("\n", "<br>")
        output.append(plain)
        inline_tex, display_tex = match.groups()
        output.append(
            _tex_to_mathml(
                inline_tex if inline_tex is not None else display_tex,
                display=display_tex is not None,
            )
        )
        cursor = match.end()
    output.append(html.escape(text[cursor:]).replace("\n", "<br>"))
    rendered = "".join(output)
    for index, block in enumerate(visual_blocks):
        rendered = rendered.replace(
            html.escape(f"\x00VISUAL{index}\x00"),
            block,
        )
    return rendered


_REVIEW_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Blinded distractor review</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #17213a;
      --muted: #61708d;
      --faint: #8d98ad;
      --canvas: #f4f6fb;
      --paper: #ffffff;
      --paper-soft: #f9faff;
      --line: #dbe1ee;
      --line-strong: #bbc6da;
      --accent: #4866e8;
      --accent-soft: #e9edff;
      --teal: #087f78;
      --teal-soft: #e3f6f3;
      --warning: #9b5d12;
      --warning-soft: #fff4df;
      --danger: #b43d4d;
      --danger-soft: #fdecef;
      --radius-sm: 8px;
      --radius-md: 14px;
      --radius-lg: 22px;
      --shadow: 0 18px 50px rgba(23, 33, 58, 0.09);
      --font-body: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-display: "Avenir Next", "Segoe UI", ui-sans-serif, sans-serif;
      --font-math: "STIX Two Math", "Cambria Math", serif;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 8% 0%, #e9edff 0, transparent 34rem),
        var(--canvas);
      color: var(--ink);
      font-family: var(--font-body);
      line-height: 1.5;
    }
    button, input, textarea { font: inherit; }
    button, label, input { -webkit-tap-highlight-color: transparent; }
    button:focus-visible, input:focus-visible, textarea:focus-visible {
      outline: 3px solid rgba(72, 102, 232, 0.28);
      outline-offset: 2px;
    }
    .shell { width: min(1480px, 100%); margin: 0 auto; padding: 20px; }
    .topbar {
      display: flex; align-items: center; gap: 16px; justify-content: space-between;
      margin-bottom: 18px;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand-mark {
      width: 36px; height: 36px; border-radius: 11px; display: grid; place-items: center;
      background: var(--ink); color: var(--paper); font: 700 12px/1 var(--font-display);
      letter-spacing: .08em;
    }
    .brand-copy strong { display: block; font: 650 14px/1.2 var(--font-display); }
    .brand-copy span { color: var(--muted); font-size: 12px; }
    .save-state {
      display: flex; align-items: center; gap: 7px; color: var(--muted); font-size: 12px;
    }
    .save-dot { width: 7px; height: 7px; border-radius: 999px; background: var(--teal); }
    .layout { display: grid; grid-template-columns: 244px minmax(0, 1fr); gap: 20px; align-items: start; }
    .rail {
      position: sticky; top: 20px; border: 1px solid var(--line); border-radius: var(--radius-lg);
      background: rgba(255, 255, 255, .85); backdrop-filter: blur(16px); padding: 18px;
    }
    .eyebrow {
      color: var(--accent); font-size: 11px; font-weight: 750; letter-spacing: .11em;
      text-transform: uppercase;
    }
    .rail h1 { margin: 7px 0 8px; font: 650 24px/1.12 var(--font-display); letter-spacing: -.025em; }
    .rail p { margin: 0; color: var(--muted); font-size: 13px; }
    .progress-label { display: flex; justify-content: space-between; margin-top: 20px; font-size: 12px; color: var(--muted); }
    .progress-track { height: 8px; margin-top: 8px; background: var(--line); border-radius: 99px; overflow: hidden; }
    .progress-fill { height: 100%; width: 0; background: var(--accent); border-radius: inherit; transition: width .25s ease; }
    .reviewer-field { display: grid; gap: 6px; margin-top: 20px; }
    .reviewer-field label { font-size: 12px; font-weight: 700; }
    .reviewer-field input {
      width: 100%; border: 1px solid var(--line-strong); border-radius: var(--radius-sm);
      padding: 9px 10px; color: var(--ink); background: var(--paper);
    }
    .rail-rule { height: 1px; background: var(--line); margin: 18px 0; }
    .anchor-list { display: grid; gap: 10px; }
    .anchor-list div { display: grid; grid-template-columns: 22px 1fr; gap: 8px; }
    .anchor-list b {
      width: 22px; height: 22px; border-radius: 7px; display: grid; place-items: center;
      background: var(--accent-soft); color: var(--accent); font-size: 11px;
    }
    .anchor-list span { color: var(--muted); font-size: 11px; line-height: 1.35; }
    .rail details { margin-top: 16px; font-size: 12px; }
    .rail summary { cursor: pointer; color: var(--ink); font-weight: 700; }
    .rail details p { margin-top: 8px; font-size: 11px; }
    .workspace { min-width: 0; }
    .question-card {
      border: 1px solid var(--line); border-radius: var(--radius-lg); background: var(--paper);
      box-shadow: var(--shadow); overflow: hidden;
    }
    .question-head {
      display: flex; justify-content: space-between; gap: 18px; align-items: flex-start;
      padding: 22px 24px 18px; border-bottom: 1px solid var(--line);
    }
    .question-meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 9px; }
    .chip {
      display: inline-flex; min-height: 24px; align-items: center; border-radius: 999px;
      padding: 3px 9px; background: var(--paper-soft); border: 1px solid var(--line);
      color: var(--muted); font-size: 11px; font-weight: 650;
    }
    .question-copy {
      font: 570 19px/1.55 var(--font-display); letter-spacing: -.012em; max-width: 980px;
    }
    .question-copy math { font-family: var(--font-math); font-size: 1.08em; }
    .question-copy math[display="block"] { margin: 10px 0; }
    .visual-prompt {
      margin: 10px 0; border-left: 3px solid var(--teal); background: var(--teal-soft);
      padding: 10px 12px; border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
      color: #185b59; font: 500 13px/1.45 var(--font-body);
    }
    .correct {
      min-width: 170px; border-radius: var(--radius-md); background: var(--paper-soft);
      border: 1px solid var(--line); padding: 11px 13px;
    }
    .correct span { display: block; color: var(--muted); font-size: 10px; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
    .correct strong { display: block; margin-top: 4px; font-family: var(--font-math); overflow-wrap: anywhere; }
    .candidate-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .candidate { min-width: 0; padding: 22px 24px 24px; }
    .candidate + .candidate { border-left: 1px solid var(--line); }
    .candidate-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
    .candidate-title h2 { margin: 0; font: 650 17px/1.2 var(--font-display); }
    .candidate-title span { color: var(--muted); font-size: 11px; }
    .distractor-list { display: grid; gap: 10px; }
    .distractor {
      border: 1px solid var(--line); border-radius: var(--radius-md); overflow: hidden; background: var(--paper-soft);
    }
    .distractor-top { display: grid; grid-template-columns: 28px 1fr; gap: 10px; padding: 12px; }
    .distractor-num {
      width: 28px; height: 28px; border-radius: 9px; display: grid; place-items: center;
      background: var(--ink); color: var(--paper); font-size: 11px; font-weight: 750;
    }
    .misconception { font-size: 13px; font-weight: 650; overflow-wrap: anywhere; }
    .distractor-data { display: grid; grid-template-columns: 1.2fr .8fr; border-top: 1px solid var(--line); }
    .data-cell { min-width: 0; padding: 9px 11px; }
    .data-cell + .data-cell { border-left: 1px solid var(--line); }
    .data-cell small { display: block; color: var(--muted); font-size: 9px; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
    .data-cell code { display: block; margin-top: 3px; color: var(--ink); font-family: var(--font-math); font-size: 12px; white-space: normal; overflow-wrap: anywhere; }
    .rating-panel { margin-top: 16px; border-top: 1px solid var(--line); padding-top: 16px; }
    fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
    .rating-panel fieldset + fieldset { margin-top: 14px; }
    legend { width: 100%; padding: 0; font-size: 12px; font-weight: 700; }
    .rating-help { display: flex; justify-content: space-between; gap: 10px; color: var(--faint); font-size: 9px; margin: 5px 0 6px; }
    .scale { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); border: 1px solid var(--line-strong); border-radius: 10px; overflow: hidden; }
    .scale label { position: relative; cursor: pointer; }
    .scale label + label { border-left: 1px solid var(--line); }
    .scale input { position: absolute; opacity: 0; pointer-events: none; }
    .scale span { min-height: 34px; display: grid; place-items: center; color: var(--muted); font-size: 12px; font-weight: 700; background: var(--paper); }
    .scale input:checked + span { color: var(--paper); background: var(--accent); }
    .issues { margin-top: 16px; padding: 12px; border-radius: var(--radius-md); background: var(--warning-soft); border: 1px solid #f1d8a9; }
    .issues legend { color: var(--warning); }
    .issue-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 10px; margin-top: 9px; }
    .check { display: flex; align-items: flex-start; gap: 7px; color: var(--ink); font-size: 11px; cursor: pointer; }
    .check input { margin: 2px 0 0; accent-color: var(--accent); }
    .check.none { grid-column: 1 / -1; border-top: 1px solid #efd29c; padding-top: 8px; color: var(--teal); font-weight: 700; }
    .assessment {
      margin-top: 18px; border: 1px solid var(--line); border-radius: var(--radius-lg);
      background: var(--paper); padding: 20px 22px; box-shadow: var(--shadow);
    }
    .assessment h2 { margin: 0 0 5px; font: 650 18px/1.2 var(--font-display); }
    .assessment > p { margin: 0 0 14px; color: var(--muted); font-size: 12px; }
    .preference { display: grid; grid-template-columns: 1fr .72fr 1fr; border: 1px solid var(--line-strong); border-radius: var(--radius-md); overflow: hidden; }
    .preference label { position: relative; cursor: pointer; }
    .preference label + label { border-left: 1px solid var(--line); }
    .preference input { position: absolute; opacity: 0; }
    .preference span { min-height: 48px; display: grid; place-items: center; background: var(--paper-soft); font-weight: 750; color: var(--muted); }
    .preference input:checked + span { color: var(--paper); background: var(--ink); }
    .note-field { display: grid; gap: 6px; margin-top: 15px; }
    .note-field label { font-size: 12px; font-weight: 700; }
    .note-field textarea {
      resize: vertical; min-height: 66px; width: 100%; border: 1px solid var(--line-strong);
      border-radius: var(--radius-md); padding: 10px 12px; color: var(--ink); background: var(--paper);
    }
    .validation {
      display: none; margin-top: 12px; border-radius: var(--radius-sm); padding: 9px 11px;
      color: var(--danger); background: var(--danger-soft); font-size: 12px; font-weight: 650;
    }
    .validation.show { display: block; }
    .actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 18px 0 32px; }
    .action-group { display: flex; gap: 9px; flex-wrap: wrap; }
    .button {
      border: 1px solid var(--line-strong); border-radius: 10px; min-height: 40px; padding: 8px 15px;
      background: var(--paper); color: var(--ink); font-weight: 750; cursor: pointer;
    }
    .button.primary { background: var(--accent); border-color: var(--accent); color: var(--paper); }
    .button.ghost { background: transparent; }
    .button:disabled { opacity: .42; cursor: not-allowed; }
    .completion {
      display: none; border: 1px solid var(--line); border-radius: var(--radius-lg);
      background: var(--paper); padding: 32px; box-shadow: var(--shadow);
    }
    .completion.show { display: block; }
    .completion h2 { margin: 0 0 8px; font: 650 25px/1.2 var(--font-display); }
    .completion p { color: var(--muted); max-width: 650px; }
    .completion .action-group { margin-top: 18px; }
    .hide { display: none !important; }
    @media (max-width: 1050px) {
      .layout { grid-template-columns: 1fr; }
      .rail { position: static; display: grid; grid-template-columns: 1.2fr 1fr; gap: 18px; }
      .rail-rule, .rail details { display: none; }
      .reviewer-field { margin-top: 10px; }
    }
    @media (max-width: 760px) {
      .shell { padding: 12px; }
      .rail { display: block; }
      .anchor-list { display: none; }
      .question-head { display: grid; padding: 18px; }
      .correct { min-width: 0; }
      .candidate-grid { grid-template-columns: 1fr; }
      .candidate { padding: 18px; }
      .candidate + .candidate { border-left: 0; border-top: 1px solid var(--line); }
      .issue-grid { grid-template-columns: 1fr; }
      .check.none { grid-column: auto; }
      .actions { align-items: stretch; flex-direction: column; }
      .action-group { width: 100%; }
      .button { flex: 1; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
    }
    @media print {
      body { background: var(--paper); }
      .rail, .actions, .topbar { display: none; }
      .layout { display: block; }
      .question-card, .assessment { box-shadow: none; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">DR</div>
        <div class="brand-copy">
          <strong>Distractor review</strong>
          <span>Anonymous paired evaluation</span>
        </div>
      </div>
      <div class="save-state"><span class="save-dot"></span><span id="save-copy">Saved locally</span></div>
    </header>
    <div class="layout">
      <aside class="rail">
        <div>
          <div class="eyebrow">Blinded review</div>
          <h1>Judge the teaching value.</h1>
          <p>Compare each pair on the same question. Identity and automatic scores are intentionally hidden.</p>
          <div class="progress-label"><span id="progress-copy">0 of 24 complete</span><span id="position-copy">Item 1 / 24</span></div>
          <div class="progress-track" aria-hidden="true"><div class="progress-fill" id="progress-fill"></div></div>
          <div class="reviewer-field">
            <label for="reviewer-code">Reviewer code</label>
            <input id="reviewer-code" autocomplete="off" maxlength="40" placeholder="Owner-assigned code, e.g. R1">
          </div>
        </div>
        <div>
          <div class="rail-rule"></div>
          <div class="eyebrow">1–5 anchors</div>
          <div class="anchor-list">
            <div><b>1</b><span>Not useful, implausible, or unclear.</span></div>
            <div><b>3</b><span>Mixed but usable; some diagnostic signal.</span></div>
            <div><b>5</b><span>Distinct, believable, and immediately teachable.</span></div>
          </div>
          <details>
            <summary>Review discipline</summary>
            <p>Use your own sixth-grade math and teaching judgment. Do not research candidate identity. Plausibility is human judgment, not observed student frequency.</p>
          </details>
        </div>
      </aside>
      <main class="workspace">
        <div id="review-view">
          <section class="question-card">
            <header class="question-head">
              <div>
                <div class="question-meta">
                  <span class="chip" id="item-code"></span>
                  <span class="chip" id="topic"></span>
                </div>
                <div class="question-copy" id="question"></div>
              </div>
              <div class="correct"><span>Correct answer</span><strong id="correct-answer"></strong></div>
            </header>
            <div class="candidate-grid" id="candidate-grid"></div>
          </section>
          <section class="assessment">
            <h2>Overall choice</h2>
            <p>Which complete set is better for diagnosing and acting on sixth-grade misconceptions?</p>
            <fieldset>
              <legend class="hide">Overall preferred candidate</legend>
              <div class="preference">
                <label><input type="radio" name="preference" value="A"><span>Candidate A</span></label>
                <label><input type="radio" name="preference" value="Tie"><span>Tie</span></label>
                <label><input type="radio" name="preference" value="B"><span>Candidate B</span></label>
              </div>
            </fieldset>
            <div class="note-field">
              <label for="note">Optional short note</label>
              <textarea id="note" maxlength="600" placeholder="Only add context that will help later adjudication."></textarea>
            </div>
            <div class="validation" id="validation" role="alert"></div>
          </section>
          <nav class="actions" aria-label="Review navigation">
            <div class="action-group">
              <button class="button ghost" id="previous" type="button">Previous</button>
              <button class="button primary" id="next" type="button">Save & next</button>
            </div>
            <div class="action-group">
              <button class="button" id="download-json" type="button" disabled>Download JSON</button>
              <button class="button" id="download-csv" type="button" disabled>Download CSV</button>
            </div>
          </nav>
        </div>
        <section class="completion" id="completion">
          <div class="eyebrow">Review complete</div>
          <h2>Export both formats before unblinding.</h2>
          <p>Your ratings remain anonymous with respect to candidate identity. Download JSON for analysis and CSV for a readable backup, then return the file(s) to the owner.</p>
          <div class="action-group">
            <button class="button primary" id="complete-json" type="button">Download JSON</button>
            <button class="button" id="complete-csv" type="button">Download CSV</button>
            <button class="button ghost" id="back-to-review" type="button">Review answers</button>
          </div>
        </section>
      </main>
    </div>
  </div>
  <script>
    "use strict";
    const REVIEW_ITEMS = __REVIEW_DATA__;
    const STORAGE_KEY = "blindedDistractorReview:r1";
    const RUBRIC_VERSION = "blinded-set-rubric-v1";
    const ISSUE_VALUES = ["mathematically_inconsistent", "correct_answer_collision", "duplicate", "nonsense"];
    const DIMENSIONS = [
      {
        key: "diagnostic_usefulness",
        label: "Diagnostic usefulness",
        low: "1 · no interpretable signal",
        high: "5 · pinpoints teachable errors"
      },
      {
        key: "student_plausibility",
        label: "Realistic student plausibility",
        low: "1 · arbitrary / giveaway",
        high: "5 · highly believable human judgment"
      },
      {
        key: "teacher_actionability",
        label: "Clarity / teacher actionability",
        low: "1 · unclear / unusable",
        high: "5 · immediate next teaching step"
      }
    ];
    const ISSUE_LABELS = {
      mathematically_inconsistent: "Mathematically inconsistent",
      correct_answer_collision: "Correct-answer collision",
      duplicate: "Duplicate",
      nonsense: "Nonsense"
    };
    let state = loadState();
    let currentIndex = Math.min(
      REVIEW_ITEMS.length - 1,
      Math.max(0, Number.isInteger(state.current_index) ? state.current_index : 0)
    );

    function emptyCandidate() {
      return {
        diagnostic_usefulness: null,
        student_plausibility: null,
        teacher_actionability: null,
        issues: [],
        issues_reviewed: false
      };
    }

    function emptyRating() {
      return {
        preference: "",
        candidate_a: emptyCandidate(),
        candidate_b: emptyCandidate(),
        note: ""
      };
    }

    function loadState() {
      try {
        const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
        return {
          reviewer_code: typeof stored.reviewer_code === "string" ? stored.reviewer_code : "",
          current_index: Number.isInteger(stored.current_index) ? stored.current_index : 0,
          ratings: stored.ratings && typeof stored.ratings === "object" ? stored.ratings : {}
        };
      } catch (_error) {
        return { reviewer_code: "", current_index: 0, ratings: {} };
      }
    }

    function saveState() {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      const copy = document.getElementById("save-copy");
      copy.textContent = "Saved locally";
    }

    function getRating(itemId) {
      if (!state.ratings[itemId]) state.ratings[itemId] = emptyRating();
      return state.ratings[itemId];
    }

    function escapeText(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      })[char]);
    }

    function candidateMarkup(label, distractors) {
      const lower = label.toLowerCase();
      const cards = distractors.length
        ? distractors.map((item, index) => `
          <article class="distractor">
            <div class="distractor-top">
              <div class="distractor-num">${index + 1}</div>
              <div class="misconception">${escapeText(item.misconception || "(No misconception supplied)")}</div>
            </div>
            <div class="distractor-data">
              <div class="data-cell"><small>Computation</small><code>${escapeText(item.computation || "Not supplied")}</code></div>
              <div class="data-cell"><small>Answer</small><code>${escapeText(item.answer || "Not supplied")}</code></div>
            </div>
          </article>`).join("")
        : '<article class="distractor"><div class="distractor-top"><div class="misconception">No distractors supplied.</div></div></article>';
      const ratingFields = DIMENSIONS.map(dimension => `
        <fieldset>
          <legend>${dimension.label}</legend>
          <div class="rating-help"><span>${dimension.low}</span><span>${dimension.high}</span></div>
          <div class="scale">
            ${[1, 2, 3, 4, 5].map(value => `
              <label>
                <input type="radio" name="${lower}_${dimension.key}" value="${value}" data-candidate="${lower}" data-dimension="${dimension.key}">
                <span>${value}</span>
              </label>`).join("")}
          </div>
        </fieldset>`).join("");
      const issueFields = ISSUE_VALUES.map(issue => `
        <label class="check">
          <input type="checkbox" value="${issue}" data-issue="${lower}">
          <span>${ISSUE_LABELS[issue]}</span>
        </label>`).join("");
      return `
        <section class="candidate" aria-labelledby="candidate-${lower}-title">
          <div class="candidate-title">
            <h2 id="candidate-${lower}-title">Candidate ${label}</h2>
            <span>${distractors.length} distractor${distractors.length === 1 ? "" : "s"}</span>
          </div>
          <div class="distractor-list">${cards}</div>
          <div class="rating-panel">${ratingFields}</div>
          <fieldset class="issues">
            <legend>Issue check · explicitly mark none or all that apply</legend>
            <div class="issue-grid">
              ${issueFields}
              <label class="check none">
                <input type="checkbox" value="none" data-issue-none="${lower}">
                <span>No listed issue noticed</span>
              </label>
            </div>
          </fieldset>
        </section>`;
    }

    function render() {
      const item = REVIEW_ITEMS[currentIndex];
      const rating = getRating(item.review_item_id);
      document.getElementById("item-code").textContent = item.review_item_id;
      document.getElementById("topic").textContent = item.topic;
      document.getElementById("question").innerHTML = item.question_html;
      document.getElementById("correct-answer").textContent = item.correct_answer;
      document.getElementById("candidate-grid").innerHTML =
        candidateMarkup("A", item.candidate_a) + candidateMarkup("B", item.candidate_b);
      document.getElementById("reviewer-code").value = state.reviewer_code;
      document.getElementById("note").value = rating.note || "";
      document.querySelectorAll('input[name="preference"]').forEach(input => {
        input.checked = input.value === rating.preference;
      });
      for (const candidate of ["a", "b"]) {
        const candidateRating = rating[`candidate_${candidate}`] || emptyCandidate();
        for (const dimension of DIMENSIONS) {
          document.querySelectorAll(`input[name="${candidate}_${dimension.key}"]`).forEach(input => {
            input.checked = Number(input.value) === candidateRating[dimension.key];
          });
        }
        document.querySelectorAll(`input[data-issue="${candidate}"]`).forEach(input => {
          input.checked = candidateRating.issues.includes(input.value);
        });
        const noneInput = document.querySelector(`input[data-issue-none="${candidate}"]`);
        noneInput.checked = candidateRating.issues_reviewed && candidateRating.issues.length === 0;
      }
      document.getElementById("previous").disabled = currentIndex === 0;
      document.getElementById("next").textContent =
        currentIndex === REVIEW_ITEMS.length - 1 ? "Finish review" : "Save & next";
      document.getElementById("position-copy").textContent =
        `Item ${currentIndex + 1} / ${REVIEW_ITEMS.length}`;
      document.getElementById("validation").classList.remove("show");
      bindItemEvents();
      updateProgress();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function bindItemEvents() {
      const item = REVIEW_ITEMS[currentIndex];
      const rating = getRating(item.review_item_id);
      document.querySelectorAll('input[name="preference"]').forEach(input => {
        input.addEventListener("change", () => {
          rating.preference = input.value;
          saveState();
          updateProgress();
        });
      });
      document.querySelectorAll("input[data-dimension]").forEach(input => {
        input.addEventListener("change", () => {
          rating[`candidate_${input.dataset.candidate}`][input.dataset.dimension] = Number(input.value);
          saveState();
          updateProgress();
        });
      });
      document.querySelectorAll("input[data-issue]").forEach(input => {
        input.addEventListener("change", () => {
          const candidate = input.dataset.issue;
          const candidateRating = rating[`candidate_${candidate}`];
          candidateRating.issues = Array.from(
            document.querySelectorAll(`input[data-issue="${candidate}"]:checked`)
          ).map(control => control.value);
          candidateRating.issues_reviewed = true;
          document.querySelector(`input[data-issue-none="${candidate}"]`).checked = false;
          saveState();
          updateProgress();
        });
      });
      document.querySelectorAll("input[data-issue-none]").forEach(input => {
        input.addEventListener("change", () => {
          const candidate = input.dataset.issueNone;
          const candidateRating = rating[`candidate_${candidate}`];
          candidateRating.issues_reviewed = input.checked;
          if (input.checked) {
            candidateRating.issues = [];
            document.querySelectorAll(`input[data-issue="${candidate}"]`).forEach(control => {
              control.checked = false;
            });
          }
          saveState();
          updateProgress();
        });
      });
    }

    function syncFreeText() {
      const item = REVIEW_ITEMS[currentIndex];
      getRating(item.review_item_id).note = document.getElementById("note").value.trim();
      state.reviewer_code = document.getElementById("reviewer-code").value.trim();
      saveState();
    }

    function missingFields(rating) {
      const missing = [];
      if (!state.reviewer_code) missing.push("reviewer code");
      if (!["A", "Tie", "B"].includes(rating.preference)) missing.push("overall preference");
      for (const candidate of ["a", "b"]) {
        const values = rating[`candidate_${candidate}`];
        for (const dimension of DIMENSIONS) {
          if (![1, 2, 3, 4, 5].includes(values[dimension.key])) {
            missing.push(`Candidate ${candidate.toUpperCase()} ${dimension.label.toLowerCase()}`);
          }
        }
        if (!values.issues_reviewed) missing.push(`Candidate ${candidate.toUpperCase()} issue check`);
      }
      return missing;
    }

    function isComplete(rating) {
      return missingFields(rating).length === 0;
    }

    function validateCurrent() {
      syncFreeText();
      const item = REVIEW_ITEMS[currentIndex];
      const missing = missingFields(getRating(item.review_item_id));
      const message = document.getElementById("validation");
      if (!missing.length) {
        message.classList.remove("show");
        return true;
      }
      message.textContent = `Complete before moving on: ${missing.join("; ")}.`;
      message.classList.add("show");
      message.scrollIntoView({ behavior: "smooth", block: "center" });
      return false;
    }

    function completedCount() {
      return REVIEW_ITEMS.filter(item => isComplete(getRating(item.review_item_id))).length;
    }

    function updateProgress() {
      const complete = completedCount();
      const percent = (100 * complete) / REVIEW_ITEMS.length;
      document.getElementById("progress-copy").textContent =
        `${complete} of ${REVIEW_ITEMS.length} complete`;
      document.getElementById("progress-fill").style.width = `${percent}%`;
      const ready = complete === REVIEW_ITEMS.length && Boolean(state.reviewer_code);
      document.getElementById("download-json").disabled = !ready;
      document.getElementById("download-csv").disabled = !ready;
    }

    function exportPayload() {
      syncFreeText();
      return {
        schema_version: "blinded-ratings-v1",
        rubric_version: RUBRIC_VERSION,
        reviewer_code: state.reviewer_code,
        sample_size: REVIEW_ITEMS.length,
        completed_at: new Date().toISOString(),
        ratings: REVIEW_ITEMS.map(item => {
          const rating = getRating(item.review_item_id);
          return {
            review_item_id: item.review_item_id,
            preference: rating.preference,
            candidate_a: {
              diagnostic_usefulness: rating.candidate_a.diagnostic_usefulness,
              student_plausibility: rating.candidate_a.student_plausibility,
              teacher_actionability: rating.candidate_a.teacher_actionability,
              issues: [...rating.candidate_a.issues]
            },
            candidate_b: {
              diagnostic_usefulness: rating.candidate_b.diagnostic_usefulness,
              student_plausibility: rating.candidate_b.student_plausibility,
              teacher_actionability: rating.candidate_b.teacher_actionability,
              issues: [...rating.candidate_b.issues]
            },
            note: rating.note || ""
          };
        })
      };
    }

    function download(name, type, content) {
      const blob = new Blob([content], { type });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    }

    function safeCode() {
      return state.reviewer_code.replace(/[^a-z0-9_-]+/gi, "-").replace(/^-|-$/g, "") || "reviewer";
    }

    function downloadJson() {
      if (completedCount() !== REVIEW_ITEMS.length || !state.reviewer_code) return;
      download(
        `blinded-ratings-${safeCode()}.json`,
        "application/json",
        JSON.stringify(exportPayload(), null, 2) + "\n"
      );
    }

    function csvCell(value) {
      const text = String(value ?? "");
      return `"${text.replace(/"/g, '""')}"`;
    }

    function downloadCsv() {
      if (completedCount() !== REVIEW_ITEMS.length || !state.reviewer_code) return;
      const payload = exportPayload();
      const headers = [
        "schema_version", "reviewer_code", "review_item_id", "preference",
        "a_diagnostic_usefulness", "a_student_plausibility", "a_teacher_actionability", "a_issues",
        "b_diagnostic_usefulness", "b_student_plausibility", "b_teacher_actionability", "b_issues", "note"
      ];
      const rows = payload.ratings.map(rating => [
        payload.schema_version, payload.reviewer_code, rating.review_item_id, rating.preference,
        rating.candidate_a.diagnostic_usefulness, rating.candidate_a.student_plausibility,
        rating.candidate_a.teacher_actionability, rating.candidate_a.issues.join("|"),
        rating.candidate_b.diagnostic_usefulness, rating.candidate_b.student_plausibility,
        rating.candidate_b.teacher_actionability, rating.candidate_b.issues.join("|"), rating.note
      ]);
      const csv = [headers, ...rows].map(row => row.map(csvCell).join(",")).join("\n") + "\n";
      download(`blinded-ratings-${safeCode()}.csv`, "text/csv", csv);
    }

    document.getElementById("reviewer-code").addEventListener("input", event => {
      state.reviewer_code = event.target.value.trim();
      saveState();
      updateProgress();
    });
    document.getElementById("note").addEventListener("input", () => {
      syncFreeText();
    });
    document.getElementById("previous").addEventListener("click", () => {
      syncFreeText();
      if (currentIndex > 0) {
        currentIndex -= 1;
        state.current_index = currentIndex;
        saveState();
        render();
      }
    });
    document.getElementById("next").addEventListener("click", () => {
      if (!validateCurrent()) return;
      if (currentIndex < REVIEW_ITEMS.length - 1) {
        currentIndex += 1;
        state.current_index = currentIndex;
        saveState();
        render();
      } else {
        document.getElementById("review-view").classList.add("hide");
        document.getElementById("completion").classList.add("show");
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    });
    document.getElementById("back-to-review").addEventListener("click", () => {
      document.getElementById("completion").classList.remove("show");
      document.getElementById("review-view").classList.remove("hide");
      render();
    });
    for (const id of ["download-json", "complete-json"]) {
      document.getElementById(id).addEventListener("click", downloadJson);
    }
    for (const id of ["download-csv", "complete-csv"]) {
      document.getElementById(id).addEventListener("click", downloadCsv);
    }
    window.addEventListener("beforeunload", syncFreeText);
    render();
  </script>
</body>
</html>
"""


def render_review_html(public_items: Sequence[dict]) -> str:
    if not public_items:
        raise ValueError("review HTML requires at least one item")
    payload = json.dumps(
        list(public_items),
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    return _REVIEW_HTML_TEMPLATE.replace("__REVIEW_DATA__", payload)


def validate_public_html(
    rendered: str,
    *,
    forbidden_labels: Sequence[str] = (),
) -> None:
    folded = rendered.casefold()
    violations = [
        label
        for label in forbidden_labels
        if label and str(label).casefold() in folded
    ]
    if violations:
        raise ValueError(f"public review HTML leaks forbidden labels: {violations}")
    network_markers = (
        "<script src=",
        "<link ",
        "fetch(",
        "xmlhttprequest",
        "websocket",
        'src="http://',
        'src="https://',
        "src='http://",
        "src='https://",
        'href="http://',
        'href="https://',
        "href='http://",
        "href='https://",
    )
    found = [marker for marker in network_markers if marker in folded]
    if found:
        raise ValueError(f"public review HTML contains network dependency: {found}")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _forbidden_public_labels(
    predictions: Mapping[str, Sequence[dict]],
) -> list[str]:
    labels = set(predictions)
    for rows in predictions.values():
        for row in rows:
            for key in ("generator_model", "inference_track"):
                value = str(row.get(key, "")).strip()
                if value:
                    labels.add(value)
    labels.update(
        {"generator_model", "inference_track", "v8_best_of_n", "model_only"}
    )
    return sorted(labels)


def _embedded_review_items(rendered: str) -> list[dict]:
    match = re.search(
        r"const REVIEW_ITEMS = (.*?);\s*const STORAGE_KEY",
        rendered,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError("review HTML has no embedded item payload")
    value = json.loads(match.group(1))
    if not isinstance(value, list):
        raise ValueError("embedded review payload must be a list")
    return value


def verify_review_package(
    *,
    gold_path: str | Path,
    prediction_paths: Mapping[str, str | Path],
    html_path: str | Path,
    key_path: str | Path,
) -> dict:
    """Verify the saved public artifact, hidden key, and every A/B mapping."""
    gold = _load_jsonl(gold_path)
    predictions = {
        label: _load_jsonl(path) for label, path in prediction_paths.items()
    }
    rendered = Path(html_path).read_text(encoding="utf-8")
    hidden_key = json.loads(Path(key_path).read_text(encoding="utf-8"))
    if not isinstance(hidden_key, dict):
        raise ValueError("hidden key must be a JSON object")
    sample_design = hidden_key.get("sample_design", {})
    expected_public, expected_key = build_review_bundle(
        gold,
        predictions,
        sample_size=int(sample_design.get("sample_size", 0)),
        sample_seed=int(sample_design.get("sample_seed", 0)),
        order_seed=int(sample_design.get("candidate_order_seed", 0)),
    )
    if hidden_key.get("items") != expected_key["items"]:
        raise ValueError("hidden key item mappings do not match source files")
    if hidden_key.get("source_labels") != expected_key["source_labels"]:
        raise ValueError("hidden key source labels do not match source files")
    if sample_design != expected_key["sample_design"]:
        raise ValueError("hidden key sample design does not reproduce")
    public_artifact = hidden_key.get("public_artifact", {})
    if public_artifact.get("sha256") != _sha256_text(rendered):
        raise ValueError("review HTML hash does not match hidden key")
    if public_artifact.get("filename") != Path(html_path).name:
        raise ValueError("review HTML filename does not match hidden key")
    embedded = _embedded_review_items(rendered)
    if embedded != expected_public:
        raise ValueError("embedded A/B pairs do not match hidden source mapping")
    validate_public_html(
        rendered,
        forbidden_labels=_forbidden_public_labels(predictions),
    )
    return {
        "sample_size": len(embedded),
        "all_sampled_ids_exist": True,
        "all_pairs_match": True,
        "candidate_order_reproduces": True,
        "public_artifact_is_blind": True,
        "public_artifact_is_offline": True,
        "public_sha256": _sha256_text(rendered),
        "family_distribution": sample_design.get("family_distribution", {}),
        "challenge_distribution": sample_design.get(
            "challenge_distribution",
            {},
        ),
        "answer_shape_distribution": sample_design.get(
            "answer_shape_distribution",
            {},
        ),
    }


def write_review_package(
    *,
    gold_path: str | Path,
    prediction_paths: Mapping[str, str | Path],
    html_path: str | Path,
    key_path: str | Path,
    sample_size: int = SAMPLE_SIZE,
    sample_seed: int = SAMPLE_SEED,
    order_seed: int = ORDER_SEED,
) -> dict:
    gold = _load_jsonl(gold_path)
    predictions = {
        label: _load_jsonl(path) for label, path in prediction_paths.items()
    }
    public_items, hidden_key = build_review_bundle(
        gold,
        predictions,
        sample_size=sample_size,
        sample_seed=sample_seed,
        order_seed=order_seed,
    )
    rendered = render_review_html(public_items)
    validate_public_html(
        rendered,
        forbidden_labels=_forbidden_public_labels(predictions),
    )

    output = Path(html_path)
    secret = Path(key_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    secret.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    hidden_key["public_artifact"] = {
        "filename": output.name,
        "sha256": _sha256_text(rendered),
        "rubric_version": RUBRIC_VERSION,
    }
    secret.write_text(
        json.dumps(hidden_key, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "html_path": str(output),
        "key_path": str(secret),
        "sample_size": len(public_items),
        "sample_design": hidden_key["sample_design"],
        "public_sha256": hidden_key["public_artifact"]["sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold",
        default="data/processed/eval_v8_frozen.jsonl",
    )
    parser.add_argument(
        "--system-one",
        default="predictions_v8_best_of_n.jsonl",
    )
    parser.add_argument(
        "--system-two",
        default="predictions_opus_v8.jsonl",
    )
    parser.add_argument(
        "--html-out",
        default="human_review/final_round/review.html",
    )
    parser.add_argument(
        "--key-out",
        default=(
            "data/eval_out/"
            "OWNER_ONLY_DO_NOT_OPEN_UNTIL_REVIEW_COMPLETE.json"
        ),
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify saved HTML/key mappings without rewriting them",
    )
    args = parser.parse_args()
    prediction_paths = {
        "v8_best_of_n": args.system_one,
        "opus": args.system_two,
    }
    if args.verify_only:
        result = verify_review_package(
            gold_path=args.gold,
            prediction_paths=prediction_paths,
            html_path=args.html_out,
            key_path=args.key_out,
        )
    else:
        result = write_review_package(
            gold_path=args.gold,
            prediction_paths=prediction_paths,
            html_path=args.html_out,
            key_path=args.key_out,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
