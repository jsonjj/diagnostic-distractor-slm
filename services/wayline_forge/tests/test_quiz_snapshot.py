from __future__ import annotations

from dataclasses import replace
import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from pydantic import ValidationError

from services.wayline_forge.app import contracts as public_contracts
from services.wayline_forge.app import quiz_snapshot
from services.wayline_forge.app.batch_material import VerifiedBatchMaterial
from services.wayline_forge.app.contracts import BattleQuizRequest
from services.wayline_forge.app.events import ObservationEvent
from services.wayline_forge.app.profile_store import (
    IdentityStoreCorruptionError,
    ProfileStore,
    ProfileStoreError,
)
from services.wayline_forge.app.quiz_machine import (
    QuizItemLayout,
    QuizSelection,
    QuizState,
    QuizSubmission,
    close_quiz,
    lock_initial,
    new_quiz,
    submit_initial,
    submit_revision,
)
from services.wayline_forge.app.quiz_store import (
    QuizStore,
    QuizStoreBusyError,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts/wayline/v1"
VALID_FIXTURES = CONTRACTS / "fixtures/valid"


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _selection_payload(
    final_result: dict[str, object],
    *,
    request_id: str,
    selection_key: str,
) -> dict[str, object]:
    items = final_result["items"]
    assert isinstance(items, list)
    selections = []
    for item in items:
        assert isinstance(item, dict)
        selection = item[selection_key]
        assert isinstance(selection, dict)
        selections.append(
            {
                "itemId": item["itemId"],
                "optionId": selection["optionId"],
                "confidence": selection["confidence"],
            }
        )
    return {
        "schemaVersion": "wayline.v1",
        "requestId": request_id,
        "batchId": final_result["batchId"],
        "itemCount": final_result["itemCount"],
        "selections": selections,
    }


class _FailingSnapshotProfileStore:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def load_profile(self, profile_id: str) -> object:
        raise self._error


class _FailingSnapshotQuizStore:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def load(self, batch_id: str, *, profile_id: str) -> object:
        raise self._error


class QuizSnapshotModuleTests(unittest.TestCase):
    def test_authenticated_quiz_snapshot_service_module_exists(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec(
                "services.wayline_forge.app.quiz_snapshot"
            ),
            "authenticated quiz snapshot/reload service is missing",
        )

    def test_public_snapshot_contract_surface_is_explicit(self) -> None:
        for name in ("QuizSnapshotState", "QuizSnapshot"):
            with self.subTest(name=name):
                self.assertTrue(
                    hasattr(public_contracts, name),
                    f"missing public snapshot contract: {name}",
                )

    def test_snapshot_service_api_is_explicit(self) -> None:
        for name in (
            "QuizSnapshotError",
            "QuizSnapshotAccessError",
            "QuizSnapshotUnavailableError",
            "QuizSnapshotIntegrityError",
            "QuizSnapshotService",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(quiz_snapshot, name), f"missing {name}")
        if hasattr(quiz_snapshot, "QuizSnapshotService"):
            self.assertEqual(
                tuple(
                    inspect.signature(
                        quiz_snapshot.QuizSnapshotService
                    ).parameters
                ),
                ("profile_store", "quiz_store"),
            )
            self.assertEqual(
                tuple(
                    inspect.signature(
                        quiz_snapshot.QuizSnapshotService.get
                    ).parameters
                ),
                ("self", "profile_id", "current_session_id", "batch_id"),
            )


class QuizSnapshotContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.public_batch = _read_json(VALID_FIXTURES / "three-item-batch.json")
        cls.revised_final = _read_json(VALID_FIXTURES / "final-result.json")
        cls.zero_initial_result = _read_json(
            VALID_FIXTURES / "zero-wrong-initial-result.json"
        )
        zero_final = cls.zero_initial_result["finalResult"]
        assert isinstance(zero_final, dict)
        cls.zero_final = zero_final
        cls.revised_initial = _selection_payload(
            cls.revised_final,
            request_id="initial-request-001",
            selection_key="firstSelection",
        )
        cls.revision = _selection_payload(
            cls.revised_final,
            request_id="revision-request-001",
            selection_key="finalSelection",
        )
        cls.zero_initial = _selection_payload(
            cls.zero_final,
            request_id="initial-request-zero",
            selection_key="firstSelection",
        )
        cls.nonzero_initial_result = _read_json(
            VALID_FIXTURES / "two-wrong-result.json"
        ) | {"finalResult": None}

    def _payload(
        self,
        state: str,
        *,
        zero_wrong: bool = False,
    ) -> dict[str, object]:
        state_versions = {
            ("ready", False): 1,
            ("initial_locked", False): 2,
            ("revision_open", False): 3,
            ("revealed", False): 4,
            ("closed", False): 5,
            ("revealed", True): 3,
            ("closed", True): 4,
        }
        payload: dict[str, object] = {
            "schemaVersion": "wayline.v1",
            "batchId": "batch-001",
            "quizState": state,
            "stateVersion": state_versions[(state, zero_wrong)],
            "publicBatch": self.public_batch,
            "initialSubmission": None,
            "initialResult": None,
            "revisionSubmission": None,
            "finalResult": None,
        }
        if state == "ready":
            return payload
        payload["initialSubmission"] = (
            self.zero_initial if zero_wrong else self.revised_initial
        )
        if state == "initial_locked":
            return payload
        payload["initialResult"] = (
            self.zero_initial_result
            if zero_wrong
            else self.nonzero_initial_result
        )
        if state == "revision_open":
            return payload
        payload["finalResult"] = (
            self.zero_final if zero_wrong else self.revised_final
        )
        if not zero_wrong:
            payload["revisionSubmission"] = self.revision
        return payload

    def test_all_persisted_public_state_shapes_round_trip_exactly(self) -> None:
        variants = (
            ("ready", False),
            ("initial_locked", False),
            ("revision_open", False),
            ("revealed", False),
            ("closed", False),
            ("revealed", True),
            ("closed", True),
        )
        for state, zero_wrong in variants:
            with self.subTest(state=state, zero_wrong=zero_wrong):
                payload = self._payload(state, zero_wrong=zero_wrong)
                try:
                    contract = public_contracts.QuizSnapshot.model_validate(payload)
                except ValidationError as error:
                    self.fail(f"valid {state} snapshot was rejected: {error}")
                self.assertEqual(
                    contract.model_dump(mode="json", by_alias=True),
                    payload,
                )
                self.assertEqual(contract.quiz_state.value, state)

    def test_preparing_and_impossible_state_shapes_are_rejected(self) -> None:
        invalid_payloads = (
            self._payload("ready") | {"quizState": "preparing", "stateVersion": 0},
            self._payload("ready")
            | {"initialSubmission": self.revised_initial},
            self._payload("initial_locked") | {"initialSubmission": None},
            self._payload("initial_locked")
            | {"initialResult": self.nonzero_initial_result},
            self._payload("revision_open") | {"initialResult": None},
            self._payload("revision_open")
            | {"initialResult": self.zero_initial_result},
            self._payload("revealed") | {"finalResult": None},
            self._payload("revealed") | {"revisionSubmission": None},
            self._payload("revealed", zero_wrong=True)
            | {"revisionSubmission": self.revision},
            self._payload("closed") | {"finalResult": None},
        )
        for index, payload in enumerate(invalid_payloads):
            with self.subTest(case=index):
                with self.assertRaises(ValidationError):
                    public_contracts.QuizSnapshot.model_validate(payload)

    def test_state_version_is_bound_to_the_exact_persisted_shape(self) -> None:
        for state, zero_wrong in (
            ("ready", False),
            ("initial_locked", False),
            ("revision_open", False),
            ("revealed", False),
            ("closed", False),
            ("revealed", True),
            ("closed", True),
        ):
            payload = self._payload(state, zero_wrong=zero_wrong)
            payload["stateVersion"] = int(payload["stateVersion"]) + 1
            with self.subTest(state=state, zero_wrong=zero_wrong):
                with self.assertRaisesRegex(ValidationError, "stateVersion"):
                    public_contracts.QuizSnapshot.model_validate(payload)

    def test_snapshot_binds_batch_items_options_submissions_and_results(self) -> None:
        baseline = self._payload("revealed")
        public_batch_mismatch = json.loads(json.dumps(baseline))
        public_batch_mismatch["publicBatch"]["batchId"] = "batch-other"

        unknown_option = json.loads(json.dumps(baseline))
        unknown_option["initialSubmission"]["selections"][0]["optionId"] = (
            "option-does-not-exist"
        )

        first_selection_mismatch = json.loads(json.dumps(baseline))
        first_selection_mismatch["initialSubmission"]["selections"][0][
            "confidence"
        ] = "guessing"

        final_selection_mismatch = json.loads(json.dumps(baseline))
        final_selection_mismatch["revisionSubmission"]["selections"][0][
            "confidence"
        ] = "guessing"

        wrong_count_mismatch = json.loads(json.dumps(baseline))
        wrong_count_mismatch["initialResult"]["wrongCount"] = 1

        initial_reveal_mismatch = self._payload("revealed", zero_wrong=True)
        initial_reveal_mismatch = json.loads(json.dumps(initial_reveal_mismatch))
        initial_reveal_mismatch["initialResult"]["finalResult"]["items"][0][
            "reliableMethod"
        ] = "A different method."

        for name, payload in (
            ("public-batch", public_batch_mismatch),
            ("unknown-option", unknown_option),
            ("first-selection", first_selection_mismatch),
            ("final-selection", final_selection_mismatch),
            ("wrong-count", wrong_count_mismatch),
            ("zero-reveal", initial_reveal_mismatch),
        ):
            with self.subTest(case=name):
                with self.assertRaises(ValidationError):
                    public_contracts.QuizSnapshot.model_validate(payload)

    def test_unknown_snake_case_and_duplicate_fields_fail_closed(self) -> None:
        payload = self._payload("ready")
        for changed in (
            payload | {"procedureId": "private-procedure"},
            {**payload, "quiz_state": payload["quizState"]},
        ):
            with self.assertRaises(ValidationError):
                public_contracts.QuizSnapshot.model_validate(changed)

        encoded = json.dumps(payload)
        duplicated = encoded.replace(
            '"batchId": "batch-001"',
            '"batchId": "batch-001", "batchId": "batch-other"',
            1,
        )
        with self.assertRaises(public_contracts.DuplicateJsonKeyError):
            public_contracts.parse_public_json(
                public_contracts.QuizSnapshot,
                duplicated,
            )

    def test_pre_reveal_serialization_never_contains_sealed_or_private_fields(self) -> None:
        forbidden = (
            "correctOptionId",
            "correctAnswer",
            "trustedSteps",
            "possibleError",
            "isCorrect",
            "procedureId",
            "evidence",
            "rawSlm",
            "privateMaterial",
            "credentials",
            "sealedQuizSha256",
        )
        for state in ("ready", "initial_locked", "revision_open"):
            try:
                snapshot = public_contracts.QuizSnapshot.model_validate(
                    self._payload(state)
                )
            except ValidationError as error:
                self.fail(f"valid pre-reveal snapshot was rejected: {error}")
            serialized = snapshot.model_dump_json(by_alias=True)
            with self.subTest(state=state):
                for field in forbidden:
                    self.assertNotIn(field, serialized)


class QuizSnapshotSchemaTests(unittest.TestCase):
    schema_path = CONTRACTS / "quiz-snapshot.schema.json"

    def test_frozen_draft_2020_12_snapshot_schema_exists(self) -> None:
        self.assertTrue(
            self.schema_path.is_file(),
            "quiz-snapshot.schema.json is missing",
        )

    def test_schema_is_recursively_closed_and_matches_model_fields(self) -> None:
        schema = _read_json(self.schema_path)
        self.assertEqual(
            schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )
        self.assertEqual(
            schema["$id"],
            "https://wayline.game/contracts/v1/quiz-snapshot.schema.json",
        )
        model_schema = public_contracts.QuizSnapshot.model_json_schema(
            by_alias=True
        )
        self.assertEqual(
            set(schema["properties"]),
            set(model_schema["properties"]),
        )
        self.assertEqual(
            set(schema["required"]),
            set(model_schema["required"]),
        )
        self._assert_objects_are_closed(schema)

        state_definition = schema["properties"]["quizState"]
        self.assertEqual(
            state_definition["enum"],
            ["ready", "initial_locked", "revision_open", "revealed", "closed"],
        )
        self.assertNotIn("preparing", json.dumps(schema))

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


class QuizSnapshotServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from services.wayline_forge.tests.test_batch_material import (
            BatchMaterialTests,
        )

        BatchMaterialTests.setUpClass()
        cls.material_fixture_type = BatchMaterialTests
        cls.verifier = BatchMaterialTests.verifier

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self._temporary_directory.name) / "wayline.sqlite3"
        )
        self.profiles = ProfileStore(self.database_path)
        self.profile = self.profiles.create_profile(
            request_id="profile-request-001"
        )
        self.session = self.profiles.create_session(
            request_id="session-request-001",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.quizzes = self._open_quiz_store()
        self.service = self._service()

    def tearDown(self) -> None:
        self.quizzes.close()
        self.profiles.close()
        self._temporary_directory.cleanup()

    def _open_quiz_store(self) -> QuizStore:
        return QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

    def _service(self) -> quiz_snapshot.QuizSnapshotService:
        return quiz_snapshot.QuizSnapshotService(
            self.profiles,
            self.quizzes,
        )

    def _restart(self) -> None:
        self.quizzes.close()
        self.profiles.close()
        self.profiles = ProfileStore(self.database_path)
        self.quizzes = self._open_quiz_store()
        self.service = self._service()

    def _material(
        self,
        *,
        batch_id: str = "batch-valuehold-001",
        origin_session_id: str | None = None,
    ) -> VerifiedBatchMaterial:
        fixture = self.material_fixture_type(methodName="runTest")
        fixture.setUp()
        fixture.context = replace(
            fixture.context,
            profile_id=self.profile.profile_id,
            session_id=origin_session_id or self.session.session_id,
            battle_id="valuehold_route_1",
        )
        material = fixture.complete_material()
        if material.batch_id != batch_id:
            material = VerifiedBatchMaterial._create(
                batch_id=batch_id,
                context=material.context,
                plan_contract=material.plan_contract,
                items=material.items,
            )
        return material

    def _prepare(
        self,
        *,
        batch_id: str = "batch-valuehold-001",
        request_id: str = "prepare-request-001",
        origin_session_id: str | None = None,
    ) -> tuple[VerifiedBatchMaterial, object]:
        material = self._material(
            batch_id=batch_id,
            origin_session_id=origin_session_id,
        )
        request = BattleQuizRequest(
            schemaVersion="wayline.v1",
            requestId=request_id,
            sessionId=material.context.session_id,
            battleId=material.context.battle_id,
            worldId=material.context.world_id,
            battleTier=material.context.battle_tier,
        )
        prepared = self.quizzes.create_prepared(
            material,
            request=request,
            profile_id=self.profile.profile_id,
        )
        return material, prepared

    @staticmethod
    def _submission(
        material: VerifiedBatchMaterial,
        request_id: str,
        *,
        correct: bool,
    ) -> QuizSubmission:
        selections: list[QuizSelection] = []
        for item in material.items:
            route = next(
                route
                for route in item.routes
                if (route.procedure_id is None) is correct
            )
            selections.append(
                QuizSelection(
                    item_id=item.item_id,
                    option_id=route.option_id,
                    confidence="certain" if correct else "leaning",
                )
            )
        return QuizSubmission(
            schema_version="wayline.v1",
            request_id=request_id,
            batch_id=material.batch_id,
            item_count=len(selections),
            selections=tuple(selections),
        )

    def _observations(
        self,
        material: VerifiedBatchMaterial,
        revealed: object,
        *,
        reveal_session_id: str | None = None,
    ) -> tuple[ObservationEvent, ...]:
        result = revealed.machine.final_result
        self.assertIsNotNone(result)
        assert result is not None
        session_id = reveal_session_id or self.session.session_id
        material_by_item = {item.item_id: item for item in material.items}
        events: list[ObservationEvent] = []
        for offset, final in enumerate(result.items, start=2):
            item = material_by_item[final.item_id]
            first_route = item.route_for_option(final.first_selection.option_id)
            final_route = item.route_for_option(final.final_selection.option_id)
            feedback = tuple(
                value
                for value in (final.possible_error, final.reliable_method)
                if value is not None
            )
            events.append(
                ObservationEvent(
                    schema_version="wayline.event.v1",
                    event_id=f"observation-{material.batch_id}-{offset:03d}",
                    idempotency_id=(
                        f"observation-request-{material.batch_id}-{offset:03d}"
                    ),
                    ordinal=offset,
                    profile_id=self.profile.profile_id,
                    session_id=session_id,
                    world_id=item.bundle.blueprint.world_id,
                    battle_id=material.context.battle_id,
                    occurred_at=f"2026-07-11T20:{offset:02d}:00+00:00",
                    batch_id=material.batch_id,
                    item_id=item.item_id,
                    question_id=item.bundle.blueprint.question_id,
                    template_id=item.bundle.template_id,
                    content_version_id=material.context.content_version_id,
                    skill_id=item.bundle.blueprint.skill_id,
                    world_core_subskill_ids=material.context.core_subskill_ids,
                    operand_signature=item.bundle.operand_signature,
                    context_id=item.bundle.context_id,
                    first_option_id=final.first_selection.option_id,
                    final_option_id=final.final_selection.option_id,
                    first_confidence=final.first_selection.confidence,
                    final_confidence=final.final_selection.confidence,
                    first_correct=final.first_selection.is_correct,
                    final_correct=final.final_selection.is_correct,
                    choice_changed=(
                        final.first_selection.option_id
                        != final.final_selection.option_id
                    ),
                    self_corrected=final.self_corrected,
                    first_procedure_id=first_route.procedure_id,
                    final_procedure_id=final_route.procedure_id,
                    targeted_procedure_ids=item.required_procedure_ids,
                    is_transfer=item.is_transfer,
                    is_changed_context_transfer=item.is_changed_context_transfer,
                    valid_for_progression=item.valid_for_progression,
                    batch_wrong_count=result.first_pass_wrong_count,
                    canonical_feedback=feedback,
                    optional_wording_shown=None,
                    receipts=item.event_receipts,
                )
            )
        return tuple(events)

    def _get(
        self,
        batch_id: str = "batch-valuehold-001",
        *,
        profile_id: str | None = None,
        session_id: str | None = None,
    ) -> public_contracts.QuizSnapshot:
        try:
            return self.service.get(
                profile_id or self.profile.profile_id,
                session_id or self.session.session_id,
                batch_id,
            )
        except NotImplementedError:
            self.fail("QuizSnapshotService.get is not implemented")

    def test_restart_recovers_every_persisted_public_state_including_closed(self) -> None:
        material, prepared = self._prepare()

        self._restart()
        ready = self._get()
        self.assertEqual(ready.quiz_state, public_contracts.QuizSnapshotState.READY)
        self.assertEqual(ready.state_version, prepared.machine.version)
        self.assertEqual(ready.public_batch, material.public_batch)
        self.assertIsNone(ready.initial_submission)

        first_submission = self._submission(
            material,
            "initial-request-001",
            correct=False,
        )
        locked = lock_initial(
            prepared.machine,
            first_submission,
            expected_version=prepared.machine.version,
        )
        self.quizzes.save_transition(
            locked,
            profile_id=self.profile.profile_id,
            expected_version=prepared.machine.version,
        )

        self._restart()
        locked_snapshot = self._get()
        self.assertEqual(
            locked_snapshot.quiz_state,
            public_contracts.QuizSnapshotState.INITIAL_LOCKED,
        )
        self.assertIsNotNone(locked_snapshot.initial_submission)
        self.assertIsNone(locked_snapshot.initial_result)

        initial = submit_initial(
            locked,
            first_submission,
            material.sealed_quiz,
            expected_version=locked.version,
        )
        self.quizzes.save_transition(
            initial.machine,
            profile_id=self.profile.profile_id,
            expected_version=locked.version,
            receipt=initial.receipt,
        )

        self._restart()
        revision_open = self._get()
        self.assertEqual(
            revision_open.quiz_state,
            public_contracts.QuizSnapshotState.REVISION_OPEN,
        )
        self.assertEqual(
            revision_open.initial_result.wrong_count,
            len(material.items),
        )
        self.assertIsNone(revision_open.initial_result.final_result)
        self.assertIsNone(revision_open.revision_submission)
        self.assertIsNone(revision_open.final_result)

        revision = submit_revision(
            initial.machine,
            self._submission(
                material,
                "revision-request-001",
                correct=True,
            ),
            material.sealed_quiz,
            expected_version=initial.machine.version,
        )
        observations = self._observations(material, revision)
        self.quizzes.save_transition(
            revision.machine,
            profile_id=self.profile.profile_id,
            expected_version=initial.machine.version,
            receipt=revision.receipt,
            observation_events=observations,
            observation_session_id=self.session.session_id,
        )

        self._restart()
        revealed = self._get()
        self.assertEqual(
            revealed.quiz_state,
            public_contracts.QuizSnapshotState.REVEALED,
        )
        self.assertIsNotNone(revealed.revision_submission)
        self.assertEqual(
            revealed.final_result.first_pass_wrong_count,
            len(material.items),
        )
        self.assertEqual(
            revealed.final_result.final_correct_count,
            len(material.items),
        )

        closed_machine = close_quiz(
            revision.machine,
            expected_version=revision.machine.version,
        )
        self.quizzes.save_transition(
            closed_machine,
            profile_id=self.profile.profile_id,
            expected_version=revision.machine.version,
        )

        self._restart()
        closed = self._get()
        self.assertEqual(
            closed.quiz_state,
            public_contracts.QuizSnapshotState.CLOSED,
        )
        self.assertEqual(closed.state_version, closed_machine.version)
        self.assertEqual(closed.final_result, revealed.final_result)

    def test_zero_wrong_snapshot_includes_the_only_immediate_final_reveal(self) -> None:
        material, prepared = self._prepare()
        initial = submit_initial(
            prepared.machine,
            self._submission(
                material,
                "initial-request-zero",
                correct=True,
            ),
            material.sealed_quiz,
            expected_version=prepared.machine.version,
        )
        observations = self._observations(material, initial)
        self.quizzes.save_transition(
            initial.machine,
            profile_id=self.profile.profile_id,
            expected_version=prepared.machine.version,
            receipt=initial.receipt,
            observation_events=observations,
            observation_session_id=self.session.session_id,
        )

        self._restart()
        snapshot = self._get()
        self.assertEqual(snapshot.quiz_state.value, "revealed")
        self.assertEqual(snapshot.initial_result.wrong_count, 0)
        self.assertFalse(snapshot.initial_result.revision_required)
        self.assertEqual(snapshot.initial_result.final_result, snapshot.final_result)
        self.assertIsNone(snapshot.revision_submission)

    def test_closed_prior_origin_session_is_allowed_for_current_same_profile(self) -> None:
        original_session_id = self.session.session_id
        material, _ = self._prepare(origin_session_id=original_session_id)
        current = self.profiles.create_session(
            request_id="session-request-002",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )

        with self.assertRaises(quiz_snapshot.QuizSnapshotAccessError):
            self._get(session_id=original_session_id)
        resumed = self._get(session_id=current.session_id)
        self.assertEqual(resumed.batch_id, material.batch_id)
        self.assertEqual(resumed.quiz_state.value, "ready")
        self.assertEqual(
            self.profiles.load_session(original_session_id).profile_id,
            self.profile.profile_id,
        )

    def test_unknown_and_cross_profile_access_are_nonenumerating(self) -> None:
        material, _ = self._prepare()
        other_profile = self.profiles.create_profile(
            request_id="profile-request-other"
        )
        other_session = self.profiles.create_session(
            request_id="session-request-other",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        attempts = (
            (self.profile.profile_id, self.session.session_id, "batch-unknown"),
            (other_profile.profile_id, other_session.session_id, material.batch_id),
            (self.profile.profile_id, "session-unknown", material.batch_id),
            ("profile-unknown", self.session.session_id, material.batch_id),
        )
        messages: set[str] = set()
        codes: set[str] = set()
        for profile_id, session_id, batch_id in attempts:
            with self.subTest(
                profile_id=profile_id,
                session_id=session_id,
                batch_id=batch_id,
            ):
                with self.assertRaises(
                    quiz_snapshot.QuizSnapshotAccessError
                ) as raised:
                    self.service.get(profile_id, session_id, batch_id)
                messages.add(str(raised.exception))
                codes.add(raised.exception.code)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages, {"snapshot_unavailable"})
        self.assertEqual(codes, {"snapshot_unavailable"})

    def test_profile_storage_busy_is_redacted_as_retryable_unavailable(self) -> None:
        causes: tuple[BaseException, ...] = (
            sqlite3.OperationalError(
                "database is locked near /private/learner.sqlite"
            ),
            self._wrapped_profile_busy_error(),
        )
        for cause in causes:
            with self.subTest(cause=type(cause).__name__):
                service = quiz_snapshot.QuizSnapshotService(
                    _FailingSnapshotProfileStore(cause),
                    self.quizzes,
                )
                with self.assertRaises(
                    quiz_snapshot.QuizSnapshotUnavailableError
                ) as caught:
                    service.get(
                        self.profile.profile_id,
                        self.session.session_id,
                        "batch-valuehold-001",
                    )
                self.assertEqual(caught.exception.code, "storage_busy")
                self.assertEqual(str(caught.exception), "storage_busy")
                self.assertNotIn("private", repr(caught.exception).casefold())

    def test_quiz_storage_busy_is_redacted_as_retryable_unavailable(self) -> None:
        service = quiz_snapshot.QuizSnapshotService(
            self.profiles,
            _FailingSnapshotQuizStore(
                QuizStoreBusyError("sensitive SQL and learner identity")
            ),
        )

        with self.assertRaises(
            quiz_snapshot.QuizSnapshotUnavailableError
        ) as caught:
            service.get(
                self.profile.profile_id,
                self.session.session_id,
                "batch-valuehold-001",
            )

        self.assertEqual(caught.exception.code, "storage_busy")
        self.assertEqual(str(caught.exception), "storage_busy")
        self.assertNotIn("sensitive", repr(caught.exception).casefold())

    def test_profile_corruption_is_redacted_as_integrity_failure(self) -> None:
        service = quiz_snapshot.QuizSnapshotService(
            _FailingSnapshotProfileStore(
                IdentityStoreCorruptionError(
                    "profile-id-secret in /private/learner.sqlite"
                )
            ),
            self.quizzes,
        )

        with self.assertRaises(
            quiz_snapshot.QuizSnapshotIntegrityError
        ) as caught:
            service.get(
                self.profile.profile_id,
                self.session.session_id,
                "batch-valuehold-001",
            )

        self.assertEqual(caught.exception.code, "integrity_failure")
        self.assertEqual(str(caught.exception), "integrity_failure")
        self.assertNotIn("secret", repr(caught.exception).casefold())

    def test_unexpected_dependency_failure_is_redacted_as_integrity_failure(
        self,
    ) -> None:
        failures = (
            _FailingSnapshotProfileStore(
                RuntimeError("api-token-secret from /private/profile.sqlite")
            ),
            _FailingSnapshotQuizStore(
                RuntimeError("SELECT private_payload FROM quiz_material")
            ),
        )
        for dependency in failures:
            with self.subTest(dependency=type(dependency).__name__):
                if isinstance(dependency, _FailingSnapshotProfileStore):
                    service = quiz_snapshot.QuizSnapshotService(
                        dependency,
                        self.quizzes,
                    )
                else:
                    service = quiz_snapshot.QuizSnapshotService(
                        self.profiles,
                        dependency,
                    )
                with self.assertRaises(
                    quiz_snapshot.QuizSnapshotIntegrityError
                ) as caught:
                    service.get(
                        self.profile.profile_id,
                        self.session.session_id,
                        "batch-valuehold-001",
                    )
                self.assertEqual(caught.exception.code, "integrity_failure")
                self.assertEqual(str(caught.exception), "integrity_failure")
                self.assertNotIn("private", repr(caught.exception).casefold())

    @staticmethod
    def _wrapped_profile_busy_error() -> ProfileStoreError:
        cause = sqlite3.OperationalError(
            "database is busy near /private/learner.sqlite"
        )
        error = ProfileStoreError("sensitive profile store wrapper")
        error.__cause__ = cause
        return error

    def test_origin_session_from_another_profile_fails_closed(self) -> None:
        other_profile = self.profiles.create_profile(
            request_id="profile-request-other"
        )
        other_session = self.profiles.create_session(
            request_id="session-request-other",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        material, _ = self._prepare(
            origin_session_id=other_session.session_id
        )

        with self.assertRaises(
            quiz_snapshot.QuizSnapshotIntegrityError
        ) as caught:
            self._get(material.batch_id)
        self.assertEqual(caught.exception.code, "integrity_failure")
        self.assertEqual(str(caught.exception), "integrity_failure")

    def test_unknown_origin_session_fails_closed(self) -> None:
        material, _ = self._prepare(origin_session_id="session-missing-origin")

        with self.assertRaises(quiz_snapshot.QuizSnapshotIntegrityError):
            self._get(material.batch_id)

    def test_preparation_only_state_is_unavailable(self) -> None:
        material = self._material()
        layouts = tuple(
            QuizItemLayout(
                item_id=item.item_id,
                option_ids=tuple(option.option_id for option in item.options),
            )
            for item in material.public_batch.items
        )
        self.quizzes.create(
            new_quiz(material.batch_id, layouts),
            profile_id=self.profile.profile_id,
        )

        with self.assertRaises(quiz_snapshot.QuizSnapshotUnavailableError):
            self._get(material.batch_id)

    def test_tampered_machine_row_fails_closed(self) -> None:
        material, _ = self._prepare()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET state = 'revealed' WHERE batch_id = ?",
                (material.batch_id,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(quiz_snapshot.QuizSnapshotIntegrityError):
            self._get(material.batch_id)

    def test_service_serialization_exposes_only_the_strict_public_allowlist(self) -> None:
        material, _ = self._prepare()
        snapshot = self._get(material.batch_id)
        payload = snapshot.model_dump(mode="json", by_alias=True)
        self.assertEqual(
            set(payload),
            {
                "schemaVersion",
                "batchId",
                "quizState",
                "stateVersion",
                "publicBatch",
                "initialSubmission",
                "initialResult",
                "revisionSubmission",
                "finalResult",
            },
        )
        serialized = json.dumps(payload, sort_keys=True).casefold()
        for forbidden in (
            "procedure",
            "evidence",
            "rawslm",
            "private",
            "credential",
            "sealed",
            "apikey",
            "token",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_authenticated_snapshot_reads_issue_zero_sql_writes(self) -> None:
        material, _ = self._prepare()
        profile_statements: list[str] = []
        quiz_statements: list[str] = []
        profile_changes = self.profiles._connection.total_changes
        quiz_connection = self.quizzes._require_connection()
        quiz_changes = quiz_connection.total_changes
        self.profiles._connection.set_trace_callback(profile_statements.append)
        quiz_connection.set_trace_callback(quiz_statements.append)
        try:
            first = self._get(material.batch_id)
            second = self._get(material.batch_id)
        finally:
            self.profiles._connection.set_trace_callback(None)
            quiz_connection.set_trace_callback(None)

        self.assertEqual(first, second)
        self.assertEqual(self.profiles._connection.total_changes, profile_changes)
        self.assertEqual(quiz_connection.total_changes, quiz_changes)
        forbidden = (
            "insert ",
            "update ",
            "delete ",
            "replace ",
            "create ",
            "drop ",
            "alter ",
            "vacuum",
        )
        for statement in (*profile_statements, *quiz_statements):
            normalized = " ".join(statement.casefold().split())
            self.assertFalse(
                normalized.startswith(forbidden),
                f"snapshot read issued a write: {statement}",
            )


if __name__ == "__main__":
    unittest.main()
