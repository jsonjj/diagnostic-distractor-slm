"""Deterministic exact question compiler for Wayline launch content."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Mapping

from .curriculum import Curriculum, FamilyDefinition, HoldoutReceipt, TemplateDefinition
from .procedure_registry import ProcedureRegistry
from .safe_numeric import format_fraction


MAX_SAMPLE_ATTEMPTS = 64


class CompilationError(ValueError):
    """Raised when a request is invalid or no safe blueprint can be compiled."""


@dataclass(frozen=True, slots=True)
class CompileRequest:
    world_id: str
    skill_id: str
    family_id: str
    difficulty: int
    seed: int


@dataclass(frozen=True, slots=True)
class CanonicalAnswer:
    value: Fraction
    display: str


@dataclass(frozen=True, slots=True)
class QuestionBlueprint:
    schema_version: str
    question_id: str
    world_id: str
    skill_id: str
    family_id: str
    topic: str
    template_id: str
    template_revision: int
    operand_names: tuple[str, ...]
    operands: tuple[str, ...]
    solver_spec: str
    prompt: str
    canonical_answer: CanonicalAnswer
    trusted_steps: tuple[str, ...]
    allowed_procedure_ids: tuple[str, ...]
    difficulty: int
    seed: int
    content_sha256: str
    holdout_receipt: HoldoutReceipt

    @property
    def operand_map(self) -> dict[str, int]:
        return {name: int(value) for name, value in zip(self.operand_names, self.operands, strict=True)}

    @property
    def context_seed(self) -> int:
        return self.seed

    @property
    def difficulty_vector(self) -> tuple[int]:
        return (self.difficulty,)


class QuestionCompiler:
    def __init__(self, curriculum: Curriculum, registry: ProcedureRegistry):
        self.curriculum = curriculum
        self.registry = registry

    @classmethod
    def for_tests(cls) -> "QuestionCompiler":
        curriculum = Curriculum.packaged_v1()
        holdout_source = Path(__file__).resolve().parents[3] / "data/processed/eval_heldout.jsonl"
        curriculum.holdout.validate_source(holdout_source)
        return cls(curriculum, ProcedureRegistry.for_tests())

    def compile(self, request: CompileRequest) -> QuestionBlueprint:
        family = self._validate_request(request)
        rng = random.Random(request.seed)
        rejected: dict[str, int] = {}
        for _attempt in range(1, MAX_SAMPLE_ATTEMPTS + 1):
            template = rng.choice(family.templates)
            operands = self._sample(family, request.difficulty, rng)
            correct = self._solve(family.family_id, operands)
            allowed = self.registry.distinct_applicable_procedures(
                family.family_id,
                operands,
                correct,
                template.procedure_ids,
            )
            if len(allowed) < 3:
                rejected["fewer_than_three_unambiguous_procedures"] = (
                    rejected.get("fewer_than_three_unambiguous_procedures", 0) + 1
                )
                continue
            try:
                prompt = template.prompt_template.format(**operands)
            except (KeyError, ValueError) as exc:
                raise CompilationError(f"invalid authored template: {template.template_id}") from exc
            holdout_receipt = self.curriculum.holdout.receipt_for(prompt)
            if holdout_receipt.excluded:
                rejected["frozen_holdout_similarity"] = rejected.get("frozen_holdout_similarity", 0) + 1
                continue
            canonical_answer = CanonicalAnswer(
                value=correct,
                display=self._answer_display(family.family_id, correct),
            )
            trusted_steps = self._trusted_steps(family.family_id, operands, correct)
            operand_values = tuple(str(operands[name]) for name in family.operand_names)
            digest = self._content_digest(
                request,
                family,
                template,
                operand_values,
                prompt,
                canonical_answer,
                trusted_steps,
                allowed,
                holdout_receipt,
            )
            return QuestionBlueprint(
                schema_version="wayline-question-blueprint-v1",
                question_id=f"{request.family_id.replace('_', '-')}-{request.seed}-{digest[:12]}",
                world_id=request.world_id,
                skill_id=request.skill_id,
                family_id=request.family_id,
                topic=family.topic,
                template_id=template.template_id,
                template_revision=template.revision,
                operand_names=family.operand_names,
                operands=operand_values,
                solver_spec=family.solver,
                prompt=prompt,
                canonical_answer=canonical_answer,
                trusted_steps=trusted_steps,
                allowed_procedure_ids=allowed,
                difficulty=request.difficulty,
                seed=request.seed,
                content_sha256=digest,
                holdout_receipt=holdout_receipt,
            )
        diagnostics = ", ".join(f"{key}={value}" for key, value in sorted(rejected.items()))
        raise CompilationError(
            f"could not compile a safe blueprint in {MAX_SAMPLE_ATTEMPTS} attempts ({diagnostics})"
        )

    def _validate_request(self, request: CompileRequest) -> FamilyDefinition:
        if not isinstance(request, CompileRequest):
            raise CompilationError("compile request has the wrong type")
        if not isinstance(request.difficulty, int) or isinstance(request.difficulty, bool):
            raise CompilationError("difficulty must be an integer")
        if request.difficulty not in (1, 2, 3):
            raise CompilationError("difficulty must be 1, 2, or 3")
        if (
            not isinstance(request.seed, int)
            or isinstance(request.seed, bool)
            or not 0 <= request.seed < 2**63
        ):
            raise CompilationError("seed must be a nonnegative signed 64-bit integer")
        try:
            family = self.curriculum.families[request.family_id]
        except KeyError as exc:
            raise CompilationError(f"unknown family: {request.family_id}") from exc
        if family.world_id != request.world_id or family.skill_id != request.skill_id:
            raise CompilationError("world, skill, and family do not match the curriculum")
        return family

    def _sample(
        self,
        family: FamilyDefinition,
        difficulty: int,
        rng: random.Random,
    ) -> dict[str, int]:
        family_id = family.family_id
        if family_id == "place_value":
            thousands = rng.randint(1, (4, 7, 9)[difficulty - 1])
            digit = rng.randint(1, 9)
            number = thousands * 1000 + digit * 100 + rng.randint(0, 9) * 10 + rng.randint(0, 9)
            return {"N": number, "d": digit}
        if family_id == "mental_add":
            upper = (49, 69, 89)[difficulty - 1]
            candidates = tuple(
                (a, b)
                for a in range(25, upper + 1)
                for b in range(15, a)
                if a % 10 + b % 10 >= 10
            )
            a, b = rng.choice(candidates)
            return {"a": a, "b": b}
        if family_id == "decimal_add":
            cap = (5, 7, 9)[difficulty - 1]
            return {"a": rng.randint(1, cap), "b": rng.randint(1, cap)}
        if family_id == "fraction_add":
            numerator_cap = (4, 6, 7)[difficulty - 1]
            denominator_cap = (6, 8, 9)[difficulty - 1]
            return {
                "a": rng.randint(1, numerator_cap),
                "b": rng.randint(2, denominator_cap),
                "c": rng.randint(1, numerator_cap),
                "d": rng.randint(2, denominator_cap),
            }
        if family_id == "fraction_multiply":
            numerator_cap = (4, 6, 7)[difficulty - 1]
            denominator_cap = (6, 8, 9)[difficulty - 1]
            b = rng.randint(2, denominator_cap)
            d = rng.randint(2, denominator_cap)
            return {
                "a": rng.randint(1, min(numerator_cap, b - 1)),
                "b": b,
                "c": rng.randint(1, min(numerator_cap, d - 1)),
                "d": d,
            }
        if family_id == "decimal_multiply":
            cap = (5, 7, 9)[difficulty - 1]
            return {"p": rng.randint(1, cap), "q": rng.randint(1, cap)}
        if family_id == "round_one_decimal":
            whole_cap = (4, 7, 9)[difficulty - 1]
            return {
                "whole": rng.randint(1, whole_cap),
                "d1": rng.randint(1, 8),
                "d2": rng.randint(1, 9),
            }
        if family_id == "fraction_divide_integer":
            n_cap = (3, 5, 6)[difficulty - 1]
            n = rng.randint(2, n_cap)
            multiplier = rng.randint(2, (3, 4, 5)[difficulty - 1])
            denominator = n * multiplier
            return {"a": rng.randint(1, denominator - 1), "b": denominator, "n": n}
        if family_id == "percent_of_amount":
            percentages = (5, 10, 15, 20, 25, 30, 40, 50, 75)
            amounts = (16, 20, 24, 30, 32, 36, 40, 48, 50, 60, 64, 80, 90, 100, 120, 150, 200, 240)
            p_limit = (5, 7, 9)[difficulty - 1]
            a_limit = (8, 13, 18)[difficulty - 1]
            return {
                "percent": rng.choice(percentages[:p_limit]),
                "amount": rng.choice(amounts[:a_limit]),
            }
        if family_id == "decimal_to_percent":
            cap = (5, 7, 9)[difficulty - 1]
            return {"a": rng.randint(1, cap), "b": rng.randint(1, 9)}
        if family_id == "negative_add":
            cap = (10, 15, 20)[difficulty - 1]
            a, b = rng.choice(
                tuple((left, right) for left in range(1, cap + 1) for right in range(1, cap + 1) if left != right)
            )
            return {"a": a, "b": b}
        if family_id == "mental_multiply":
            return {
                "a": rng.randint(11, (14, 17, 19)[difficulty - 1]),
                "b": rng.randint(3, (6, 8, 9)[difficulty - 1]),
            }
        if family_id == "hcf":
            common = rng.choice((2, 3, 4, 5, 6, 7, 8, 9)[: (4, 6, 8)[difficulty - 1]])
            pairs = (
                (2, 5), (3, 5), (2, 7), (3, 7), (4, 7), (2, 9),
                (4, 9), (5, 7), (5, 8), (3, 8), (3, 4), (2, 3),
                (4, 5), (5, 6), (6, 7), (7, 8), (7, 9),
            )
            x, y = rng.choice(pairs)
            return {"a": common * x, "b": common * y}
        if family_id == "bidmas_add_multiply":
            cap = (5, 7, 9)[difficulty - 1]
            return {"a": rng.randint(2, cap), "b": rng.randint(2, cap), "c": rng.randint(2, cap)}
        if family_id == "indices_same_base_multiply":
            exponent_cap = (3, 4, 5)[difficulty - 1]
            m, n = rng.choice(
                tuple(
                    (left, right)
                    for left in range(2, exponent_cap + 1)
                    for right in range(2, exponent_cap + 1)
                    if left != right
                )
            )
            return {
                "base": rng.randint(2, (4, 7, 9)[difficulty - 1]),
                "m": m,
                "n": n,
            }
        raise CompilationError(f"unsupported sampler: {family_id}")

    def _solve(self, family_id: str, o: Mapping[str, int]) -> Fraction:
        if family_id == "place_value":
            return Fraction(o["d"] * 100)
        if family_id == "mental_add":
            return Fraction(o["a"] + o["b"])
        if family_id == "decimal_add":
            return Fraction(o["a"] * 10 + o["b"], 100)
        if family_id == "fraction_add":
            return Fraction(o["a"], o["b"]) + Fraction(o["c"], o["d"])
        if family_id == "decimal_multiply":
            return Fraction(o["p"] * o["q"], 100)
        if family_id == "round_one_decimal":
            return Fraction(o["whole"] * 10 + o["d1"] + (1 if o["d2"] >= 5 else 0), 10)
        if family_id == "fraction_multiply":
            return Fraction(o["a"] * o["c"], o["b"] * o["d"])
        if family_id == "fraction_divide_integer":
            return Fraction(o["a"], o["b"] * o["n"])
        if family_id == "percent_of_amount":
            return Fraction(o["amount"] * o["percent"], 100)
        if family_id == "decimal_to_percent":
            return Fraction(o["a"] * 10 + o["b"])
        if family_id == "negative_add":
            return Fraction(o["b"] - o["a"])
        if family_id == "mental_multiply":
            return Fraction(o["a"] * o["b"])
        if family_id == "hcf":
            return Fraction(math.gcd(o["a"], o["b"]))
        if family_id == "bidmas_add_multiply":
            return Fraction(o["a"] + o["b"] * o["c"])
        if family_id == "indices_same_base_multiply":
            return Fraction(o["base"] ** (o["m"] + o["n"]))
        raise CompilationError(f"unsupported solver: {family_id}")

    def _answer_display(self, family_id: str, answer: Fraction) -> str:
        suffix = "%" if family_id == "decimal_to_percent" else ""
        return format_fraction(answer) + suffix

    def _trusted_steps(
        self, family_id: str, o: Mapping[str, int], answer: Fraction
    ) -> tuple[str, ...]:
        answer_text = self._answer_display(family_id, answer)
        if family_id == "place_value":
            return ("Locate the digit in the hundreds place.", f"{o['d']} × 100 = {answer_text}.")
        if family_id == "mental_add":
            return (f"Add {o['b']} to {o['a']} by place value.", f"The total is {answer_text}.")
        if family_id == "decimal_add":
            return ("Align the decimal points and write both hundredths places.", f"The sum is {answer_text}.")
        if family_id == "fraction_add":
            return ("Rewrite both fractions with a common denominator.", f"Add the new numerators and simplify to {answer_text}.")
        if family_id == "decimal_multiply":
            return (f"Multiply {o['p']} by {o['q']}.", f"The two factors have two decimal places altogether, giving {answer_text}.")
        if family_id == "round_one_decimal":
            return (f"Inspect the hundredths digit, {o['d2']}.", f"Round the tenths digit to obtain {answer_text}.")
        if family_id == "fraction_multiply":
            return ("Multiply the numerators and multiply the denominators.", f"Simplify the product to {answer_text}.")
        if family_id == "fraction_divide_integer":
            return (f"Treat {o['n']} as {o['n']}/1 and multiply by its reciprocal.", f"Simplify to {answer_text}.")
        if family_id == "percent_of_amount":
            return (f"Write {o['percent']}% as {o['percent']}/100.", f"Multiply by {o['amount']} to obtain {answer_text}.")
        if family_id == "decimal_to_percent":
            return ("Multiply the decimal by 100.", f"Attach the percent sign: {answer_text}.")
        if family_id == "negative_add":
            return (f"Start at -{o['a']} and move {o['b']} units right.", f"The result is {answer_text}.")
        if family_id == "mental_multiply":
            return (f"Split {o['a']} into 10 and {o['a'] - 10}.", f"Multiply both parts by {o['b']} and combine to get {answer_text}.")
        if family_id == "hcf":
            return ("List or factor both numbers.", f"The greatest factor shared by both is {answer_text}.")
        if family_id == "bidmas_add_multiply":
            return (f"Multiply {o['b']} × {o['c']} before adding.", f"Then add {o['a']} to obtain {answer_text}.")
        if family_id == "indices_same_base_multiply":
            return (f"Keep base {o['base']} and add exponents {o['m']} + {o['n']}.", f"Evaluate the resulting power to get {answer_text}.")
        raise CompilationError(f"unsupported trusted steps: {family_id}")

    def _content_digest(
        self,
        request: CompileRequest,
        family: FamilyDefinition,
        template: TemplateDefinition,
        operands: tuple[str, ...],
        prompt: str,
        answer: CanonicalAnswer,
        steps: tuple[str, ...],
        allowed: tuple[str, ...],
        receipt: HoldoutReceipt,
    ) -> str:
        payload = {
            "schema_version": "wayline-question-blueprint-v1",
            "request": asdict(request),
            "topic": family.topic,
            "template_id": template.template_id,
            "template_revision": template.revision,
            "operand_names": family.operand_names,
            "operands": operands,
            "solver_spec": family.solver,
            "prompt": prompt,
            "canonical_answer": {
                "numerator": answer.value.numerator,
                "denominator": answer.value.denominator,
                "display": answer.display,
            },
            "trusted_steps": steps,
            "allowed_procedure_ids": allowed,
            "holdout_receipt": asdict(receipt),
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
