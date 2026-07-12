from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from services.wayline_forge.app.batch_material import (
    BatchContext,
    VerifiedBatchMaterial,
)
from services.wayline_forge.app.events import ObservationEvent
from services.wayline_forge.app.quiz_machine import (
    QuizItemLayout,
    QuizSelection,
    QuizSubmission,
    lock_initial,
    mark_ready,
    new_quiz,
    submit_initial,
    submit_revision,
)
from services.wayline_forge.app.quiz_store import (
    QuizStore,
    QuizStoreCorruptionError,
    QuizStoreSchemaError,
    QuizTransitionConflictError,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class VerifiedBatchPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Keep the production-style material factory in one audited fixture.
        # Importing locally prevents unittest from rediscovering that TestCase.
        from services.wayline_forge.tests.test_batch_material import (
            BatchMaterialTests,
        )

        BatchMaterialTests.setUpClass()
        cls.material_fixture_type = BatchMaterialTests

    def setUp(self) -> None:
        self.material_fixture = self.material_fixture_type(methodName="runTest")
        self.material_fixture.setUp()
        self.material = self.material_fixture.complete_material()
        self.profile_id = self.material.context.profile_id
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self._temporary_directory.name) / "wayline.sqlite3"
        )
        self.store = self._open_store()

    def tearDown(self) -> None:
        self.store.close()
        self._temporary_directory.cleanup()

    def _open_store(self) -> QuizStore:
        return QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            compiler=self.material_fixture.verifier.compiler,
            manifest=self.material_fixture.verifier.manifest,
        )

    def restart(self) -> QuizStore:
        self.store.close()
        self.store = self._open_store()
        return self.store

    def layouts(self) -> tuple[QuizItemLayout, ...]:
        return tuple(
            QuizItemLayout(
                item_id=item.placement.item_instance_id,
                option_ids=tuple(
                    option.option_id for option in item.placement.options
                ),
            )
            for item in self.material.items
        )

    def preparing_and_ready(self):
        preparing = new_quiz(self.material.batch_id, self.layouts())
        ready = mark_ready(
            preparing,
            sealed_quiz=self.material.sealed_quiz,
            expected_version=preparing.version,
        )
        return preparing, ready

    def persist_ready(self):
        preparing, ready = self.preparing_and_ready()
        self.store.create(preparing, profile_id=self.profile_id)
        self.store.save_transition(
            ready,
            profile_id=self.profile_id,
            expected_version=preparing.version,
            batch_material=self.material,
            expected_context=self.material.context,
            planned_slots=self.material_fixture.slots,
        )
        return ready

    def submission(
        self,
        request_id: str,
        *,
        correct: bool,
    ) -> QuizSubmission:
        selections: list[QuizSelection] = []
        for item in self.material.items:
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
            batch_id=self.material.batch_id,
            item_count=len(selections),
            selections=tuple(selections),
        )

    def revision_fixture(self):
        ready = self.persist_ready()
        initial = submit_initial(
            ready,
            self.submission("initial-material-001", correct=False),
            self.material.sealed_quiz,
            expected_version=ready.version,
        )
        self.store.save_transition(
            initial.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=initial.receipt,
        )
        revision = submit_revision(
            initial.machine,
            self.submission("revision-material-001", correct=True),
            self.material.sealed_quiz,
            expected_version=initial.machine.version,
        )
        return initial.machine, revision

    def observations_for(self, revision) -> tuple[ObservationEvent, ...]:
        result = revision.machine.final_result
        self.assertIsNotNone(result)
        assert result is not None
        material_by_item = {item.item_id: item for item in self.material.items}
        events: list[ObservationEvent] = []
        for offset, final in enumerate(result.items, start=1):
            item = material_by_item[final.item_id]
            first_route = item.route_for_option(final.first_selection.option_id)
            final_route = item.route_for_option(final.final_selection.option_id)
            feedback = tuple(
                value
                for value in (final.possible_error, final.reliable_method)
                if value is not None
            )
            events.append(ObservationEvent(
                schema_version="wayline.event.v1",
                event_id=f"observation-material-{offset:03d}",
                idempotency_id=f"observation-request-material-{offset:03d}",
                ordinal=offset,
                profile_id=self.profile_id,
                session_id=self.material.context.session_id,
                world_id=item.bundle.blueprint.world_id,
                battle_id=self.material.context.battle_id,
                occurred_at=f"2026-07-11T20:{offset:02d}:00+00:00",
                batch_id=self.material.batch_id,
                item_id=item.item_id,
                question_id=item.bundle.blueprint.question_id,
                template_id=item.bundle.template_id,
                content_version_id=self.material.context.content_version_id,
                skill_id=item.bundle.blueprint.skill_id,
                world_core_subskill_ids=self.material.context.core_subskill_ids,
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
            ))
        return tuple(events)

    def test_ready_atomically_persists_and_restart_reconstructs_exact_material(self):
        preparing, ready = self.preparing_and_ready()
        self.store.create(preparing, profile_id=self.profile_id)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM quiz_batch_material"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

        self.store.save_transition(
            ready,
            profile_id=self.profile_id,
            expected_version=preparing.version,
            batch_material=self.material,
            expected_context=self.material.context,
            planned_slots=self.material_fixture.slots,
        )

        restarted = self.restart()
        self.assertEqual(
            restarted.load(ready.batch_id, profile_id=self.profile_id),
            ready,
        )
        restored = restarted.load_batch_material(
            ready.batch_id,
            profile_id=self.profile_id,
            expected_context=self.material.context,
            planned_slots=self.material_fixture.slots,
        )
        self.assertEqual(restored, self.material)
        self.assertEqual(restored.to_private_json(), self.material.to_private_json())

        connection = sqlite3.connect(self.database_path)
        try:
            row = connection.execute(
                "SELECT m.private_json, m.private_json_sha256, "
                "m.batch_material_sha256, m.sealed_quiz_sha256, m.item_count, "
                "q.batch_material_sha256 "
                "FROM quiz_batch_material AS m JOIN quiz_machines AS q "
                "ON q.batch_id = m.batch_id WHERE m.batch_id = ?",
                (ready.batch_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], self.material.to_private_json())
        self.assertEqual(row[1], _sha256(row[0]))
        self.assertEqual(row[2], self.material.batch_material_sha256)
        self.assertEqual(row[3], ready.sealed_quiz_sha256)
        self.assertEqual(row[4], len(self.material.items))
        self.assertEqual(row[5], self.material.batch_material_sha256)

    def test_strict_ready_requires_material_and_rolls_back_material_failpoint(self):
        preparing, ready = self.preparing_and_ready()
        self.store.create(preparing, profile_id=self.profile_id)

        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                ready,
                profile_id=self.profile_id,
                expected_version=preparing.version,
            )
        self.assertEqual(
            self.store.load(preparing.batch_id, profile_id=self.profile_id),
            preparing,
        )

        self.store._failpoint_stage = "after_material_insert"
        with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
            self.store.save_transition(
                ready,
                profile_id=self.profile_id,
                expected_version=preparing.version,
                batch_material=self.material,
            )
        self.store._failpoint_stage = None

        connection = sqlite3.connect(self.database_path)
        try:
            state, material_count = connection.execute(
                "SELECT state, "
                "(SELECT COUNT(*) FROM quiz_batch_material) "
                "FROM quiz_machines WHERE batch_id = ?",
                (preparing.batch_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(state, "preparing")
        self.assertEqual(material_count, 0)

    def test_material_must_match_profile_batch_context_layout_and_seal(self):
        preparing, ready = self.preparing_and_ready()
        self.store.create(preparing, profile_id=self.profile_id)
        wrong_context = replace(
            self.material.context,
            profile_id="profile-other-002",
        )
        wrong_owner = VerifiedBatchMaterial._create(
            batch_id=self.material.batch_id,
            context=wrong_context,
            items=self.material.items,
        )

        invalid_materials = (
            wrong_owner,
            VerifiedBatchMaterial._create(
                batch_id="batch-other-002",
                context=self.material.context,
                items=self.material.items,
            ),
        )
        for invalid in invalid_materials:
            with self.subTest(invalid=invalid.batch_id):
                with self.assertRaises(QuizTransitionConflictError):
                    self.store.save_transition(
                        ready,
                        profile_id=self.profile_id,
                        expected_version=preparing.version,
                        batch_material=invalid,
                    )

        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                replace(ready, sealed_quiz_sha256="f" * 64),
                profile_id=self.profile_id,
                expected_version=preparing.version,
                batch_material=self.material,
            )
        self.assertEqual(
            self.store.load(preparing.batch_id, profile_id=self.profile_id),
            preparing,
        )

    def test_material_is_immutable_after_the_ready_transition(self):
        ready = self.persist_ready()
        locked = lock_initial(
            ready,
            self.submission("initial-lock-material-001", correct=False),
            expected_version=ready.version,
        )

        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                locked,
                profile_id=self.profile_id,
                expected_version=ready.version,
                batch_material=self.material,
            )
        self.assertEqual(
            self.store.load(ready.batch_id, profile_id=self.profile_id),
            ready,
        )

    def test_ready_missing_material_and_unverified_restart_fail_closed(self):
        ready = self.persist_ready()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "DELETE FROM quiz_batch_material WHERE batch_id = ?",
                (ready.batch_id,),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load(ready.batch_id, profile_id=self.profile_id)

        self.store.close()
        self.database_path.unlink()
        legacy = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )
        preparing, ready = self.preparing_and_ready()
        legacy.create(preparing, profile_id=self.profile_id)
        legacy.save_transition(
            ready,
            profile_id=self.profile_id,
            expected_version=preparing.version,
        )
        legacy.close()
        self.store = self._open_store()

        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load(ready.batch_id, profile_id=self.profile_id)

    def test_material_hash_index_and_rehashed_content_tampering_fail_closed(self):
        ready = self.persist_ready()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_batch_material SET private_json_sha256 = ? "
                "WHERE batch_id = ?",
                ("f" * 64, ready.batch_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load(ready.batch_id, profile_id=self.profile_id)

        self.store.close()
        self.database_path.unlink()
        self.store = self._open_store()
        ready = self.persist_ready()
        forged = json.loads(self.material.to_private_json())
        forged["context"]["sessionId"] = "session-forged-002"
        unsigned = dict(forged)
        unsigned.pop("batchMaterialSha256")
        forged["batchMaterialSha256"] = _sha256(_canonical_json(unsigned))
        forged_json = _canonical_json(forged)
        forged_context_json = _canonical_json(forged["context"])
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_batch_material SET private_json = ?, "
                "private_json_sha256 = ?, batch_material_sha256 = ?, "
                "context_json = ?, context_sha256 = ? WHERE batch_id = ?",
                (
                    forged_json,
                    _sha256(forged_json),
                    forged["batchMaterialSha256"],
                    forged_context_json,
                    _sha256(forged_context_json),
                    ready.batch_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load(ready.batch_id, profile_id=self.profile_id)

    def test_reveal_observations_are_bound_to_exact_persisted_material(self):
        revision_open, revision = self.revision_fixture()
        events = self.observations_for(revision)
        first_item = self.material.items[0]
        other_route = next(
            route
            for route in first_item.routes
            if route.procedure_id is not None
            and route.procedure_id != events[0].first_procedure_id
        )
        for forged in (
            replace(events[0], first_procedure_id=other_route.procedure_id),
            replace(events[0], question_id="question-forged-002"),
            replace(events[0], optional_wording_shown="Unreceipted wording"),
        ):
            with self.subTest(forged=forged):
                with self.assertRaises(QuizTransitionConflictError):
                    self.store.save_transition(
                        revision.machine,
                        profile_id=self.profile_id,
                        expected_version=revision_open.version,
                        receipt=revision.receipt,
                        observation_events=(forged, *events[1:]),
                        observation_session_id=events[0].session_id,
                    )
                self.assertEqual(
                    self.store.load(
                        revision_open.batch_id,
                        profile_id=self.profile_id,
                    ),
                    revision_open,
                )

        persisted = self.store.save_transition(
            revision.machine,
            profile_id=self.profile_id,
            expected_version=revision_open.version,
            receipt=revision.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        self.assertFalse(persisted.replayed)

    def test_persisted_prior_session_batch_reveals_in_later_session(self):
        revision_open, revision = self.revision_fixture()
        preparation_session_id = self.material.context.session_id
        reveal_session_id = "session-material-resumed-002"
        self.assertNotEqual(reveal_session_id, preparation_session_id)
        events = tuple(
            replace(event, session_id=reveal_session_id)
            for event in self.observations_for(revision)
        )

        persisted = self.store.save_transition(
            revision.machine,
            profile_id=self.profile_id,
            expected_version=revision_open.version,
            receipt=revision.receipt,
            observation_events=events,
            observation_session_id=reveal_session_id,
        )

        self.assertFalse(persisted.replayed)
        self.assertEqual(
            self.restart().pending_observations(self.profile_id),
            events,
        )

    def test_final_feedback_must_belong_to_the_exact_selected_route(self):
        revision_open, revision = self.revision_fixture()
        events = self.observations_for(revision)
        result = revision.machine.final_result
        self.assertIsNotNone(result)
        assert result is not None
        first_result = result.items[0]
        first_material = self.material.items[0]
        wrong_feedback = next(
            route.feedback
            for route in first_material.routes
            if route.procedure_id is not None
            and route.procedure_id != events[0].first_procedure_id
        )
        self.assertIsNotNone(wrong_feedback)
        assert wrong_feedback is not None
        sealed_first = next(
            item
            for item in self.material.sealed_quiz.items
            if item.item_id == first_result.item_id
        )
        self.assertIn(
            wrong_feedback,
            {feedback for _, feedback in sealed_first.possible_errors},
        )
        forged_item = replace(first_result, possible_error=wrong_feedback)
        forged_result = replace(
            result,
            items=(forged_item, *result.items[1:]),
        )
        output_sha256 = _sha256(_canonical_json(forged_result.to_public_dict()))
        receipt_fields = {
            "action": revision.receipt.action,
            "batchId": revision.receipt.batch_id,
            "requestId": revision.receipt.request_id,
            "payloadSha256": revision.receipt.payload_sha256,
            "fromVersion": revision.receipt.from_version,
            "toVersion": revision.receipt.to_version,
            "outputSha256": output_sha256,
        }
        forged_receipt = replace(
            revision.receipt,
            output_sha256=output_sha256,
            receipt_sha256=_sha256(_canonical_json(receipt_fields)),
        )
        forged_machine = replace(
            revision.machine,
            final_result=forged_result,
            revision_receipt=forged_receipt,
        )
        forged_event = replace(
            events[0],
            canonical_feedback=(wrong_feedback, forged_item.reliable_method),
        )

        with self.assertRaises(QuizTransitionConflictError) as raised:
            self.store.save_transition(
                forged_machine,
                profile_id=self.profile_id,
                expected_version=revision_open.version,
                receipt=forged_receipt,
                observation_events=(forged_event, *events[1:]),
                observation_session_id=events[0].session_id,
            )
        self.assertEqual(
            getattr(raised.exception.__cause__, "code", None),
            "final_feedback_mismatch",
        )
        self.assertEqual(
            self.store.load(revision_open.batch_id, profile_id=self.profile_id),
            revision_open,
        )

    def test_profile_delete_cascades_private_material(self):
        ready = self.persist_ready()
        self.store.delete_profile(self.profile_id)

        connection = sqlite3.connect(self.database_path)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM quiz_batch_material WHERE batch_id = ?",
                (ready.batch_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_v4_store_is_rejected_without_mutation(self):
        self.store.close()
        self.database_path.unlink()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "CREATE TABLE quiz_store_metadata ("
                "singleton INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO quiz_store_metadata VALUES (1, 4)"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(QuizStoreSchemaError):
            self._open_store()

        connection = sqlite3.connect(self.database_path)
        try:
            version = connection.execute(
                "SELECT schema_version FROM quiz_store_metadata WHERE singleton = 1"
            ).fetchone()[0]
            material_table = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'quiz_batch_material'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, 4)
        self.assertEqual(material_table, 0)


if __name__ == "__main__":
    unittest.main()
