from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import re
import unittest

from services.wayline_forge.app.question_kernel import CompileRequest
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.verified_question import (
    VERIFIER_RECEIPT_SHA256,
    VERIFIER_VERSION,
    VerifiedQuestionBundle,
    VerifiedQuestionError,
    mint_item_instance_id,
)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expected_semantic_sha256(bundle) -> str:
    blueprint = bundle.blueprint
    option_display = {
        option.option_id: option.display_text for option in bundle.options
    }
    distractors = sorted(
        (
            {
                "procedureId": route.procedure_id,
                "display": option_display[route.option_id],
                "canonicalLabel": route.canonical_label,
                "computation": route.computation,
                "feedback": route.feedback,
                "reliableMethod": route.reliable_method,
            }
            for route in bundle.verified_distractors
        ),
        key=lambda route: route["procedureId"],
    )
    return _canonical_sha256(
        {
            "schemaVersion": "wayline.semantic-question.v1",
            "question": {
                "questionSchemaVersion": blueprint.schema_version,
                "worldId": blueprint.world_id,
                "skillId": blueprint.skill_id,
                "familyId": blueprint.family_id,
                "topic": blueprint.topic,
                "templateId": blueprint.template_id,
                "templateRevision": blueprint.template_revision,
                "contextId": bundle.context_id,
                "operandNames": list(blueprint.operand_names),
                "operands": list(blueprint.operands),
                "solverSpec": blueprint.solver_spec,
                "prompt": blueprint.prompt,
                "canonicalAnswer": {
                    "numerator": blueprint.canonical_answer.value.numerator,
                    "denominator": blueprint.canonical_answer.value.denominator,
                    "display": blueprint.canonical_answer.display,
                },
                "trustedSteps": list(blueprint.trusted_steps),
                "allowedProcedureIds": sorted(blueprint.allowed_procedure_ids),
                "difficulty": blueprint.difficulty,
            },
            "distractors": distractors,
        }
    )


