from dataclasses import replace
import json
import unittest

from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.procedure_registry import ProcedureRegistry
from services.wayline_forge.app.question_kernel import CanonicalAnswer, CompileRequest
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle


class DistractorVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = DistractorVerifier.for_tests()
        self.blueprint = self.verifier.reference_blueprint("decimal-add-731")

    def reseal_blueprint(self, blueprint):
        family = self.verifier.compiler.curriculum.families[blueprint.family_id]
        template = next(
            item for item in family.templates if item.template_id == blueprint.template_id
        )
        operands = blueprint.operand_map
        prompt = template.prompt_template.format(**operands)
        trusted_steps = self.verifier.compiler._trusted_steps(
            blueprint.family_id,
            operands,
            blueprint.canonical_answer.value,
        )
        receipt = self.verifier.compiler.curriculum.holdout.receipt_for(prompt)
        blueprint = replace(
            blueprint,
            prompt=prompt,
            trusted_steps=trusted_steps,
            holdout_receipt=receipt,
        )
        request = CompileRequest(
            blueprint.world_id,
            blueprint.skill_id,
            blueprint.family_id,
            blueprint.difficulty,
            blueprint.seed,
        )
        digest = self.verifier.compiler._content_digest(
            request,
            family,
            template,
            blueprint.operands,
            blueprint.prompt,
            blueprint.canonical_answer,
            blueprint.trusted_steps,
            blueprint.allowed_procedure_ids,
            blueprint.holdout_receipt,
        )
        return replace(
            blueprint,
            content_sha256=digest,
            question_id=f"{blueprint.family_id.replace('_', '-')}-{blueprint.seed}-{digest[:12]}",
        )

    def test_accepts_only_unique_exact_routes_and_seals_internal_options(self):
        first = self.verifier.verify_fixture(self.blueprint, "accepted.json")
        second = self.verifier.verify_fixture(self.blueprint, "accepted.json")

        self.assertTrue(first.accepted)
        self.assertEqual(first, second)
        self.assertIsNone(first.code)
        self.assertEqual(len(first.value.options), 4)
        self.assertEqual(len({option.option_id for option in first.value.options}), 4)
        self.assertEqual(len({option.display_text for option in first.value.options}), 4)
        self.assertEqual(len(first.value.verified_distractors), 3)

        self.assertEqual(first.value.prompt, self.blueprint.prompt)
        option_ids = {option.option_id for option in first.value.options}
        self.assertIn(first.value.correct_option_id, option_ids)
        self.assertEqual(
            {item.option_id for item in first.value.verified_distractors},
            option_ids - {first.value.correct_option_id},
        )
        self.assertFalse(hasattr(first.value, "raw_generation"))
        self.assertFalse(hasattr(first.value, "model_sha256"))
        for option in first.value.options:
            self.assertRegex(option.option_id, r"^opt_[0-9a-f]{24}$")
            self.assertNotIn("correct", option.option_id)
            self.assertNotIn("da_", option.option_id)

    def test_low_level_verified_set_has_no_public_serializer_and_uses_placement(self):
        request = CompileRequest(
            self.blueprint.world_id,
            self.blueprint.skill_id,
            self.blueprint.family_id,
            self.blueprint.difficulty,
            self.blueprint.seed,
        )
        generation = self.verifier.fixture_generation(self.blueprint, "accepted.json")
        result = self.verifier.verify_generation(self.blueprint, generation)
        self.assertTrue(result.accepted)
        bundle = VerifiedQuestionBundle.from_verified(
            compiler=self.verifier.compiler,
            request=request,
            blueprint=self.blueprint,
            verified=result.value,
            generation=generation,
            manifest=self.verifier.manifest,
        )
        placement = bundle.place("item_" + "a" * 32)
        public = placement.public_payload()
        source_option_ids = {option.option_id for option in result.value.options}
        placed_option_ids = {option["optionId"] for option in public["options"]}

        self.assertEqual(set(public), {"itemId", "prompt", "options"})
        self.assertTrue(source_option_ids.isdisjoint(placed_option_ids))
        self.assertFalse(hasattr(result.value, "public_payload"))

    def test_rejects_correct_key_collision(self):
        result = self.verifier.verify_fixture(self.blueprint, "key_collision.json")
        self.assertFalse(result.accepted)
        self.assertIsNone(result.value)
        self.assertEqual(result.code, "correct_key_collision")

    def test_rejects_answer_matching_two_routes(self):
        blueprint = self.verifier.compiler.compile(
            CompileRequest("roundglass", "decimal_multiply_divide", "decimal_multiply", 1, 731)
        )
        drifted_entries = tuple(
            replace(entry, formula_name="dec_one_place")
            if entry.procedure_id == "dec_add"
            else entry
            for entry in self.verifier.registry.entries
        )
        drifted_registry = ProcedureRegistry(
            self.verifier.registry.registry_id,
            drifted_entries,
        )
        verifier = DistractorVerifier(
            self.verifier.compiler,
            drifted_registry,
            self.verifier.manifest,
        )
        generation = self.verifier.fixture_generation(blueprint, "accepted.json")
        generation = replace(
            generation,
            text=json.dumps(
                {
                    "distractors": [
                        {
                            "misconception": drifted_registry.canonical_label("dec_one_place"),
                            "computation": "untrusted",
                            "answer": "0.5",
                        },
                        {
                            "misconception": drifted_registry.canonical_label("dec_no_point"),
                            "computation": "untrusted",
                            "answer": "5",
                        },
                        {
                            "misconception": drifted_registry.canonical_label("dec_too_many_places"),
                            "computation": "untrusted",
                            "answer": "0.005",
                        },
                    ]
                },
                separators=(",", ":"),
            ),
        )
        result = verifier.verify_generation(blueprint, generation)
        self.assertEqual(result.code, "ambiguous_procedure_mapping")
        self.assertIsNone(result.value)

    def test_rejects_consistently_resealed_non_authoritative_operands(self):
        operands = ("4", "3")
        operand_map = dict(zip(self.blueprint.operand_names, map(int, operands), strict=True))
        answer = self.verifier.compiler._solve(self.blueprint.family_id, operand_map)
        forged = self.reseal_blueprint(
            replace(
                self.blueprint,
                operands=operands,
                canonical_answer=CanonicalAnswer(
                    answer,
                    self.verifier.compiler._answer_display(self.blueprint.family_id, answer),
                ),
            )
        )

        result = self.verifier.verify_fixture(forged, "accepted.json")

        self.assertEqual(result.code, "blueprint_not_verifiable")
        self.assertIsNone(result.value)

    def test_rejects_unapproved_label_alias(self):
        result = self.verifier.verify_fixture(self.blueprint, "label_mismatch.json")
        self.assertEqual(result.code, "label_procedure_mismatch")

    def test_strict_parser_and_numeric_rejection_table(self):
        cases = {
            "duplicate_keys.json": "duplicate_json_key",
            "extra_prose.json": "invalid_json",
            "code_fence.json": "invalid_json",
            "wrong_count.json": "wrong_distractor_count",
            "extra_field.json": "invalid_schema",
            "exponent_bomb.json": "invalid_numeric_answer",
            "huge_integer.json": "invalid_numeric_answer",
            "nan.json": "invalid_numeric_answer",
            "infinity.json": "invalid_numeric_answer",
            "unsafe_control.json": "unsafe_text",
            "unsafe_html.json": "unsafe_text",
            "unsupported_answer.json": "unsupported_procedure_mapping",
            "duplicate_answer.json": "duplicate_answer",
        }
        for fixture, expected_code in cases.items():
            with self.subTest(fixture=fixture):
                result = self.verifier.verify_fixture(self.blueprint, fixture)
                self.assertFalse(result.accepted)
                self.assertIsNone(result.value)
                self.assertEqual(result.code, expected_code)

    def test_safe_untrusted_computation_is_discarded_not_displayed(self):
        for fixture in ("operand_substitution.json", "mismatched_computation.json"):
            with self.subTest(fixture=fixture):
                result = self.verifier.verify_fixture(self.blueprint, fixture)
                self.assertTrue(result.accepted)
                canonical = {
                    item.procedure_id: item.computation
                    for item in result.value.verified_distractors
                }
                self.assertEqual(canonical["da_align_wrong"], "(6 + 5)/10 = 1.1")
                self.assertFalse(
                    any("999" in option.display_text for option in result.value.options)
                )
                self.assertFalse(any("999" in text for text in canonical.values()))

    def test_forged_generation_receipts_fail_closed(self):
        generation = self.verifier.fixture_generation(self.blueprint, "accepted.json")
        mutations = (
            replace(generation, model_sha256="f" * 64),
            replace(generation, adapter_identity_receipt_sha256="f" * 64),
            replace(generation, gguf_sha256="f" * 64),
            replace(generation, generator_identity_receipt_sha256="f" * 64),
            replace(generation, prompt_sha256="f" * 64),
            replace(generation, registry_id="forged-registry"),
            replace(generation, generated_at_utc="2026-07-11T18:00:00+05:00"),
        )
        for forged in mutations:
            with self.subTest(forged=forged):
                result = self.verifier.verify_generation(self.blueprint, forged)
                self.assertEqual(result.code, "receipt_mismatch")
                self.assertIsNone(result.value)

    def test_blueprint_with_unknown_allowed_route_fails_closed(self):
        malformed = replace(
            self.blueprint,
            allowed_procedure_ids=self.blueprint.allowed_procedure_ids + ("not_a_route",),
        )
        result = self.verifier.verify_fixture(malformed, "accepted.json")
        self.assertEqual(result.code, "blueprint_not_verifiable")

    def test_tampered_blueprint_prompt_or_content_receipt_fails_closed(self):
        tampered = (
            replace(self.blueprint, prompt=self.blueprint.prompt + " altered"),
            replace(self.blueprint, content_sha256="f" * 64),
            replace(self.blueprint, question_id="decimal-add-forged"),
        )
        for blueprint in tampered:
            with self.subTest(blueprint=blueprint):
                result = self.verifier.verify_fixture(blueprint, "accepted.json")
                self.assertEqual(result.code, "blueprint_not_verifiable")


if __name__ == "__main__":
    unittest.main()
