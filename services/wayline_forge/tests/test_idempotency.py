from __future__ import annotations

import sqlite3
import unittest

from services.wayline_forge.app.quiz_machine import (
    IdempotencyConflictError,
    StaleQuizStateError,
    close_quiz,
    lock_initial,
    mark_ready,
    submit_initial,
    submit_revision,
)
from services.wayline_forge.app.quiz_store import (
    QuizStore,
    QuizStoreBusyError,
    QuizTransitionConflictError,
)
from services.wayline_forge.tests.test_quiz_store import QuizStoreFixture


class QuizStoreIdempotencyTests(QuizStoreFixture, unittest.TestCase):
    def test_duplicate_initial_with_same_request_and_payload_replays_receipt(self):
        ready = self.persist_ready()
        payload = self.submission("initial-001", choices=(1, 0, 0))
        transition = submit_initial(
            ready,
            payload,
            self.sealed(),
            expected_version=ready.version,
        )

        first = self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
        )
        replay = self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
        )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.receipt, transition.receipt)
        self.assertEqual(replay.machine, transition.machine)
        connection = sqlite3.connect(self.database_path)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM quiz_transition_receipts "
                "WHERE batch_id = ? AND action = 'initial'",
                (ready.batch_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1)

    def test_same_initial_request_with_different_payload_conflicts(self):
        ready = self.persist_ready()
        first = submit_initial(
            ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            self.sealed(),
            expected_version=ready.version,
        )
        changed = submit_initial(
            ready,
            self.submission("initial-001", choices=(2, 0, 0)),
            self.sealed(),
            expected_version=ready.version,
        )
        self.store.save_transition(
            first.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=first.receipt,
        )

        with self.assertRaises(IdempotencyConflictError):
            self.store.save_transition(
                changed.machine,
                profile_id=self.profile_id,
                expected_version=ready.version,
                receipt=changed.receipt,
            )
        self.assertEqual(
            self.store.load(ready.batch_id, profile_id=self.profile_id),
            first.machine,
        )

    def test_distinct_second_initial_receipt_conflicts(self):
        ready = self.persist_ready()
        first = submit_initial(
            ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            self.sealed(),
            expected_version=ready.version,
        )
        second = submit_initial(
            ready,
            self.submission("initial-002", choices=(1, 0, 0)),
            self.sealed(),
            expected_version=ready.version,
        )
        self.store.save_transition(
            first.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=first.receipt,
        )

        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                second.machine,
                profile_id=self.profile_id,
                expected_version=ready.version,
                receipt=second.receipt,
            )

    def test_duplicate_revision_replays_original_receipt_after_close(self):
        revision_open = self.persist_revision_open()
        revision = submit_revision(
            revision_open,
            self.submission("revision-001"),
            self.sealed(),
            expected_version=revision_open.version,
        )
        self.store.save_transition(
            revision.machine,
            profile_id=self.profile_id,
            expected_version=revision_open.version,
            receipt=revision.receipt,
            observation_events=self.observations_for(revision.machine),
            observation_session_id="session-outbox-001",
        )
        closed = close_quiz(
            revision.machine,
            expected_version=revision.machine.version,
        )
        self.store.save_transition(
            closed,
            profile_id=self.profile_id,
            expected_version=revision.machine.version,
        )

        replay = self.store.save_transition(
            revision.machine,
            profile_id=self.profile_id,
            expected_version=revision_open.version,
            receipt=revision.receipt,
            observation_events=self.observations_for(revision.machine),
            observation_session_id="session-outbox-001",
        )

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.receipt, revision.receipt)
        self.assertEqual(replay.machine, closed)
        connection = sqlite3.connect(self.database_path)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM quiz_transition_receipts "
                "WHERE batch_id = ? AND action = 'revision'",
                (closed.batch_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1)

    def test_changed_duplicate_revision_payload_conflicts(self):
        revision_open = self.persist_revision_open()
        first = submit_revision(
            revision_open,
            self.submission("revision-001"),
            self.sealed(),
            expected_version=revision_open.version,
        )
        changed = submit_revision(
            revision_open,
            self.submission("revision-001", choices=(0, 1, 0)),
            self.sealed(),
            expected_version=revision_open.version,
        )
        self.store.save_transition(
            first.machine,
            profile_id=self.profile_id,
            expected_version=revision_open.version,
            receipt=first.receipt,
            observation_events=self.observations_for(first.machine),
            observation_session_id="session-outbox-001",
        )

        with self.assertRaises(IdempotencyConflictError):
            self.store.save_transition(
                changed.machine,
                profile_id=self.profile_id,
                expected_version=revision_open.version,
                receipt=changed.receipt,
                observation_events=self.observations_for(changed.machine),
                observation_session_id="session-outbox-001",
            )

    def test_stale_competing_write_fails_without_changing_the_winner(self):
        preparing = self.preparing()
        self.store.create(preparing, profile_id=self.profile_id)
        competitor = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )
        try:
            first_snapshot = self.store.load(
                preparing.batch_id,
                profile_id=self.profile_id,
            )
            second_snapshot = competitor.load(
                preparing.batch_id,
                profile_id=self.profile_id,
            )
            winner = mark_ready(
                first_snapshot,
                sealed_quiz=self.sealed(),
                expected_version=first_snapshot.version,
            )
            competitor_candidate = mark_ready(
                second_snapshot,
                sealed_quiz=self.sealed(),
                expected_version=second_snapshot.version,
            )
            self.store.save_transition(
                winner,
                profile_id=self.profile_id,
                expected_version=first_snapshot.version,
            )

            with self.assertRaises(StaleQuizStateError):
                competitor.save_transition(
                    competitor_candidate,
                    profile_id=self.profile_id,
                    expected_version=second_snapshot.version,
                )
        finally:
            competitor.close()

        self.assertEqual(
            self.store.load(preparing.batch_id, profile_id=self.profile_id),
            winner,
        )

    def test_row_version_must_advance_and_match_expected_version(self):
        preparing = self.preparing()
        self.store.create(preparing, profile_id=self.profile_id)
        ready = mark_ready(
            preparing,
            sealed_quiz=self.sealed(),
            expected_version=preparing.version,
        )

        with self.assertRaises(StaleQuizStateError):
            self.store.save_transition(
                ready,
                profile_id=self.profile_id,
                expected_version=7,
            )
        with self.assertRaises(StaleQuizStateError):
            self.store.save_transition(
                preparing,
                profile_id=self.profile_id,
                expected_version=preparing.version,
            )
        self.assertEqual(
            self.store.load(preparing.batch_id, profile_id=self.profile_id),
            preparing,
        )

    def test_failpoint_rolls_back_receipt_and_machine_together(self):
        ready = self.persist_ready()
        initial = submit_initial(
            ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            self.sealed(),
            expected_version=ready.version,
        )

        self.store._failpoint_stage = "after_receipt_insert"
        with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
            self.store.save_transition(
                initial.machine,
                profile_id=self.profile_id,
                expected_version=ready.version,
                receipt=initial.receipt,
            )
        self.store._failpoint_stage = None

        self.assertEqual(
            self.restart().load(ready.batch_id, profile_id=self.profile_id),
            ready,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM quiz_transition_receipts WHERE batch_id = ?",
                (ready.batch_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_failure_after_machine_update_still_rolls_back_both_rows(self):
        ready = self.persist_ready()
        initial = submit_initial(
            ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            self.sealed(),
            expected_version=ready.version,
        )

        self.store._failpoint_stage = "after_machine_update"
        with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
            self.store.save_transition(
                initial.machine,
                profile_id=self.profile_id,
                expected_version=ready.version,
                receipt=initial.receipt,
            )
        self.store._failpoint_stage = None

        self.assertEqual(
            self.restart().load(ready.batch_id, profile_id=self.profile_id),
            ready,
        )

    def test_database_write_lock_fails_closed_without_partial_transition(self):
        preparing = self.preparing()
        self.store.create(preparing, profile_id=self.profile_id)
        ready = mark_ready(
            preparing,
            sealed_quiz=self.sealed(),
            expected_version=preparing.version,
        )
        locker = sqlite3.connect(self.database_path, isolation_level=None, timeout=0)
        try:
            locker.execute("BEGIN IMMEDIATE")
            with self.assertRaises(QuizStoreBusyError):
                self.store.save_transition(
                    ready,
                    profile_id=self.profile_id,
                    expected_version=preparing.version,
                )
        finally:
            locker.rollback()
            locker.close()

        self.assertEqual(
            self.store.load(preparing.batch_id, profile_id=self.profile_id),
            preparing,
        )

    def test_locked_initial_can_be_saved_without_a_receipt_then_resolved_atomically(self):
        ready = self.persist_ready()
        locked = lock_initial(
            ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            expected_version=ready.version,
        )
        stored = self.store.save_transition(
            locked,
            profile_id=self.profile_id,
            expected_version=ready.version,
        )

        self.assertIsNone(stored.receipt)
        self.assertEqual(
            self.restart().load(ready.batch_id, profile_id=self.profile_id),
            locked,
        )

    def test_identical_locked_snapshot_replays_without_a_receipt(self):
        ready = self.persist_ready()
        locked = lock_initial(
            ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            expected_version=ready.version,
        )

        first = self.store.save_transition(
            locked,
            profile_id=self.profile_id,
            expected_version=ready.version,
        )
        replay = self.store.save_transition(
            locked,
            profile_id=self.profile_id,
            expected_version=ready.version,
        )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertIsNone(replay.receipt)
        self.assertEqual(replay.machine, locked)

    def test_changed_locked_snapshot_conflicts_instead_of_replaying(self):
        ready = self.persist_ready()
        locked = lock_initial(
            ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            expected_version=ready.version,
        )
        changed = lock_initial(
            ready,
            self.submission("initial-002", choices=(2, 0, 0)),
            expected_version=ready.version,
        )
        self.store.save_transition(
            locked,
            profile_id=self.profile_id,
            expected_version=ready.version,
        )

        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                changed,
                profile_id=self.profile_id,
                expected_version=ready.version,
            )
        self.assertEqual(
            self.store.load(ready.batch_id, profile_id=self.profile_id),
            locked,
        )

    def test_concurrent_identical_locked_write_returns_one_replay(self):
        ready = self.persist_ready()
        competitor = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )
        try:
            first_ready = self.store.load(
                ready.batch_id,
                profile_id=self.profile_id,
            )
            second_ready = competitor.load(
                ready.batch_id,
                profile_id=self.profile_id,
            )
            payload = self.submission("initial-001", choices=(1, 0, 0))
            first_locked = lock_initial(
                first_ready,
                payload,
                expected_version=first_ready.version,
            )
            second_locked = lock_initial(
                second_ready,
                payload,
                expected_version=second_ready.version,
            )

            winner = self.store.save_transition(
                first_locked,
                profile_id=self.profile_id,
                expected_version=first_ready.version,
            )
            replay = competitor.save_transition(
                second_locked,
                profile_id=self.profile_id,
                expected_version=second_ready.version,
            )
        finally:
            competitor.close()

        self.assertFalse(winner.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.machine, first_locked)
        connection = sqlite3.connect(self.database_path)
        try:
            receipt_count = connection.execute(
                "SELECT COUNT(*) FROM quiz_transition_receipts WHERE batch_id = ?",
                (ready.batch_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(receipt_count, 0)


if __name__ == "__main__":
    unittest.main()