class VerifiedQuestionBundleTests(unittest.TestCase):
    def setUp(self):
        self.verifier = DistractorVerifier.for_tests()
        self.request = CompileRequest(
            "decimara",
            "decimal_add_sub",
            "decimal_add",
            2,
            731,
        )
        self.blueprint = self.verifier.compiler.compile(self.request)
        self.generation = self.verifier.fixture_generation(
            self.blueprint,
            "accepted.json",
        )
        result = self.verifier.verify_generation(self.blueprint, self.generation)
        self.assertTrue(result.accepted)
        self.verified = result.value
        self.bundle = VerifiedQuestionBundle.from_verified(
            compiler=self.verifier.compiler,
            request=self.request,
            blueprint=self.blueprint,
            verified=self.verified,
            generation=self.generation,
            manifest=self.verifier.manifest,
        )

    def test_seals_recomputable_question_and_explicit_provenance_immutably(self):
        self.assertEqual(self.bundle.request, self.request)
        self.assertEqual(self.bundle.blueprint, self.blueprint)
        self.assertEqual(self.bundle.options, self.verified.options)
        self.assertEqual(self.bundle.correct_option_id, self.verified.correct_option_id)
        self.assertEqual(
            self.bundle.verified_distractors,
            self.verified.verified_distractors,
        )
        self.assertEqual(self.bundle.source_bundle_sha256, self.verified.bundle_sha256)
        self.assertEqual(self.bundle.template_id, self.blueprint.template_id)
        family = self.verifier.compiler.curriculum.families[self.blueprint.family_id]
        template = next(
            item for item in family.templates if item.template_id == self.blueprint.template_id
        )
        self.assertEqual(self.bundle.context_id, template.context_id)
        self.assertRegex(self.bundle.operand_signature, r"^[0-9a-f]{64}$")
        self.assertRegex(self.bundle.cache_content_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(
            self.bundle.semantic_content_sha256,
            _expected_semantic_sha256(self.bundle),
        )

        provenance = self.bundle.provenance
        self.assertEqual(provenance.model_id, self.verifier.manifest.model_id)
        self.assertEqual(provenance.model_sha256, self.generation.model_sha256)
        self.assertEqual(
            provenance.adapter_identity_receipt_sha256,
            self.generation.adapter_identity_receipt_sha256,
        )
        self.assertEqual(provenance.gguf_sha256, self.generation.gguf_sha256)
        self.assertEqual(
            provenance.generator_identity_receipt_sha256,
            self.generation.generator_identity_receipt_sha256,
        )
        self.assertEqual(provenance.prompt_sha256, self.generation.prompt_sha256)
        self.assertEqual(
            provenance.prompt_template_sha256,
            self.generation.prompt_template_sha256,
        )
        self.assertEqual(provenance.registry_id, self.generation.registry_id)
        self.assertEqual(provenance.generation_sha256, self.verified.generation_sha256)
        self.assertEqual(
            provenance.generation_receipt_sha256,
            self.verified.receipt_sha256,
        )
        self.assertEqual(provenance.verifier_version, VERIFIER_VERSION)
        self.assertEqual(provenance.verifier_receipt_sha256, VERIFIER_RECEIPT_SHA256)
        with self.assertRaises(FrozenInstanceError):
            self.bundle.context_id = "tampered"

    def test_public_payload_requires_fresh_opaque_item_instance_id(self):
        first_id = mint_item_instance_id()
        second_id = mint_item_instance_id()
        self.assertRegex(first_id, r"^item_[0-9a-f]{32}$")
        self.assertNotEqual(first_id, second_id)

        public = self.bundle.public_payload(first_id)
        self.assertEqual(set(public), {"itemId", "prompt", "options"})
        self.assertEqual(public["itemId"], first_id)
        self.assertEqual(public["prompt"], self.blueprint.prompt)
        self.assertEqual(
            set(public["options"][0]),
            {"optionId", "displayText"},
        )
        public_option_ids = {option["optionId"] for option in public["options"]}
        source_option_ids = {option.option_id for option in self.bundle.options}
        self.assertTrue(all(re.fullmatch(r"opt_[0-9a-f]{32}", value) for value in public_option_ids))
        self.assertTrue(public_option_ids.isdisjoint(source_option_ids))

        serialized = json.dumps(public, sort_keys=True).casefold()
        for forbidden in (
            "correctoption",
            "procedure",
            "diagnosis",
            "misconception",
            "computation",
            "receipt",
            "generation",
            "model_sha",
            "raw",
            "questionid",
            self.blueprint.question_id.casefold(),
        ):
            self.assertNotIn(forbidden, serialized)

    def test_placement_rekeys_and_shuffles_every_cached_instance(self):
        source_before = self.bundle.to_private_json()
        source_ids = {option.option_id for option in self.bundle.options}
        placements = tuple(
            self.bundle.place(f"item_{ordinal:032x}")
            for ordinal in range(1, 17)
        )

        public_id_sets = []
        display_orders = []
        correct_positions = []
        for placement in placements:
            public = placement.public_payload()
            option_ids = {option["optionId"] for option in public["options"]}
            public_id_sets.append(option_ids)
            self.assertTrue(option_ids.isdisjoint(source_ids))
            self.assertTrue(
                all(re.fullmatch(r"opt_[0-9a-f]{32}", value) for value in option_ids)
            )
            display_orders.append(
                tuple(option["displayText"] for option in public["options"])
            )
            correct_positions.append(
                next(
                    index
                    for index, option in enumerate(placement.options)
                    if option.option_id == placement.correct_option_id
                )
            )

        for index, option_ids in enumerate(public_id_sets):
            self.assertTrue(
                option_ids.isdisjoint(set().union(*public_id_sets[:index]))
            )
        self.assertGreater(len(set(display_orders)), 1)
        self.assertGreater(len(set(correct_positions)), 1)
        self.assertEqual(self.bundle.to_private_json(), source_before)

    def test_placement_is_deterministic_immutable_and_seals_scoring_map(self):
        item_id = "item_" + "a" * 32
        first = self.bundle.place(item_id)
        replay = self.bundle.place(item_id)
        self.assertEqual(first, replay)
        self.assertRegex(first.placement_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(
            first.source_semantic_content_sha256,
            self.bundle.semantic_content_sha256,
        )
        self.assertEqual(len(first.options), 4)
        self.assertEqual(len(first.bindings), 4)
        self.assertEqual(
            {binding.instance_option_id for binding in first.bindings},
            {option.option_id for option in first.options},
        )
        self.assertEqual(
            {binding.source_option_id for binding in first.bindings},
            {option.option_id for option in self.bundle.options},
        )

        correct = first.binding_for(first.correct_option_id)
        self.assertEqual(correct.source_option_id, self.bundle.correct_option_id)
        self.assertIsNone(correct.procedure_id)
        self.assertTrue(first.is_correct(first.correct_option_id))
        wrong = next(
            binding for binding in first.bindings if binding.procedure_id is not None
        )
        self.assertFalse(first.is_correct(wrong.instance_option_id))
        self.assertIn(
            wrong.procedure_id,
            {route.procedure_id for route in self.bundle.verified_distractors},
        )
        with self.assertRaises(FrozenInstanceError):
            first.correct_option_id = wrong.instance_option_id

    def test_placement_receipt_binds_semantic_content_hash(self):
        placement = self.bundle.place("item_" + "c" * 32)
        original = placement.source_semantic_content_sha256
        forged = ("0" if original[0] != "0" else "1") + original[1:]
        with self.assertRaises(ValueError):
            replace(
                placement,
                source_semantic_content_sha256=forged,
            )

    def test_placement_public_payload_has_no_scoring_map_or_source_option_id(self):
        placement = self.bundle.place("item_" + "b" * 32)
        public = placement.public_payload()
        serialized = json.dumps(public, sort_keys=True).casefold()
        self.assertEqual(set(public), {"itemId", "prompt", "options"})
        self.assertNotIn("correct", serialized)
        self.assertNotIn("binding", serialized)
        self.assertNotIn("source", serialized)
        self.assertNotIn("procedure", serialized)
        for option in self.bundle.options:
            self.assertNotIn(option.option_id, serialized)

    def test_public_payload_rejects_stable_or_malformed_item_ids(self):
        for item_id in (
            self.blueprint.question_id,
            self.blueprint.content_sha256,
            self.verified.bundle_sha256,
            "item_short",
            "item_" + "g" * 32,
            " item_" + "1" * 32,
        ):
            with self.subTest(item_id=item_id):
                with self.assertRaises(VerifiedQuestionError) as caught:
                    self.bundle.public_payload(item_id)
                self.assertEqual(caught.exception.code, "invalid_item_instance_id")

    def test_rejects_request_or_blueprint_that_cannot_recompile_exactly(self):
        mismatched_requests = (
            replace(self.request, seed=self.request.seed + 1),
            replace(self.request, difficulty=1),
        )
        for request in mismatched_requests:
            with self.subTest(request=request):
                with self.assertRaises(VerifiedQuestionError) as caught:
                    VerifiedQuestionBundle.from_verified(
                        compiler=self.verifier.compiler,
                        request=request,
                        blueprint=self.blueprint,
                        verified=self.verified,
                        generation=self.generation,
                        manifest=self.verifier.manifest,
                    )
                self.assertEqual(caught.exception.code, "blueprint_mismatch")

        tampered = replace(self.blueprint, trusted_steps=("Forged step.",))
        with self.assertRaises(VerifiedQuestionError) as caught:
            VerifiedQuestionBundle.from_verified(
                compiler=self.verifier.compiler,
                request=self.request,
                blueprint=tampered,
                verified=self.verified,
                generation=self.generation,
                manifest=self.verifier.manifest,
            )
        self.assertEqual(caught.exception.code, "blueprint_mismatch")

    def test_rejects_tampered_options_key_routes_and_source_hash(self):
        forged_route = replace(
            self.verified.verified_distractors[0],
            canonical_label="Forged learner diagnosis",
        )
        mutations = (
            replace(self.verified, correct_option_id="opt_" + "f" * 24),
            replace(self.verified, prompt=self.verified.prompt + " altered"),
            replace(self.verified, blueprint_sha256="f" * 64),
            replace(self.verified, bundle_sha256="f" * 64),
            replace(
                self.verified,
                verified_distractors=(
                    forged_route,
                    *self.verified.verified_distractors[1:],
                ),
            ),
        )
        for verified in mutations:
            with self.subTest(verified=verified):
                with self.assertRaises(VerifiedQuestionError) as caught:
                    VerifiedQuestionBundle.from_verified(
                        compiler=self.verifier.compiler,
                        request=self.request,
                        blueprint=self.blueprint,
                        verified=verified,
                        generation=self.generation,
                        manifest=self.verifier.manifest,
                    )
                self.assertEqual(caught.exception.code, "verified_set_mismatch")

    def test_rejects_forged_manifest_or_generation_receipts(self):
        cases = (
            (
                self.generation,
                replace(self.verifier.manifest, model_sha256="f" * 64),
            ),
            (
                replace(self.generation, prompt_sha256="f" * 64),
                self.verifier.manifest,
            ),
            (
                replace(self.generation, generated_at_utc="not-a-timestamp"),
                self.verifier.manifest,
            ),
        )
        for generation, manifest in cases:
            with self.subTest(generation=generation, manifest=manifest):
                with self.assertRaises(VerifiedQuestionError) as caught:
                    VerifiedQuestionBundle.from_verified(
                        compiler=self.verifier.compiler,
                        request=self.request,
                        blueprint=self.blueprint,
                        verified=self.verified,
                        generation=generation,
                        manifest=manifest,
                    )
                self.assertEqual(caught.exception.code, "provenance_mismatch")

    def test_private_json_is_canonical_deterministic_and_contains_no_raw_output(self):
        first = self.bundle.to_private_json()
        second = self.bundle.to_private_json()
        self.assertEqual(first, second)
        self.assertEqual(first, json.dumps(
            json.loads(first),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ))

        private = json.loads(first)
        self.assertEqual(
            set(private),
            {
                "schemaVersion",
                "compileRequest",
                "blueprint",
                "verifiedSet",
                "templateId",
                "contextId",
                "operandSignature",
                "semanticContentSha256",
                "provenance",
                "cacheContentSha256",
            },
        )
        unsigned = dict(private)
        content_sha256 = unsigned.pop("cacheContentSha256")
        self.assertEqual(content_sha256, _canonical_sha256(unsigned))
        self.assertEqual(private["blueprint"]["trustedSteps"], list(self.blueprint.trusted_steps))
        self.assertNotIn(self.generation.text, first)
        lowered_keys = {key.casefold() for key in self._all_keys(private)}
        self.assertFalse(any("raw" in key for key in lowered_keys))
        self.assertNotIn("text", lowered_keys)

    def test_semantic_hash_is_stable_across_distinct_generation_provenance(self):
        later_generation = replace(
            self.generation,
            generated_at_utc="2026-07-11T18:01:00Z",
        )
        later_result = self.verifier.verify_generation(
            self.blueprint,
            later_generation,
        )
        self.assertTrue(later_result.accepted)
        later_bundle = VerifiedQuestionBundle.from_verified(
            compiler=self.verifier.compiler,
            request=self.request,
            blueprint=self.blueprint,
            verified=later_result.value,
            generation=later_generation,
            manifest=self.verifier.manifest,
        )

        self.assertNotEqual(
            self.bundle.cache_content_sha256,
            later_bundle.cache_content_sha256,
        )
        self.assertNotEqual(
            self.bundle.provenance.generation_receipt_sha256,
            later_bundle.provenance.generation_receipt_sha256,
        )
        self.assertEqual(
            self.bundle.semantic_content_sha256,
            later_bundle.semantic_content_sha256,
        )
        self.assertEqual(
            later_bundle.semantic_content_sha256,
            _expected_semantic_sha256(later_bundle),
        )

    def test_private_json_round_trips_with_full_validation(self):
        restored = VerifiedQuestionBundle.from_private_json(
            self.bundle.to_private_json(),
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        self.assertEqual(restored, self.bundle)
        self.assertEqual(restored.to_private_json(), self.bundle.to_private_json())

    def test_private_parser_rejects_duplicate_unknown_and_nonstandard_json(self):
        canonical = self.bundle.to_private_json()
        duplicate = canonical.replace(
            '"schemaVersion":',
            '"schemaVersion":"wayline.verified-question.v1","schemaVersion":',
            1,
        )
        unknown = json.loads(canonical)
        unknown["unexpected"] = "field"
        unsigned = dict(unknown)
        unsigned.pop("cacheContentSha256")
        unknown["cacheContentSha256"] = _canonical_sha256(unsigned)

        for payload in (
            duplicate,
            json.dumps(unknown),
            canonical[:-1] + ',"number":NaN}',
        ):
            with self.subTest(payload=payload[-80:]):
                with self.assertRaises(VerifiedQuestionError) as caught:
                    VerifiedQuestionBundle.from_private_json(
                        payload,
                        compiler=self.verifier.compiler,
                        manifest=self.verifier.manifest,
                    )
                self.assertEqual(caught.exception.code, "invalid_private_payload")

    def test_private_parser_rejects_hash_and_semantic_metadata_tampering(self):
        bad_hash = json.loads(self.bundle.to_private_json())
        bad_hash["contextId"] = "forged-context"
        with self.assertRaises(VerifiedQuestionError) as caught:
            VerifiedQuestionBundle.from_private_json(
                json.dumps(bad_hash),
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )
        self.assertEqual(caught.exception.code, "canonical_hash_mismatch")

        rehashed = json.loads(self.bundle.to_private_json())
        rehashed["contextId"] = "forged-context"
        unsigned = dict(rehashed)
        unsigned.pop("cacheContentSha256")
        rehashed["cacheContentSha256"] = _canonical_sha256(unsigned)
        with self.assertRaises(VerifiedQuestionError) as caught:
            VerifiedQuestionBundle.from_private_json(
                json.dumps(rehashed),
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )
        self.assertEqual(caught.exception.code, "bundle_metadata_mismatch")

        forged_provenance = json.loads(self.bundle.to_private_json())
        forged_provenance["provenance"]["modelId"] = "forged-model"
        unsigned = dict(forged_provenance)
        unsigned.pop("cacheContentSha256")
        forged_provenance["cacheContentSha256"] = _canonical_sha256(unsigned)
        with self.assertRaises(VerifiedQuestionError) as caught:
            VerifiedQuestionBundle.from_private_json(
                json.dumps(forged_provenance),
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )
        self.assertEqual(caught.exception.code, "provenance_mismatch")

    def test_private_parser_rejects_rehashed_forged_semantic_content_hash(self):
        forged = json.loads(self.bundle.to_private_json())
        original = self.bundle.semantic_content_sha256
        forged["semanticContentSha256"] = (
            ("0" if original[0] != "0" else "1") + original[1:]
        )
        unsigned = dict(forged)
        unsigned.pop("cacheContentSha256")
        forged["cacheContentSha256"] = _canonical_sha256(unsigned)

        with self.assertRaises(VerifiedQuestionError) as caught:
            VerifiedQuestionBundle.from_private_json(
                json.dumps(forged),
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )
        self.assertEqual(caught.exception.code, "semantic_content_mismatch")

    def test_private_parser_types_json_escaped_unpaired_surrogates(self):
        canonical = self.bundle.to_private_json()
        escaped_surrogate = '"\\ud800"'
        cases = (
            canonical.replace(
                json.dumps(self.bundle.context_id),
                escaped_surrogate,
                1,
            ),
            canonical.replace(
                json.dumps(self.blueprint.prompt, ensure_ascii=False),
                escaped_surrogate,
                1,
            ),
            canonical.replace('"contextId"', escaped_surrogate, 1),
        )
        for payload in cases:
            self.assertIn("\\ud800", payload)
            with self.subTest(payload=payload[-120:]):
                with self.assertRaises(VerifiedQuestionError) as caught:
                    VerifiedQuestionBundle.from_private_json(
                        payload,
                        compiler=self.verifier.compiler,
                        manifest=self.verifier.manifest,
                    )
                self.assertEqual(caught.exception.code, "invalid_private_payload")

    def test_verifier_receipt_is_stable_and_code_owned(self):
        self.assertEqual(VERIFIER_VERSION, "wayline.distractor-verifier.v1")
        self.assertRegex(VERIFIER_RECEIPT_SHA256, r"^[0-9a-f]{64}$")
        expected = _canonical_sha256(
            {
                "acceptanceContract": "wayline.runtime.acceptance-algorithm.v1",
                "schemaVersion": "wayline.verifier-receipt.v1",
                "verifierVersion": VERIFIER_VERSION,
            }
        )
        self.assertEqual(VERIFIER_RECEIPT_SHA256, expected)

    @classmethod
    def _all_keys(cls, value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from cls._all_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from cls._all_keys(child)


if __name__ == "__main__":
    unittest.main()
