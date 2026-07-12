import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from services.wayline_forge.app import contracts as public_contracts
from services.wayline_forge.app.contracts import (
    BattleCompleted,
    BattleQuizRequest,
    BossGateResult,
    FinalQuizResult,
    InitialSubmission,
    PublicQuizBatch,
    RevivedCombatCompleted,
    RevisionSubmission,
    SealTrialPrepare,
    SealTrialPrepared,
    SealTrialCompleted,
    SecondWindCompleted,
    SecondWindStarted,
    SessionCreate,
    WorldActivated,
    WrongCountResult,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts/wayline/v1"
FIXTURES = CONTRACTS / "fixtures"


class ContractTests(unittest.TestCase):
    def test_seal_trial_contracts_are_shared_closed_and_server_scoped(self):
        request_payload = {
            "schemaVersion": "wayline.v1",
            "requestId": "seal-request-001",
            "sessionId": "session-001",
        }
        request = SealTrialPrepare.model_validate(request_payload)

        self.assertEqual(
            request.model_dump(mode="json", by_alias=True),
            request_payload,
        )
        self.assertNotIn("profileId", SealTrialPrepare.model_json_schema(by_alias=True)["properties"])
        self.assertNotIn("worldId", SealTrialPrepare.model_json_schema(by_alias=True)["properties"])
        with self.assertRaises(ValidationError):
            SealTrialPrepare.model_validate(request_payload | {"profileId": "profile-001"})

        batch = PublicQuizBatch.model_validate_json(
            (FIXTURES / "valid/three-item-batch.json").read_text()
        )
        response = SealTrialPrepared(
            schemaVersion="wayline.v1",
            requestId="seal-request-001",
            worldId="valuehold",
            attemptNumber=1,
            battleId="valuehold_seal_trial_1",
            batch=batch,
        )
        self.assertEqual(response.batch.item_count, 3)
        self.assertTrue((CONTRACTS / "seal-trial-prepare.schema.json").is_file())
        self.assertTrue((CONTRACTS / "seal-trial-prepared.schema.json").is_file())

    def test_public_error_contract_artifacts_exist(self):
        missing_models = {
            name
            for name in ("PublicError", "PublicErrorCode")
            if not hasattr(public_contracts, name)
        }

        self.assertEqual(missing_models, set())
        self.assertTrue((CONTRACTS / "public-error.schema.json").is_file())

    def test_public_error_codes_exactly_cover_transport_and_public_services(self):
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )
        from services.wayline_forge.app.gate_query import BossGateQueryError
        from services.wayline_forge.app.identity_lifecycle import (
            IdentityLifecycleError,
        )
        from services.wayline_forge.app.loopback_security import (
            SecurityRejectionCode,
        )
        from services.wayline_forge.app.orchestrator import (
            BatchPreparationError as InternalPreparationError,
        )
        from services.wayline_forge.app.profile_deletion import (
            ProfileDeletionError,
        )
        from services.wayline_forge.app.quiz_snapshot import QuizSnapshotError
        from services.wayline_forge.app.quiz_submissions import QuizSubmissionError
        from services.wayline_forge.app.runtime_state import RuntimeStateError

        transport_codes = {
            "request_malformed",
            "authorization_required",
            "origin_forbidden",
            "body_too_large",
            "contract_invalid",
            "route_not_found",
            "method_not_allowed",
            "content_type_unsupported",
        }
        service_codes = set().union(
            IdentityLifecycleError._CODES,
            RuntimeStateError._CODES,
            BattlePreparationError._CODES,
            QuizSnapshotError._CODES,
            QuizSubmissionError._CODES,
            BossGateQueryError._CODES,
            ProfileDeletionError._CODES,
        )
        public_codes = {
            code.value for code in public_contracts.PublicErrorCode
        }

        self.assertEqual(public_codes, transport_codes | service_codes)
        self.assertEqual(len(public_codes), 24)

        internal_security_codes = {
            code.value for code in SecurityRejectionCode
        }
        self.assertEqual(
            public_codes & internal_security_codes,
            {"body_too_large"},
        )
        self.assertTrue(
            (internal_security_codes - {"body_too_large"}).isdisjoint(
                public_codes
            )
        )
        self.assertTrue(
            InternalPreparationError._CODES.isdisjoint(public_codes)
        )

    def test_public_error_is_an_immutable_two_field_alias_envelope(self):
        payload = {
            "schemaVersion": "wayline.error.v1",
            "code": "storage_busy",
        }

        error = public_contracts.PublicError.model_validate(payload)

        self.assertEqual(
            error.model_dump(mode="json", by_alias=True),
            payload,
        )
        self.assertEqual(
            {
                field.alias or name
                for name, field in public_contracts.PublicError.model_fields.items()
            },
            {"schemaVersion", "code"},
        )
        with self.assertRaises(ValidationError):
            error.code = public_contracts.PublicErrorCode.INTEGRITY_FAILURE

        forbidden_fields = (
            "detail",
            "message",
            "path",
            "requestId",
            "profileId",
            "sessionId",
            "batchId",
        )
        for field in forbidden_fields:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                public_contracts.PublicError.model_validate(payload | {field: "secret"})
        with self.assertRaises(ValidationError):
            public_contracts.PublicError.model_validate(
                {"schema_version": "wayline.error.v1", "code": "storage_busy"}
            )

    def test_public_error_parser_rejects_duplicate_unknown_and_non_string_codes(self):
        valid_json = (
            '{"schemaVersion":"wayline.error.v1","code":"storage_busy"}'
        )
        parsed = public_contracts.parse_public_json(
            public_contracts.PublicError,
            valid_json,
        )
        self.assertEqual(parsed.code.value, "storage_busy")

        duplicate = (
            '{"schemaVersion":"wayline.error.v1","code":"storage_busy",'
            '"code":"integrity_failure"}'
        )
        with self.assertRaises(public_contracts.DuplicateJsonKeyError) as caught:
            public_contracts.parse_public_json(
                public_contracts.PublicError,
                duplicate,
            )
        self.assertEqual(caught.exception.key, "code")

        baseline = {
            "schemaVersion": "wayline.error.v1",
            "code": "storage_busy",
        }
        invalid_codes = (
            "unknown_error",
            1,
            True,
            b"storage_busy",
            bytearray(b"storage_busy"),
            None,
            ["storage_busy"],
            {"value": "storage_busy"},
        )
        for code in invalid_codes:
            with self.subTest(code=code), self.assertRaises(ValidationError):
                public_contracts.PublicError.model_validate(
                    baseline | {"code": code}
                )
        with self.assertRaises(ValidationError):
            public_contracts.PublicError.model_validate(
                baseline | {"schemaVersion": "wayline.v1"}
            )
        with self.assertRaises(ValidationError):
            public_contracts.PublicError.model_validate(
                baseline | {"unknown": "value"}
            )

    def test_public_error_schema_is_closed_and_exactly_matches_the_model(self):
        schema = json.loads(
            (CONTRACTS / "public-error.schema.json").read_text()
        )
        model_schema = public_contracts.PublicError.model_json_schema(
            by_alias=True
        )
        public_codes = {
            code.value for code in public_contracts.PublicErrorCode
        }

        self.assertEqual(
            schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )
        self._assert_objects_are_closed(schema)
        self.assertEqual(set(schema["properties"]), {"schemaVersion", "code"})
        self.assertEqual(set(schema["required"]), {"schemaVersion", "code"})
        self.assertEqual(
            schema["properties"]["schemaVersion"],
            {"const": "wayline.error.v1"},
        )
        self.assertEqual(schema["properties"]["code"]["type"], "string")
        self.assertEqual(
            set(schema["properties"]["code"]["enum"]),
            public_codes,
        )
        self.assertEqual(
            set(schema["properties"]),
            set(model_schema["properties"]),
        )
        self.assertEqual(
            set(schema["required"]),
            set(model_schema["required"]),
        )

    def test_public_parser_rejects_duplicate_json_keys_before_validation(self):
        payload = (
            '{"schemaVersion":"wayline.v1","batchId":"batch-001",'
            '"itemCount":3,"itemCount":4,"items":[]}'
        )

        with self.assertRaises(public_contracts.DuplicateJsonKeyError) as caught:
            public_contracts.parse_public_json(PublicQuizBatch, payload)

        self.assertEqual(caught.exception.key, "itemCount")

    def test_decoded_python_json_shapes_validate_without_scalar_coercion(self):
        BattleQuizRequest.model_validate(
            {
                "schemaVersion": "wayline.v1",
                "requestId": "request-002",
                "sessionId": "session-001",
                "battleId": "battle-001",
                "worldId": "valuehold",
                "battleTier": "route_1",
            }
        )
        submission = {
            "schemaVersion": "wayline.v1",
            "requestId": "request-001",
            "batchId": "batch-001",
            "itemCount": 3,
            "selections": [
                {"itemId": "item-001", "optionId": "opt-001-a", "confidence": "certain"},
                {"itemId": "item-002", "optionId": "opt-002-b", "confidence": "leaning"},
                {"itemId": "item-003", "optionId": "opt-003-c", "confidence": "guessing"},
            ],
        }
        InitialSubmission.model_validate(submission)
        RevisionSubmission.model_validate(submission)

        with self.assertRaises(ValidationError):
            InitialSubmission.model_validate(submission | {"itemCount": "3"})
        with self.assertRaises(ValidationError):
            BattleQuizRequest.model_validate(
                {
                    "schemaVersion": "wayline.v1",
                    "requestId": "request-002",
                    "sessionId": "session-001",
                    "battleId": "battle-001",
                    "worldId": "valuehold",
                    "battleTier": 1,
                }
            )
        invalid_confidence = json.loads(json.dumps(submission))
        invalid_confidence["selections"][0]["confidence"] = 1
        with self.assertRaises(ValidationError):
            InitialSubmission.model_validate(invalid_confidence)

    def test_only_camel_case_public_input_fields_validate(self):
        snake_case_top_level = {
            "schema_version": "wayline.v1",
            "batch_id": "batch-001",
            "item_count": 3,
            "wrong_count": 2,
            "revision_required": True,
        }
        with self.assertRaises(ValidationError):
            WrongCountResult.model_validate(snake_case_top_level)

        snake_case_nested = {
            "schemaVersion": "wayline.v1",
            "requestId": "request-001",
            "batchId": "batch-001",
            "itemCount": 3,
            "selections": [
                {"item_id": "item-001", "option_id": "opt-001-a", "confidence": "certain"},
                {"itemId": "item-002", "optionId": "opt-002-b", "confidence": "leaning"},
                {"itemId": "item-003", "optionId": "opt-003-c", "confidence": "guessing"},
            ],
        }
        with self.assertRaises(ValidationError):
            InitialSubmission.model_validate_json(json.dumps(snake_case_nested))

    def test_display_normalization_is_frozen_and_duplicate_safe(self):
        self.assertEqual(
            public_contracts.normalize_public_display(
                "\u2003ＦｏＯ\t BAR\n",
            ),
            "foo bar",
        )
        self.assertEqual(
            public_contracts.normalize_public_display("Straße"),
            "strasse",
        )

        duplicate_pairs = (
            ("Straße", "STRASSE", "unicode-casefold"),
            ("1\u20032", "  1 2\t", "unicode-whitespace"),
            ("１２", "12", "fullwidth-nfkc"),
        )
        for first, second, reason in duplicate_pairs:
            with self.subTest(reason=reason):
                payload = json.loads(
                    (FIXTURES / "valid/three-item-batch.json").read_text()
                )
                payload["items"][0]["options"][0]["displayText"] = first
                payload["items"][0]["options"][1]["displayText"] = second
                with self.assertRaises(ValidationError):
                    PublicQuizBatch.model_validate(payload)

    def test_public_batch_contains_no_answer_or_diagnosis_fields(self):
        payload = (FIXTURES / "valid/three-item-batch.json").read_text()

        model = public_contracts.parse_public_json(PublicQuizBatch, payload)

        serialized = model.model_dump_json(by_alias=True).lower()
        for banned in (
            "correctanswer",
            "correct_answer",
            "iscorrect",
            "is_correct",
            "procedureid",
            "procedure_id",
            "misconception",
        ):
            self.assertNotIn(banned, serialized)

    def test_wrong_count_is_exact_and_bounded(self):
        result = WrongCountResult.model_validate(
            {
                "schemaVersion": "wayline.v1",
                "batchId": "b-1",
                "itemCount": 3,
                "wrongCount": 2,
                "revisionRequired": True,
            }
        )

        self.assertEqual(result.wrong_count, 2)

    def test_wrong_count_rejects_impossible_or_inconsistent_values(self):
        impossible = {
            "schemaVersion": "wayline.v1",
            "batchId": "b-1",
            "itemCount": 3,
            "wrongCount": 4,
            "revisionRequired": True,
        }
        inconsistent = impossible | {"wrongCount": 0}

        with self.assertRaises(ValidationError):
            WrongCountResult.model_validate(impossible)
        with self.assertRaises(ValidationError):
            WrongCountResult.model_validate(inconsistent)

    def test_initial_result_reveals_items_only_for_a_zero_wrong_first_pass(self):
        final_result = json.loads(
            (FIXTURES / "valid/final-result.json").read_text()
        )
        final_result["firstPassWrongCount"] = 0
        final_result["finalCorrectCount"] = final_result["itemCount"]
        final_result["revisionUsed"] = False
        for item in final_result["items"]:
            item["firstSelection"] = dict(item["finalSelection"])
            item["firstSelection"]["isCorrect"] = True
            item["finalSelection"]["isCorrect"] = True
            item["possibleError"] = None
            item["selfCorrected"] = False

        zero_wrong = {
            "schemaVersion": "wayline.v1",
            "batchId": final_result["batchId"],
            "itemCount": final_result["itemCount"],
            "wrongCount": 0,
            "revisionRequired": False,
            "finalResult": final_result,
        }
        nonzero = {
            "schemaVersion": "wayline.v1",
            "batchId": "batch-001",
            "itemCount": 3,
            "wrongCount": 1,
            "revisionRequired": True,
            "finalResult": None,
        }

        result_type = public_contracts.InitialSubmissionResult
        self.assertIsNotNone(result_type.model_validate(zero_wrong).final_result)
        self.assertIsNone(result_type.model_validate(nonzero).final_result)

        for invalid in (
            zero_wrong | {"finalResult": None},
            nonzero | {"finalResult": final_result},
            zero_wrong | {
                "finalResult": final_result | {"batchId": "batch-other"}
            },
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    result_type.model_validate(invalid)

    def test_valid_fixtures_validate_with_their_models(self):
        fixtures = (
            ("three-item-batch.json", PublicQuizBatch),
            ("seal-trial-prepare.json", SealTrialPrepare),
            ("seal-trial-prepared.json", SealTrialPrepared),
            ("two-wrong-result.json", WrongCountResult),
            (
                "zero-wrong-initial-result.json",
                public_contracts.InitialSubmissionResult,
            ),
            ("final-result.json", FinalQuizResult),
        )

        for filename, model_type in fixtures:
            with self.subTest(filename=filename):
                payload = (FIXTURES / "valid" / filename).read_text()
                public_contracts.parse_public_json(model_type, payload)

    def test_invalid_fixtures_fail_with_their_models(self):
        fixtures = (
            ("leaked-key.json", PublicQuizBatch),
            ("missing-confidence.json", InitialSubmission),
            ("unknown-field.json", PublicQuizBatch),
            ("count-mismatch.json", PublicQuizBatch),
            ("wrong-count-exceeds-item-count.json", WrongCountResult),
            (
                "initial-zero-without-final.json",
                public_contracts.InitialSubmissionResult,
            ),
            (
                "initial-nonzero-with-final.json",
                public_contracts.InitialSubmissionResult,
            ),
            (
                "initial-final-identity-mismatch.json",
                public_contracts.InitialSubmissionResult,
            ),
            ("final-aggregate-mismatch.json", FinalQuizResult),
            ("gate-mismatch.json", BossGateResult),
            ("seal-trial-battle-id-mismatch.json", SealTrialPrepared),
            ("seal-trial-four-items.json", SealTrialPrepared),
        )

        for filename, model_type in fixtures:
            with self.subTest(filename=filename):
                payload = (FIXTURES / "invalid" / filename).read_text()
                with self.assertRaises(ValidationError):
                    public_contracts.parse_public_json(model_type, payload)

    def test_structural_schemas_defer_dynamic_rules_to_shared_invariants(self):
        wrong_count_schema = json.loads(
            (CONTRACTS / "wrong-count-result.schema.json").read_text()
        )
        self.assertNotIn("allOf", wrong_count_schema)
        self.assertNotIn("$data", json.dumps(wrong_count_schema))

        manifest = json.loads(
            (CONTRACTS / "shared-invariants.v1.json").read_text()
        )
        self.assertEqual(manifest["schemaVersion"], "wayline.shared-invariants.v1")
        self.assertEqual(manifest["contractVersion"], "wayline.v1")
        self.assertEqual(manifest["jsonSchemaScope"], "structural_only")
        self.assertEqual(manifest["semanticEnforcers"], ["python", "unity"])
        self.assertEqual(
            manifest["displayNormalization"]["algorithm"],
            [
                "unicode_nfkc",
                "collapse_unicode_whitespace_runs_to_ascii_space",
                "trim",
                "unicode_casefold",
            ],
        )
        self.assertEqual(
            manifest["displayNormalization"]["unicodeDataVersion"],
            "15.0.0",
        )
        self.assertEqual(
            manifest["displayNormalization"]["testVectors"],
            [
                {"input": "Straße", "normalized": "strasse"},
                {"input": "1\u20032", "normalized": "1 2"},
                {"input": "１２", "normalized": "12"},
            ],
        )
        self.assertIn("Unity", manifest["displayNormalization"]["unityRequirement"])
        self.assertIn(
            "later",
            manifest["displayNormalization"]["numericAnswerValidation"],
        )

        expected_ids = {
            "public_item.unique_option_ids",
            "public_item.unique_display_values",
            "public_batch.item_count_matches_items",
            "public_batch.unique_item_ids",
            "submission.item_count_matches_selections",
            "submission.unique_item_ids",
            "wrong_count.not_above_item_count",
            "wrong_count.revision_required_iff_nonzero",
            "initial_result.final_present_iff_zero",
            "initial_result.final_matches_first_pass",
            "final_item.first_correctness_matches_key",
            "final_item.final_correctness_matches_key",
            "final_item.self_corrected_iff_wrong_to_correct",
            "final_result.item_count_matches_items",
            "final_result.unique_item_ids",
            "final_result.first_wrong_count_matches_items",
            "final_result.final_correct_count_matches_items",
            "final_result.revision_used_iff_initial_wrong",
            "final_result.no_revision_keeps_selection",
            "boss_gate.latest_correct_not_above_items",
            "boss_gate.ready_subskills_not_above_total",
            "boss_gate.unmet_requirements_match_thresholds",
            "boss_gate.unlocked_iff_no_unmet",
            "seal_trial.battle_id_matches_attempt",
            "seal_trial.exactly_three_items",
            "battle_completion.final_correct_not_above_item_count",
            "seal_completion.pass_matches_score",
            "second_wind_started.identities_derived",
            "second_wind_completed.shield_matches_score",
            "revived_combat_completed.battle_matches_win",
            "world_activation.active_differs_from_completed",
        }
        invariants = manifest["invariants"]
        self.assertEqual({rule["id"] for rule in invariants}, expected_ids)
        for rule in invariants:
            self.assertTrue(rule["contract"])
            self.assertTrue(rule["contractSchemas"])
            self.assertTrue(rule["pythonModels"])
            self.assertTrue(rule["description"])
            self.assertTrue(rule["fields"])
            self.assertTrue(rule["invalidFixtures"])

        fixture_rules = {
            fixture: rule["id"]
            for rule in invariants
            for fixture in rule["invalidFixtures"]
        }
        self.assertEqual(
            len(fixture_rules),
            32,
        )

    def test_every_semantic_fixture_is_mapped_and_rejected_by_python(self):
        model_types = {
            "PublicQuizBatch": PublicQuizBatch,
            "InitialSubmission": InitialSubmission,
            "RevisionSubmission": RevisionSubmission,
            "WrongCountResult": WrongCountResult,
            "InitialSubmissionResult": public_contracts.InitialSubmissionResult,
            "FinalQuizResult": FinalQuizResult,
            "BossGateResult": BossGateResult,
            "SealTrialPrepared": SealTrialPrepared,
            "BattleCompleted": BattleCompleted,
            "SealTrialCompleted": SealTrialCompleted,
            "SecondWindStarted": SecondWindStarted,
            "SecondWindCompleted": SecondWindCompleted,
            "RevivedCombatCompleted": RevivedCombatCompleted,
            "WorldActivated": WorldActivated,
        }
        manifest = json.loads(
            (CONTRACTS / "shared-invariants.v1.json").read_text()
        )
        seen: set[str] = set()

        for rule in manifest["invariants"]:
            for fixture_name in rule["invalidFixtures"]:
                with self.subTest(rule=rule["id"], fixture=fixture_name):
                    self.assertNotIn(fixture_name, seen)
                    seen.add(fixture_name)
                    fixture_path = FIXTURES / "invalid" / fixture_name
                    self.assertTrue(fixture_path.is_file())
                    payload = fixture_path.read_text()
                    for model_name in rule["pythonModels"]:
                        with self.assertRaises(ValidationError) as caught:
                            public_contracts.parse_public_json(
                                model_types[model_name],
                                payload,
                            )
                        self.assertIn(
                            rule["pythonErrorContains"],
                            str(caught.exception),
                        )
                        messages = {
                            error["msg"].removeprefix("Value error, ")
                            for error in caught.exception.errors()
                        }
                        self.assertEqual(
                            messages,
                            {rule["pythonErrorContains"]},
                        )

        self.assertEqual(len(seen), 32)

    def test_collection_cardinality_checks_remain_explicit(self):
        public_batch = json.loads(
            (FIXTURES / "valid/three-item-batch.json").read_text()
        )
        too_few_options = json.loads(json.dumps(public_batch))
        too_few_options["items"][0]["options"].pop()
        too_few_items = json.loads(json.dumps(public_batch))
        too_few_items["items"].pop()

        submission = {
            "schemaVersion": "wayline.v1",
            "requestId": "request-001",
            "batchId": "batch-001",
            "itemCount": 3,
            "selections": [
                {"itemId": "item-001", "optionId": "opt-001-a", "confidence": "certain"},
                {"itemId": "item-002", "optionId": "opt-002-b", "confidence": "leaning"},
            ],
        }
        final_result = json.loads(
            (FIXTURES / "valid/final-result.json").read_text()
        )
        final_result["items"].pop()

        cases = (
            (PublicQuizBatch, too_few_options, "options must contain exactly 4 options"),
            (PublicQuizBatch, too_few_items, "items must contain between 3 and 10 items"),
            (InitialSubmission, submission, "selections must contain between 3 and 10 answers"),
            (RevisionSubmission, submission, "selections must contain between 3 and 10 answers"),
            (FinalQuizResult, final_result, "items must contain between 3 and 10 final results"),
        )

        for model_type, payload, expected_message in cases:
            with self.subTest(model=model_type.__name__, message=expected_message):
                with self.assertRaises(ValidationError) as caught:
                    model_type.model_validate(payload)
                messages = {error["msg"] for error in caught.exception.errors()}
                self.assertEqual(messages, {f"Value error, {expected_message}"})

    def test_submissions_require_complete_unique_selections(self):
        payload = {
            "schemaVersion": "wayline.v1",
            "requestId": "request-001",
            "batchId": "batch-001",
            "itemCount": 3,
            "selections": [
                {"itemId": "item-001", "optionId": "opt-001-a", "confidence": "certain"},
                {"itemId": "item-002", "optionId": "opt-002-b", "confidence": "leaning"},
                {"itemId": "item-003", "optionId": "opt-003-c", "confidence": "guessing"},
            ],
        }

        InitialSubmission.model_validate_json(json.dumps(payload))
        RevisionSubmission.model_validate_json(json.dumps(payload))

        duplicate = payload | {"selections": [payload["selections"][0]] * 3}
        with self.assertRaises(ValidationError):
            InitialSubmission.model_validate_json(json.dumps(duplicate))

    def test_session_battle_and_gate_contracts_validate(self):
        SessionCreate.model_validate_json(
            json.dumps(
                {
                    "schemaVersion": "wayline.v1",
                    "requestId": "request-001",
                    "profileId": "profile-001",
                    "clientBuild": "mac-demo-0.1.0",
                }
            )
        )
        BattleQuizRequest.model_validate_json(
            json.dumps(
                {
                    "schemaVersion": "wayline.v1",
                    "requestId": "request-002",
                    "sessionId": "session-001",
                    "battleId": "battle-001",
                    "worldId": "valuehold",
                    "battleTier": "route_1",
                }
            )
        )
        BossGateResult.model_validate_json(
            json.dumps(
                {
                    "schemaVersion": "wayline.v1",
                    "worldId": "valuehold",
                    "unlocked": True,
                    "leadInWins": 4,
                    "requiredLeadInWins": 4,
                    "validWorldItems": 16,
                    "requiredValidWorldItems": 16,
                    "latestTenItemCount": 10,
                    "latestTenCorrectCount": 7,
                    "requiredLatestTenCorrectCount": 7,
                    "coreSubskillCount": 2,
                    "readyCoreSubskillCount": 2,
                    "unmetRequirements": [],
                }
            )
        )

    def test_every_json_schema_is_closed_and_requires_every_property(self):
        schema_names = (
            "session-create.schema.json",
            "battle-quiz-request.schema.json",
            "public-quiz-batch.schema.json",
            "initial-submit.schema.json",
            "wrong-count-result.schema.json",
            "initial-submission-result.schema.json",
            "revision-submit.schema.json",
            "final-quiz-result.schema.json",
            "boss-gate-result.schema.json",
        )

        for schema_name in schema_names:
            with self.subTest(schema=schema_name):
                schema = json.loads((CONTRACTS / schema_name).read_text())
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self._assert_objects_are_closed(schema)

    def test_json_schema_top_level_fields_match_pydantic_models(self):
        pairs = (
            ("session-create.schema.json", SessionCreate),
            ("battle-quiz-request.schema.json", BattleQuizRequest),
            ("public-quiz-batch.schema.json", PublicQuizBatch),
            ("initial-submit.schema.json", InitialSubmission),
            ("wrong-count-result.schema.json", WrongCountResult),
            (
                "initial-submission-result.schema.json",
                public_contracts.InitialSubmissionResult,
            ),
            ("revision-submit.schema.json", RevisionSubmission),
            ("final-quiz-result.schema.json", FinalQuizResult),
            ("boss-gate-result.schema.json", BossGateResult),
        )

        for schema_name, model_type in pairs:
            with self.subTest(schema=schema_name):
                frozen_schema = json.loads((CONTRACTS / schema_name).read_text())
                model_schema = model_type.model_json_schema(by_alias=True)
                self.assertEqual(
                    set(frozen_schema["properties"]),
                    set(model_schema["properties"]),
                )
                self.assertEqual(
                    set(frozen_schema["required"]),
                    set(model_schema["required"]),
                )

    def _assert_objects_are_closed(self, value: object) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                self.assertIs(value.get("additionalProperties"), False)
                self.assertEqual(
                    set(value.get("required", [])),
                    set(value.get("properties", {})),
                )
            for child in value.values():
                self._assert_objects_are_closed(child)
        elif isinstance(value, list):
            for child in value:
                self._assert_objects_are_closed(child)
    RevivedCombatCompleted,
    WorldActivated,
