from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from services.wayline_forge.app.events import ObservationEvent, ProvenanceReceipts
from services.wayline_forge.app.profile_store import ProfileStore
from services.wayline_forge.app.quiz_machine import (
    QuizItemLayout,
    QuizMachine,
    QuizSelection,
    QuizSubmission,
    SealedQuiz,
    SealedQuizItem,
    close_quiz,
    lock_initial,
    mark_ready,
    new_quiz,
    resolve_initial,
    submit_initial,
    submit_revision,
)
from services.wayline_forge.app.quiz_store import (
    QuizNotFoundError,
    QuizOwnershipError,
    QuizStore,
    QuizStoreCorruptionError,
    QuizStoreSchemaError,
    QuizTransitionConflictError,
)


class QuizStoreFixture:
    receipts = ProvenanceReceipts(
        generator="generator-test-v1",
        model="model-test-v1",
        adapter="adapter-test-v1",
        gguf="gguf-test-v1",
        verifier="verifier-test-v1",
        registry="registry-test-v1",
        cache="cache-test-v1",
    )

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self._temporary_directory.name) / "wayline.sqlite3"
        self.profile_id = "profile-owner-001"
        self.store = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )

    def tearDown(self) -> None:
        self.store.close()
        self._temporary_directory.cleanup()

    def restart(self) -> QuizStore:
        self.store.close()
        self.store = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )
        return self.store

    @staticmethod
    def layouts(batch_id: str = "batch-001") -> tuple[QuizItemLayout, ...]:
        del batch_id
        return tuple(
            QuizItemLayout(
                item_id=f"item-{number}",
                option_ids=tuple(
                    f"item-{number}-option-{letter}"
                    for letter in ("a", "b", "c", "d")
                ),
            )
            for number in range(1, 4)
        )

    @classmethod
    def sealed(cls, batch_id: str = "batch-001") -> SealedQuiz:
        layouts = cls.layouts(batch_id)
        return SealedQuiz(
            batch_id=batch_id,
            items=tuple(
                SealedQuizItem(
                    item_id=layout.item_id,
                    correct_option_id=layout.option_ids[0],
                    correct_answer=f"answer-{number}",
                    trusted_steps=(f"trusted-step-{number}",),
                    possible_errors=tuple(
                        (option_id, f"possible-error-{number}-{index}")
                        for index, option_id in enumerate(layout.option_ids[1:], 1)
                    ),
                    reliable_method=f"reliable-method-{number}",
                )
                for number, layout in enumerate(layouts, 1)
            ),
        )

    @classmethod
    def submission(
        cls,
        request_id: str,
        *,
        batch_id: str = "batch-001",
        choices: tuple[int, int, int] = (0, 0, 0),
        confidences: tuple[str, str, str] = ("certain", "leaning", "guessing"),
    ) -> QuizSubmission:
        layouts = cls.layouts(batch_id)
        return QuizSubmission(
            schema_version="wayline.v1",
            request_id=request_id,
            batch_id=batch_id,
            item_count=3,
            selections=tuple(
                QuizSelection(
                    item_id=layout.item_id,
                    option_id=layout.option_ids[choice],
                    confidence=confidence,
                )
                for layout, choice, confidence in zip(
                    layouts,
                    choices,
                    confidences,
                    strict=True,
                )
            ),
        )

    @classmethod
    def preparing(cls, batch_id: str = "batch-001") -> QuizMachine:
        return new_quiz(batch_id, cls.layouts(batch_id))

    @classmethod
    def observations_for(
        cls,
        machine: QuizMachine,
        *,
        profile_id: str = "profile-owner-001",
        start_ordinal: int = 1,
    ) -> tuple[ObservationEvent, ...]:
        result = machine.final_result
        if result is None:
            raise AssertionError("observation fixtures require a revealed machine")
        observations: list[ObservationEvent] = []
        for offset, item in enumerate(result.items):
            first = item.first_selection
            final = item.final_selection
            observations.append(ObservationEvent(
                schema_version="wayline.event.v1",
                event_id=f"observation-{machine.batch_id}-{item.item_id}",
                idempotency_id=f"outbox-{machine.batch_id}-{item.item_id}",
                ordinal=start_ordinal + offset,
                profile_id=profile_id,
                session_id="session-outbox-001",
                world_id="valuehold",
                battle_id="battle-outbox-001",
                occurred_at=f"2026-07-11T14:{offset:02d}:00+00:00",
                batch_id=machine.batch_id,
                item_id=item.item_id,
                question_id=f"question-{machine.batch_id}-{item.item_id}",
                template_id=f"template-{item.item_id}",
                content_version_id="content-test-v1",
                skill_id="place_value",
                world_core_subskill_ids=("place_value", "mental_add_sub"),
                operand_signature=f"operands-{machine.batch_id}-{item.item_id}",
                context_id=f"context-{item.item_id}",
                first_option_id=first.option_id,
                final_option_id=final.option_id,
                first_confidence=first.confidence,
                final_confidence=final.confidence,
                first_correct=first.is_correct,
                final_correct=final.is_correct,
                choice_changed=first.option_id != final.option_id,
                self_corrected=item.self_corrected,
                first_procedure_id=(
                    None if first.is_correct else f"first-route-{offset}"
                ),
                final_procedure_id=(
                    None if final.is_correct else f"final-route-{offset}"
                ),
                targeted_procedure_ids=(),
                is_transfer=False,
                is_changed_context_transfer=False,
                valid_for_progression=True,
                batch_wrong_count=result.first_pass_wrong_count,
                canonical_feedback=(
                    item.possible_error or "The first method was verified.",
                    item.reliable_method,
                ),
                optional_wording_shown=None,
                receipts=cls.receipts,
            ))
        return tuple(observations)

    def persist_ready(
        self,
        batch_id: str = "batch-001",
        *,
        profile_id: str | None = None,
    ) -> QuizMachine:
        owner = profile_id or self.profile_id
        preparing = self.preparing(batch_id)
        self.store.create(preparing, profile_id=owner)
        ready = mark_ready(
            preparing,
            sealed_quiz=self.sealed(batch_id),
            expected_version=preparing.version,
        )
        persisted = self.store.save_transition(
            ready,
            profile_id=owner,
            expected_version=preparing.version,
        )
        self.assertFalse(persisted.replayed)
        return ready

    def persist_revision_open(
        self,
        batch_id: str = "batch-001",
        *,
        profile_id: str | None = None,
    ) -> QuizMachine:
        owner = profile_id or self.profile_id
        ready = self.persist_ready(batch_id, profile_id=owner)
        initial = submit_initial(
            ready,
            self.submission(
                "initial-001",
                batch_id=batch_id,
                choices=(1, 0, 0),
            ),
            self.sealed(batch_id),
            expected_version=ready.version,
        )
        self.store.save_transition(
            initial.machine,
            profile_id=owner,
            expected_version=ready.version,
            receipt=initial.receipt,
        )
        return initial.machine


