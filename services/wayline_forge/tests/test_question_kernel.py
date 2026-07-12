from fractions import Fraction
import inspect
import json
import math
from pathlib import Path
import tempfile
import unittest

from services.wayline_forge.app.curriculum import Curriculum, CurriculumError
from services.wayline_forge.app.question_kernel import (
    CompilationError,
    CompileRequest,
    QuestionCompiler,
)
from services.wayline_forge.app.procedure_registry import ProcedureRegistry


EXPECTED_FAMILIES = {
    "place_value": ("valuehold", "place_value"),
    "mental_add": ("valuehold", "mental_add_sub"),
    "decimal_add": ("decimara", "decimal_add_sub"),
    "fraction_add": ("fracture_isles", "fraction_add_sub"),
    "decimal_multiply": ("roundglass", "decimal_multiply_divide"),
    "round_one_decimal": ("roundglass", "rounding_decimal_places"),
    "fraction_multiply": ("reciprocal_deep", "fraction_multiply"),
    "fraction_divide_integer": ("reciprocal_deep", "fraction_divide"),
    "percent_of_amount": ("hundredfold", "percent_of_amount"),
    "decimal_to_percent": ("hundredfold", "decimal_percent_conversion"),
    "negative_add": ("minus_meridian", "negative_add_sub"),
    "mental_multiply": ("factor_forge", "mental_multiply_divide"),
    "hcf": ("factor_forge", "factors_hcf"),
    "bidmas_add_multiply": ("order_spire", "bidmas"),
    "indices_same_base_multiply": ("order_spire", "laws_of_indices"),
}


def independently_solve(family_id: str, operands: dict[str, int]) -> Fraction:
    o = operands
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
        return Fraction(o["whole"] * 10 + o["d1"] + (o["d2"] >= 5), 10)
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
    raise AssertionError(f"uncovered test family: {family_id}")


class QuestionKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = QuestionCompiler.for_tests()
        cls.registry = ProcedureRegistry.for_tests()

    def test_same_seed_produces_same_blueprint(self):
        request = CompileRequest(
            world_id="decimara",
            skill_id="decimal_add_sub",
            family_id="decimal_add",
            difficulty=2,
            seed=731,
        )
        self.assertEqual(self.compiler.compile(request), self.compiler.compile(request))

    def test_decimal_answer_is_exact(self):
        blueprint = self.compiler.compile(
            CompileRequest("decimara", "decimal_add_sub", "decimal_add", 2, 731)
        )
        self.assertIsInstance(blueprint.canonical_answer.value, Fraction)
        self.assertEqual(
            blueprint.canonical_answer.value,
            independently_solve(blueprint.family_id, blueprint.operand_map),
        )

    def test_blueprint_has_allowed_procedures_and_holdout_receipt(self):
        blueprint = self.compiler.compile(
            CompileRequest("valuehold", "place_value", "place_value", 1, 19)
        )
        self.assertGreaterEqual(len(blueprint.allowed_procedure_ids), 3)
        self.assertEqual(blueprint.holdout_receipt.record_count, 140)
        self.assertEqual(
            blueprint.holdout_receipt.source_sha256,
            "ad5b0a15ea5b3f8a306c4f1bda881b6d2ff8cb1951a8b934f5d7b53b6eef4246",
        )
        self.assertFalse(blueprint.holdout_receipt.excluded)

    def test_launch_curriculum_is_exactly_the_fifteen_trained_narrow_families(self):
        families = self.compiler.curriculum.families
        self.assertEqual(set(families), set(EXPECTED_FAMILIES))
        self.assertNotIn("square", families)
        for family_id, family in families.items():
            with self.subTest(family_id=family_id):
                self.assertEqual((family.world_id, family.skill_id), EXPECTED_FAMILIES[family_id])
                self.assertGreaterEqual(len(family.templates), 2)
                self.assertGreaterEqual(len({t.context_id for t in family.templates}), 2)
                self.assertTrue(all(len(t.procedure_ids) >= 3 for t in family.templates))

    def test_place_value_templates_name_the_hundreds_place_when_digits_repeat(self):
        family = self.compiler.curriculum.families["place_value"]
        for template in family.templates:
            with self.subTest(template_id=template.template_id):
                self.assertIn("hundreds place", template.prompt_template.lower())

        repeated_digit_blueprints = []
        for seed in range(200):
            blueprint = self.compiler.compile(
                CompileRequest("valuehold", "place_value", "place_value", 2, seed)
            )
            operands = blueprint.operand_map
            if str(operands["N"]).count(str(operands["d"])) > 1:
                repeated_digit_blueprints.append(blueprint)
        self.assertTrue(repeated_digit_blueprints)
        self.assertTrue(
            all("hundreds place" in blueprint.prompt.lower() for blueprint in repeated_digit_blueprints)
        )

    def test_mental_add_always_requires_a_units_regrouping(self):
        for seed in range(200):
            blueprint = self.compiler.compile(
                CompileRequest("valuehold", "mental_add_sub", "mental_add", seed % 3 + 1, seed)
            )
            operands = blueprint.operand_map
            self.assertGreaterEqual(operands["a"] % 10 + operands["b"] % 10, 10)
            self.assertIn("ma_forgets_ten", blueprint.allowed_procedure_ids)

    def test_fraction_multiply_story_operands_are_proper_fractions(self):
        for seed in range(200):
            blueprint = self.compiler.compile(
                CompileRequest(
                    "reciprocal_deep",
                    "fraction_multiply",
                    "fraction_multiply",
                    seed % 3 + 1,
                    seed,
                )
            )
            operands = blueprint.operand_map
            self.assertLess(operands["a"], operands["b"])
            self.assertLess(operands["c"], operands["d"])

    def test_sampler_contains_no_unbounded_inner_loops(self):
        source = inspect.getsource(QuestionCompiler._sample)
        self.assertNotIn("while ", source)

    def test_rejects_unknown_or_crosswired_curriculum_requests(self):
        invalid = (
            CompileRequest("decimara", "decimal_add_sub", "unknown", 2, 1),
            CompileRequest("valuehold", "decimal_add_sub", "decimal_add", 2, 1),
            CompileRequest("decimara", "place_value", "decimal_add", 2, 1),
            CompileRequest("decimara", "decimal_add_sub", "decimal_add", 0, 1),
            CompileRequest("decimara", "decimal_add_sub", "decimal_add", 4, 1),
            CompileRequest("decimara", "decimal_add_sub", "decimal_add", 2, -1),
        )
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(CompilationError):
                self.compiler.compile(request)

    def test_frozen_holdout_source_and_near_match_are_detected(self):
        repo_root = Path(__file__).resolve().parents[3]
        source = repo_root / "data/processed/eval_heldout.jsonl"
        self.compiler.curriculum.holdout.validate_source(source)

        exact = self.compiler.curriculum.holdout.receipt_for("\\( 0.2 \\div 0.4= \\)")
        near = self.compiler.curriculum.holdout.receipt_for(
            "What is 0.2 divided by 0.4?"
        )
        self.assertTrue(exact.excluded)
        self.assertTrue(near.excluded)

    def test_one_thousand_blueprints_are_exact_distinct_and_holdout_safe(self):
        families = tuple(sorted(self.compiler.curriculum.families.values(), key=lambda f: f.family_id))
        seen_templates: dict[str, set[str]] = {family.family_id: set() for family in families}

        for index in range(1000):
            family = families[index % len(families)]
            request = CompileRequest(
                family.world_id,
                family.skill_id,
                family.family_id,
                (index % 3) + 1,
                100_000 + index,
            )
            blueprint = self.compiler.compile(request)
            seen_templates[family.family_id].add(blueprint.template_id)
            self.assertEqual(
                blueprint.canonical_answer.value,
                independently_solve(blueprint.family_id, blueprint.operand_map),
            )
            values = [
                self.registry.evaluate(procedure_id, blueprint).value
                for procedure_id in blueprint.allowed_procedure_ids
            ]
            self.assertGreaterEqual(len(values), 3)
            self.assertEqual(len(values), len(set(values)))
            self.assertNotIn(blueprint.canonical_answer.value, values)
            self.assertFalse(blueprint.holdout_receipt.excluded)
            self.assertEqual(len(blueprint.content_sha256), 64)
            if index < 30:
                self.assertEqual(blueprint, self.compiler.compile(request))

        for family_id, template_ids in seen_templates.items():
            with self.subTest(family_id=family_id):
                self.assertGreaterEqual(len(template_ids), 2)

    def test_reference_prompt_set_contains_sixty_replayable_prompts(self):
        repo_root = Path(__file__).resolve().parents[3]
        path = repo_root / "data/wayline/runtime/reference_prompts_v1.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 60)
        self.assertEqual(len({row["content_sha256"] for row in rows}), 60)
        counts = {family_id: 0 for family_id in EXPECTED_FAMILIES}
        for row in rows:
            request = CompileRequest(**row["compile_request"])
            blueprint = self.compiler.compile(request)
            counts[request.family_id] += 1
            self.assertEqual(row["question_id"], blueprint.question_id)
            self.assertEqual(row["question"], blueprint.prompt)
            self.assertEqual(row["correct_answer"], blueprint.canonical_answer.display)
            self.assertEqual(row["topic"], blueprint.topic)
            self.assertEqual(row["content_sha256"], blueprint.content_sha256)
            self.assertEqual(row["allowed_procedure_ids"], list(blueprint.allowed_procedure_ids))
            self.assertFalse(blueprint.holdout_receipt.excluded)
        self.assertEqual(set(counts.values()), {4})

    def test_corrupt_curriculum_resource_fails_closed(self):
        with self.assertRaises(CurriculumError):
            self.compiler.curriculum.holdout.validate_source(Path(__file__))

    def test_modified_packaged_curriculum_fails_its_code_owned_digest(self):
        packaged = Path(__file__).resolve().parents[1] / "resources/curriculum_v1.json"
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "curriculum_v1.json"
            payload = json.loads(packaged.read_text(encoding="utf-8"))
            payload["curriculum_id"] = "tampered-launch-core"
            modified.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(CurriculumError):
                Curriculum.packaged_v1(resource_path=modified)


if __name__ == "__main__":
    unittest.main()