class QuizStorePersistenceTests(QuizStoreFixture, unittest.TestCase):
    def test_create_rejects_a_second_live_batch_but_profiles_are_independent(self):
        first = self.preparing("batch-owner-001")
        blocked = self.preparing("batch-owner-002")
        independent = self.preparing("batch-other-001")
        self.store.create(first, profile_id=self.profile_id)

        with self.assertRaises(QuizTransitionConflictError):
            self.store.create(blocked, profile_id=self.profile_id)

        self.assertEqual(
            self.store.create(independent, profile_id="profile-other-002"),
            independent,
        )
        self.assertEqual(
            self.store.load(first.batch_id, profile_id=self.profile_id),
            first,
        )
        with self.assertRaises(QuizNotFoundError):
            self.store.load(blocked.batch_id, profile_id=self.profile_id)

    def test_create_treats_preexisting_multiple_live_batches_as_corruption(self):
        first = self.preparing("batch-owner-001")
        second = self.preparing("batch-other-001")
        self.store.create(first, profile_id=self.profile_id)
        self.store.create(second, profile_id="profile-other-002")
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET profile_id = ? WHERE batch_id = ?",
                (self.profile_id, second.batch_id),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(QuizStoreCorruptionError):
            self.store.create(
                self.preparing("batch-owner-003"),
                profile_id=self.profile_id,
            )

    def test_restart_round_trips_every_machine_state_exactly(self):
        preparing = self.preparing()
        self.store.create(preparing, profile_id=self.profile_id)
        self.assertEqual(
            self.restart().load(preparing.batch_id, profile_id=self.profile_id),
            preparing,
        )

        ready = mark_ready(
            preparing,
            sealed_quiz=self.sealed(),
            expected_version=preparing.version,
        )
        self.store.save_transition(
            ready,
            profile_id=self.profile_id,
            expected_version=preparing.version,
        )
        self.assertEqual(
            self.restart().load(ready.batch_id, profile_id=self.profile_id),
            ready,
        )
        self.assertRegex(ready.sealed_quiz_sha256 or "", r"^[0-9a-f]{64}$")

        locked = lock_initial(
            ready,
            self.submission("initial-001", choices=(1, 0, 2)),
            expected_version=ready.version,
        )
        self.store.save_transition(
            locked,
            profile_id=self.profile_id,
            expected_version=ready.version,
        )
        self.assertEqual(
            self.restart().load(locked.batch_id, profile_id=self.profile_id),
            locked,
        )

        initial = resolve_initial(
            locked,
            self.sealed(),
            expected_version=locked.version,
        )
        self.store.save_transition(
            initial.machine,
            profile_id=self.profile_id,
            expected_version=locked.version,
            receipt=initial.receipt,
        )
        self.assertEqual(
            self.restart().load(
                initial.machine.batch_id,
                profile_id=self.profile_id,
            ),
            initial.machine,
        )

        revision = submit_revision(
            initial.machine,
            self.submission("revision-001", choices=(0, 0, 3)),
            self.sealed(),
            expected_version=initial.machine.version,
        )
        self.store.save_transition(
            revision.machine,
            profile_id=self.profile_id,
            expected_version=initial.machine.version,
            receipt=revision.receipt,
            observation_events=self.observations_for(revision.machine),
            observation_session_id="session-outbox-001",
        )
        self.assertEqual(
            self.restart().load(
                revision.machine.batch_id,
                profile_id=self.profile_id,
            ),
            revision.machine,
        )

        closed = close_quiz(revision.machine, expected_version=revision.machine.version)
        self.store.save_transition(
            closed,
            profile_id=self.profile_id,
            expected_version=revision.machine.version,
        )
        self.assertEqual(
            self.restart().load(closed.batch_id, profile_id=self.profile_id),
            closed,
        )
        next_batch = self.preparing("batch-002")
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET state = ? WHERE batch_id = ?",
                ("ready", closed.batch_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(QuizStoreCorruptionError):
            self.store.create(next_batch, profile_id=self.profile_id)

        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET state = ? WHERE batch_id = ?",
                ("closed", closed.batch_id),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(
            self.store.create(next_batch, profile_id=self.profile_id),
            next_batch,
        )

    def test_zero_wrong_revealed_state_round_trips_with_final_result(self):
        ready = self.persist_ready()
        initial = submit_initial(
            ready,
            self.submission("initial-001"),
            self.sealed(),
            expected_version=ready.version,
        )

        self.store.save_transition(
            initial.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=initial.receipt,
            observation_events=self.observations_for(initial.machine),
            observation_session_id="session-outbox-001",
        )

        self.assertEqual(
            self.restart().load(ready.batch_id, profile_id=self.profile_id),
            initial.machine,
        )
        self.assertIsNotNone(initial.machine.final_result)
        self.assertIsNone(initial.machine.revision_submission)

    def test_pre_reveal_snapshots_contain_submissions_but_no_answer_key_fields(self):
        ready = self.persist_ready()
        locked = lock_initial(
            ready,
            self.submission("initial-001", choices=(1, 0, 2)),
            expected_version=ready.version,
        )
        self.store.save_transition(
            locked,
            profile_id=self.profile_id,
            expected_version=ready.version,
        )

        connection = sqlite3.connect(self.database_path)
        try:
            machine_json = str(connection.execute(
                "SELECT machine_json FROM quiz_machines WHERE batch_id = ?",
                (ready.batch_id,),
            ).fetchone()[0])
        finally:
            connection.close()

        self.assertIn('"initialSubmission"', machine_json)
        self.assertIn('"item-1-option-b"', machine_json)
        for forbidden_key in (
            '"correctOptionId"',
            '"correctAnswer"',
            '"trustedSteps"',
            '"possibleError"',
            '"isCorrect"',
        ):
            self.assertNotIn(forbidden_key, machine_json)

    def test_machine_is_stored_as_canonical_text_json_with_matching_hash(self):
        self.store.create(self.preparing(), profile_id=self.profile_id)
        connection = sqlite3.connect(self.database_path)
        try:
            row = connection.execute(
                "SELECT typeof(machine_json), machine_json, machine_sha256 "
                "FROM quiz_machines WHERE batch_id = 'batch-001'"
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(row[0], "text")
        parsed = json.loads(row[1])
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual(row[1], canonical)
        self.assertEqual(
            row[2],
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def test_missing_batch_fails_closed(self):
        with self.assertRaises(QuizNotFoundError):
            self.store.load("batch-missing", profile_id=self.profile_id)

    def test_invalid_json_or_hash_tampering_is_detected(self):
        self.store.create(self.preparing(), profile_id=self.profile_id)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET machine_json = '{' WHERE batch_id = ?",
                ("batch-001",),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load("batch-001", profile_id=self.profile_id)

        self.store.close()
        self.database_path.unlink()
        self.store = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )
        self.store.create(self.preparing(), profile_id=self.profile_id)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET machine_sha256 = ? WHERE batch_id = ?",
                ("0" * 64, "batch-001"),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load("batch-001", profile_id=self.profile_id)

    def test_duplicate_keys_and_noncanonical_json_are_detected_even_with_new_hash(self):
        self.store.create(self.preparing(), profile_id=self.profile_id)
        connection = sqlite3.connect(self.database_path)
        try:
            original = str(connection.execute(
                "SELECT machine_json FROM quiz_machines WHERE batch_id = ?",
                ("batch-001",),
            ).fetchone()[0])
            duplicate = '{"batchId":"forged",' + original[1:]
            connection.execute(
                "UPDATE quiz_machines SET machine_json = ?, machine_sha256 = ? "
                "WHERE batch_id = ?",
                (
                    duplicate,
                    hashlib.sha256(duplicate.encode("utf-8")).hexdigest(),
                    "batch-001",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load("batch-001", profile_id=self.profile_id)

        self.store.close()
        self.database_path.unlink()
        self.store = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )
        self.store.create(self.preparing(), profile_id=self.profile_id)
        connection = sqlite3.connect(self.database_path)
        try:
            canonical = str(connection.execute(
                "SELECT machine_json FROM quiz_machines WHERE batch_id = ?",
                ("batch-001",),
            ).fetchone()[0])
            noncanonical = json.dumps(json.loads(canonical), ensure_ascii=False)
            connection.execute(
                "UPDATE quiz_machines SET machine_json = ?, machine_sha256 = ? "
                "WHERE batch_id = ?",
                (
                    noncanonical,
                    hashlib.sha256(noncanonical.encode("utf-8")).hexdigest(),
                    "batch-001",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load("batch-001", profile_id=self.profile_id)

    def test_row_index_and_embedded_machine_must_agree(self):
        self.store.create(self.preparing(), profile_id=self.profile_id)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET version = version + 1 WHERE batch_id = ?",
                ("batch-001",),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load("batch-001", profile_id=self.profile_id)

    def test_receipt_json_and_hash_are_verified_on_every_load(self):
        self.persist_revision_open()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_transition_receipts SET receipt_sha256 = ? "
                "WHERE batch_id = ? AND action = 'initial'",
                ("f" * 64, "batch-001"),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load("batch-001", profile_id=self.profile_id)

    def test_pre_reveal_state_with_injected_final_result_fails_closed(self):
        ready = self.persist_ready()
        zero_wrong = submit_initial(
            ready,
            self.submission("initial-001"),
            self.sealed(),
            expected_version=ready.version,
        ).machine
        revealed_payload = json.loads(self.store.machine_json(zero_wrong))
        ready_payload = json.loads(self.store.machine_json(ready))
        ready_payload["finalResult"] = revealed_payload["finalResult"]
        forged = json.dumps(
            ready_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET machine_json = ?, machine_sha256 = ? "
                "WHERE batch_id = ?",
                (
                    forged,
                    hashlib.sha256(forged.encode("utf-8")).hexdigest(),
                    ready.batch_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load(ready.batch_id, profile_id=self.profile_id)

    def test_seal_commitment_is_null_only_while_preparing_and_then_immutable(self):
        preparing = self.preparing()
        preparing_json = json.loads(self.store.machine_json(preparing))
        self.assertIsNone(preparing_json["sealedQuizSha256"])
        self.store.create(preparing, profile_id=self.profile_id)

        ready = mark_ready(
            preparing,
            sealed_quiz=self.sealed(),
            expected_version=preparing.version,
        )
        ready_json = json.loads(self.store.machine_json(ready))
        self.assertEqual(ready_json["sealedQuizSha256"], ready.sealed_quiz_sha256)
        self.assertRegex(ready.sealed_quiz_sha256 or "", r"^[0-9a-f]{64}$")
        self.store.save_transition(
            ready,
            profile_id=self.profile_id,
            expected_version=preparing.version,
        )

        locked = lock_initial(
            ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            expected_version=ready.version,
        )
        changed_seal = replace(locked, sealed_quiz_sha256="f" * 64)
        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                changed_seal,
                profile_id=self.profile_id,
                expected_version=ready.version,
            )
        self.assertEqual(
            self.store.load(ready.batch_id, profile_id=self.profile_id),
            ready,
        )

    def test_ready_or_later_snapshot_without_a_valid_seal_fails_closed(self):
        preparing = self.preparing()
        ready = mark_ready(
            preparing,
            sealed_quiz=self.sealed(),
            expected_version=preparing.version,
        )
        for invalid in (None, "f" * 63, "F" * 64):
            with self.subTest(invalid=invalid):
                with self.assertRaises(QuizTransitionConflictError):
                    self.store.machine_json(replace(ready, sealed_quiz_sha256=invalid))

    def test_profile_ownership_is_required_and_cannot_change(self):
        preparing = self.preparing()
        self.store.create(preparing, profile_id=self.profile_id)
        with self.assertRaises(QuizOwnershipError):
            self.store.load(preparing.batch_id, profile_id="profile-other-002")

        ready = mark_ready(
            preparing,
            sealed_quiz=self.sealed(),
            expected_version=preparing.version,
        )
        with self.assertRaises(QuizOwnershipError):
            self.store.save_transition(
                ready,
                profile_id="profile-other-002",
                expected_version=preparing.version,
            )
        self.assertEqual(
            self.store.load(preparing.batch_id, profile_id=self.profile_id),
            preparing,
        )

    def test_malformed_stored_profile_index_is_corruption_not_an_owner_mismatch(self):
        preparing = self.preparing()
        self.store.create(preparing, profile_id=self.profile_id)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET profile_id = ? WHERE batch_id = ?",
                ("profile id with spaces", preparing.batch_id),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load(preparing.batch_id, profile_id=self.profile_id)

    def test_delete_profile_cascades_quizzes_and_receipts_and_scrubs_storage(self):
        deleted_profile = "profile-delete-marker-7x9"
        retained_profile = "profile-retained-002"
        deleted_ready = self.persist_ready(
            "batch-delete-001",
            profile_id=deleted_profile,
        )
        deleted_transition = submit_initial(
            deleted_ready,
            self.submission("initial-delete-001", batch_id=deleted_ready.batch_id),
            self.sealed(deleted_ready.batch_id),
            expected_version=deleted_ready.version,
        )
        deleted_events = self.observations_for(
            deleted_transition.machine,
            profile_id=deleted_profile,
        )
        self.store.save_transition(
            deleted_transition.machine,
            profile_id=deleted_profile,
            expected_version=deleted_ready.version,
            receipt=deleted_transition.receipt,
            observation_events=deleted_events,
            observation_session_id=deleted_events[0].session_id,
        )
        retained_ready = self.persist_ready(
            "batch-retained-002",
            profile_id=retained_profile,
        )
        retained_transition = submit_initial(
            retained_ready,
            self.submission("initial-retained-002", batch_id=retained_ready.batch_id),
            self.sealed(retained_ready.batch_id),
            expected_version=retained_ready.version,
        )
        retained_events = self.observations_for(
            retained_transition.machine,
            profile_id=retained_profile,
        )
        self.store.save_transition(
            retained_transition.machine,
            profile_id=retained_profile,
            expected_version=retained_ready.version,
            receipt=retained_transition.receipt,
            observation_events=retained_events,
            observation_session_id=retained_events[0].session_id,
        )
        with ProfileStore(self.database_path) as profiles:
            profiles.append(deleted_events[0])
            profiles.append(retained_events[0])

        marker = deleted_profile.encode("utf-8")
        before_delete = b"".join(
            path.read_bytes()
            for path in (
                self.database_path,
                Path(str(self.database_path) + "-wal"),
            )
            if path.exists()
        )
        self.assertIn(marker, before_delete)
        backup = self.database_path.with_suffix(
            self.database_path.suffix + ".backup-v2"
        )
        backup.write_bytes(marker)

        self.store.delete_profile(deleted_profile)
        self.assertFalse(backup.exists())

        with self.assertRaises(QuizNotFoundError):
            self.store.load(
                deleted_transition.machine.batch_id,
                profile_id=deleted_profile,
            )
        self.assertEqual(
            self.store.load(
                retained_transition.machine.batch_id,
                profile_id=retained_profile,
            ),
            retained_transition.machine,
        )
        with ProfileStore(self.database_path) as profiles:
            self.assertEqual(profiles.load_events(deleted_profile), ())
            self.assertEqual(
                profiles.load_events(retained_profile),
                (retained_events[0],),
            )
        connection = sqlite3.connect(self.database_path)
        try:
            deleted_counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM quiz_machines WHERE profile_id = ?),
                    (SELECT COUNT(*) FROM quiz_transition_receipts WHERE profile_id = ?),
                    (SELECT COUNT(*) FROM quiz_observation_outbox WHERE profile_id = ?)
                """,
                (deleted_profile, deleted_profile, deleted_profile),
            ).fetchone()
            retained_counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM quiz_machines WHERE profile_id = ?),
                    (SELECT COUNT(*) FROM quiz_transition_receipts WHERE profile_id = ?),
                    (SELECT COUNT(*) FROM quiz_observation_outbox WHERE profile_id = ?)
                """,
                (retained_profile, retained_profile, retained_profile),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(deleted_counts, (0, 0, 0))
        self.assertEqual(retained_counts, (1, 1, 3))

        self.store.close()
        for path in (
            self.database_path,
            Path(str(self.database_path) + "-wal"),
        ):
            if path.exists():
                self.assertNotIn(marker, path.read_bytes(), path.name)
        self.store = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )

    def test_pre_outbox_quiz_store_schema_is_rejected_without_mutation(self):
        self.store.close()
        self.database_path.unlink()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """
                CREATE TABLE quiz_store_metadata (
                    singleton INTEGER PRIMARY KEY,
                    schema_version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO quiz_store_metadata VALUES (1, 3)"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(QuizStoreSchemaError):
            QuizStore(self.database_path, timeout_seconds=0.05)

        connection = sqlite3.connect(self.database_path)
        try:
            version = connection.execute(
                "SELECT schema_version FROM quiz_store_metadata WHERE singleton = 1"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, 3)


if __name__ == "__main__":
    unittest.main()
