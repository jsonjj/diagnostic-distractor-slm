from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import sqlite3
import unittest

from services.wayline_forge.app.profile_store import (
    OutboxReservationError,
    ProfileStore,
)
from services.wayline_forge.app.quiz_machine import (
    close_quiz,
    mark_ready,
    submit_initial,
    submit_revision,
)
from services.wayline_forge.app.quiz_store import (
    QuizNotFoundError,
    QuizStore,
    QuizStoreCorruptionError,
    QuizStoreError,
    QuizTransitionConflictError,
)
from services.wayline_forge.tests.test_quiz_store import QuizStoreFixture
from services.wayline_forge.tests.fixtures import event


class LearningOutboxTests(QuizStoreFixture, unittest.TestCase):
    def zero_wrong_reveal(
        self,
        *,
        batch_id: str = "batch-001",
        profile_id: str | None = None,
        start_ordinal: int = 1,
    ):
        owner = profile_id or self.profile_id
        ready = self.persist_ready(batch_id, profile_id=owner)
        transition = submit_initial(
            ready,
            self.submission("initial-001", batch_id=batch_id),
            self.sealed(batch_id),
            expected_version=ready.version,
        )
        events = self.observations_for(
            transition.machine,
            profile_id=owner,
            start_ordinal=start_ordinal,
        )
        return ready, transition, events

    def revision_reveal(
        self,
        *,
        batch_id: str = "batch-001",
        profile_id: str | None = None,
        start_ordinal: int = 1,
    ):
        owner = profile_id or self.profile_id
        revision_open = self.persist_revision_open(batch_id, profile_id=owner)
        transition = submit_revision(
            revision_open,
            self.submission("revision-001", batch_id=batch_id),
            self.sealed(batch_id),
            expected_version=revision_open.version,
        )
        events = self.observations_for(
            transition.machine,
            profile_id=owner,
            start_ordinal=start_ordinal,
        )
        return revision_open, transition, events

    def test_reveal_records_the_explicit_actual_session_after_resume(self):
        ready, transition, events = self.zero_wrong_reveal()
        reveal_session_id = "session-resumed-outbox-002"
        resumed_events = tuple(
            replace(observation, session_id=reveal_session_id)
            for observation in events
        )

        stored = self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
            observation_events=resumed_events,
            observation_session_id=reveal_session_id,
        )

        self.assertFalse(stored.replayed)
        self.assertEqual(
            self.store.pending_observations(self.profile_id),
            resumed_events,
        )

    def test_reveal_requires_one_valid_explicit_session_matching_every_event(self):
        ready, transition, events = self.zero_wrong_reveal()

        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                transition.machine,
                profile_id=self.profile_id,
                expected_version=ready.version,
                receipt=transition.receipt,
                observation_events=events,
            )

        for observation_session_id in (
            "session-other-002",
            "malformed session id",
            True,
        ):
            with self.subTest(observation_session_id=observation_session_id):
                with self.assertRaises(QuizTransitionConflictError):
                    self.store.save_transition(
                        transition.machine,
                        profile_id=self.profile_id,
                        expected_version=ready.version,
                        receipt=transition.receipt,
                        observation_events=events,
                        observation_session_id=observation_session_id,  # type: ignore[arg-type]
                    )

        self.assertEqual(
            self.store.load(ready.batch_id, profile_id=self.profile_id),
            ready,
        )

    def test_transition_without_observations_rejects_a_stray_session(self):
        batch_id = "batch-no-observation-session-001"
        preparing = self.preparing(batch_id)
        self.store.create(preparing, profile_id=self.profile_id)
        ready = mark_ready(
            preparing,
            sealed_quiz=self.sealed(batch_id),
            expected_version=preparing.version,
        )

        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                ready,
                profile_id=self.profile_id,
                expected_version=preparing.version,
                observation_session_id="session-smuggled-001",
            )

        self.assertEqual(
            self.store.load(batch_id, profile_id=self.profile_id),
            preparing,
        )

    def test_first_reveal_requires_exactly_one_matching_observation_per_item(self):
        ready, transition, events = self.zero_wrong_reveal()

        invalid_payloads = (
            (),
            events[:-1],
            (*events, events[0]),
            (
                replace(events[0], profile_id="profile-other-002"),
                *events[1:],
            ),
            (
                replace(events[0], batch_id="batch-other-002"),
                *events[1:],
            ),
            (
                replace(events[0], first_confidence="leaning"),
                *events[1:],
            ),
            (
                replace(events[0], batch_wrong_count=1),
                *events[1:],
            ),
            (
                replace(events[0], first_correct=1),  # type: ignore[arg-type]
                *events[1:],
            ),
        )
        for observation_events in invalid_payloads:
            with self.subTest(observation_events=observation_events):
                with self.assertRaises(QuizTransitionConflictError):
                    self.store.save_transition(
                        transition.machine,
                        profile_id=self.profile_id,
                        expected_version=ready.version,
                        receipt=transition.receipt,
                        observation_events=observation_events,
                        observation_session_id=events[0].session_id,
                    )
                self.assertEqual(
                    self.store.load(ready.batch_id, profile_id=self.profile_id),
                    ready,
                )

        persisted = self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        self.assertFalse(persisted.replayed)

    def test_non_reveal_transition_forbids_observation_events(self):
        source_ready, reveal, events = self.zero_wrong_reveal(
            batch_id="batch-source-002"
        )
        self.store.save_transition(
            reveal.machine,
            profile_id=self.profile_id,
            expected_version=source_ready.version,
            receipt=reveal.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        closed_source = close_quiz(
            reveal.machine,
            expected_version=reveal.machine.version,
        )
        self.store.save_transition(
            closed_source,
            profile_id=self.profile_id,
            expected_version=reveal.machine.version,
        )

        batch_id = "batch-nonreveal-001"
        preparing = self.preparing(batch_id)
        self.store.create(preparing, profile_id=self.profile_id)
        ready = mark_ready(
            preparing,
            sealed_quiz=self.sealed(batch_id),
            expected_version=preparing.version,
        )

        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                ready,
                profile_id=self.profile_id,
                expected_version=preparing.version,
                observation_events=events,
                observation_session_id=events[0].session_id,
            )
        self.assertEqual(
            self.store.load(batch_id, profile_id=self.profile_id),
            preparing,
        )
        self.assertIsNotNone(reveal.machine.final_result)

    def test_outbox_and_reveal_roll_back_together_before_commit(self):
        ready, transition, events = self.zero_wrong_reveal()
        self.store._failpoint_stage = "after_outbox_insert"

        with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
            self.store.save_transition(
                transition.machine,
                profile_id=self.profile_id,
                expected_version=ready.version,
                receipt=transition.receipt,
                observation_events=events,
                observation_session_id=events[0].session_id,
            )
        self.store._failpoint_stage = None

        self.assertEqual(
            self.store.load(ready.batch_id, profile_id=self.profile_id),
            ready,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM quiz_transition_receipts WHERE batch_id = ?),
                    (SELECT COUNT(*) FROM quiz_observation_outbox WHERE batch_id = ?)
                """,
                (ready.batch_id, ready.batch_id),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(counts, (0, 0))

    def test_crash_after_reveal_commit_recovers_pending_events_after_restart(self):
        ready, transition, events = self.zero_wrong_reveal()
        self.store._failpoint_stage = "after_reveal_commit"

        with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
            self.store.save_transition(
                transition.machine,
                profile_id=self.profile_id,
                expected_version=ready.version,
                receipt=transition.receipt,
                observation_events=events,
                observation_session_id=events[0].session_id,
            )

        self.store.close()
        self.store = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )
        self.assertEqual(
            self.store.load(ready.batch_id, profile_id=self.profile_id),
            transition.machine,
        )
        self.assertEqual(self.store.pending_observations(self.profile_id), events)

        with ProfileStore(self.database_path) as profiles:
            delivered = self.store.drain_observations(
                self.profile_id,
                profile_store=profiles,
            )
            self.assertEqual(delivered, len(events))
            self.assertEqual(profiles.load_events(self.profile_id), events)
        self.assertEqual(self.store.pending_observations(self.profile_id), ())

    def test_exact_reveal_replay_requires_the_same_outbox_payload(self):
        revision_open, transition, events = self.revision_reveal()
        first = self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=revision_open.version,
            receipt=transition.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        self.assertFalse(first.replayed)

        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                transition.machine,
                profile_id=self.profile_id,
                expected_version=revision_open.version,
                receipt=transition.receipt,
                observation_events=(),
            )
        changed = (
            replace(events[0], optional_wording_shown="Changed wording."),
            *events[1:],
        )
        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                transition.machine,
                profile_id=self.profile_id,
                expected_version=revision_open.version,
                receipt=transition.receipt,
                observation_events=changed,
                observation_session_id=events[0].session_id,
            )

        changed_session_id = "session-replay-changed-002"
        changed_session_events = tuple(
            replace(observation, session_id=changed_session_id)
            for observation in events
        )
        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                transition.machine,
                profile_id=self.profile_id,
                expected_version=revision_open.version,
                receipt=transition.receipt,
                observation_events=changed_session_events,
                observation_session_id=changed_session_id,
            )

        replay = self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=revision_open.version,
            receipt=transition.receipt,
            observation_events=tuple(reversed(events)),
            observation_session_id=events[0].session_id,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.machine, transition.machine)

    def test_earlier_non_reveal_receipt_replays_after_later_revision_outbox(self):
        revision_open, revision, events = self.revision_reveal()
        self.store.save_transition(
            revision.machine,
            profile_id=self.profile_id,
            expected_version=revision_open.version,
            receipt=revision.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        initial_receipt = revision_open.initial_receipt
        self.assertIsNotNone(initial_receipt)

        replay = self.store.save_transition(
            revision_open,
            profile_id=self.profile_id,
            expected_version=1,
            receipt=initial_receipt,
            observation_events=(),
        )

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.receipt, initial_receipt)
        self.assertEqual(replay.machine, revision.machine)

    def test_pending_observations_detects_corrupt_canonical_rows(self):
        ready, transition, events = self.zero_wrong_reveal()
        self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_observation_outbox SET event_sha256 = ? "
                "WHERE profile_id = ? AND item_id = ?",
                ("f" * 64, self.profile_id, events[0].item_id),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(QuizStoreCorruptionError):
            self.store.pending_observations(self.profile_id)

    def test_pending_outbox_blocks_unrelated_event_until_drain_completes(self):
        ready, transition, events = self.zero_wrong_reveal()
        self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        unrelated = event.activate(
            ordinal=1,
            profile=self.profile_id,
            session="session-unrelated-001",
        )

        with ProfileStore(self.database_path) as profiles:
            with self.assertRaises(OutboxReservationError):
                profiles.append(unrelated)
            self.assertEqual(profiles.load_events(self.profile_id), ())

            self.assertEqual(
                self.store.drain_observations(
                    self.profile_id,
                    profile_store=profiles,
                ),
                len(events),
            )
            next_event = event.activate(
                ordinal=4,
                world="decimara",
                profile=self.profile_id,
                session="session-after-drain-002",
            )
            profiles.append(next_event)
            self.assertEqual(
                profiles.load_events(self.profile_id),
                (*events, next_event),
            )

    def test_exact_replay_of_older_durable_event_is_allowed_with_newer_reservation(self):
        ready, transition, events = self.zero_wrong_reveal()
        self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        self.store._failpoint_stage = f"after_delivery_mark:{events[0].item_id}"
        with ProfileStore(self.database_path) as profiles:
            with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
                self.store.drain_observations(
                    self.profile_id,
                    profile_store=profiles,
                )
            self.assertEqual(
                self.store.pending_observations(self.profile_id),
                events[1:],
            )

            profiles.append(events[0])
            self.assertEqual(profiles.load_events(self.profile_id), (events[0],))

            self.store._failpoint_stage = None
            self.store.drain_observations(
                self.profile_id,
                profile_store=profiles,
            )
            self.assertEqual(profiles.load_events(self.profile_id), events)

    def test_multiple_queued_batches_reserve_nonoverlapping_contiguous_ordinals(self):
        first_ready, first, first_events = self.zero_wrong_reveal(
            batch_id="batch-queued-001",
        )
        self.store.save_transition(
            first.machine,
            profile_id=self.profile_id,
            expected_version=first_ready.version,
            receipt=first.receipt,
            observation_events=first_events,
            observation_session_id=first_events[0].session_id,
        )
        closed_first = close_quiz(
            first.machine,
            expected_version=first.machine.version,
        )
        self.store.save_transition(
            closed_first,
            profile_id=self.profile_id,
            expected_version=first.machine.version,
        )
        self.assertEqual(self.store.next_profile_ordinal(self.profile_id), 4)

        second_ready, second, overlapping = self.zero_wrong_reveal(
            batch_id="batch-queued-002",
        )
        with self.assertRaises(QuizTransitionConflictError):
            self.store.save_transition(
                second.machine,
                profile_id=self.profile_id,
                expected_version=second_ready.version,
                receipt=second.receipt,
                observation_events=overlapping,
                observation_session_id=overlapping[0].session_id,
            )
        second_events = self.observations_for(
            second.machine,
            profile_id=self.profile_id,
            start_ordinal=4,
        )
        self.store.save_transition(
            second.machine,
            profile_id=self.profile_id,
            expected_version=second_ready.version,
            receipt=second.receipt,
            observation_events=second_events,
            observation_session_id=second_events[0].session_id,
        )

        self.assertEqual(self.store.next_profile_ordinal(self.profile_id), 7)
        self.assertEqual(
            self.store.pending_observations(self.profile_id),
            (*first_events, *second_events),
        )

    def test_stored_receipt_hash_cryptographically_includes_outbox_commitment(self):
        ready, transition, events = self.zero_wrong_reveal()
        self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            receipt_json, receipt_sha256, outbox_sha256 = connection.execute(
                """
                SELECT receipt_json, receipt_sha256, outbox_sha256
                FROM quiz_transition_receipts
                WHERE batch_id = ? AND action = 'initial'
                """,
                (transition.machine.batch_id,),
            ).fetchone()
            stored = json.loads(receipt_json)
            self.assertEqual(stored["outboxSha256"], outbox_sha256)
            self.assertEqual(
                receipt_sha256,
                hashlib.sha256(receipt_json.encode("utf-8")).hexdigest(),
            )
            replacement = "f" * 64
            self.assertNotEqual(replacement, outbox_sha256)
            connection.execute(
                """
                UPDATE quiz_transition_receipts SET outbox_sha256 = ?
                WHERE batch_id = ? AND action = 'initial'
                """,
                (replacement, transition.machine.batch_id),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load(transition.machine.batch_id, profile_id=self.profile_id)

    def test_delivered_marker_requires_matching_canonical_event_log_row(self):
        ready, transition, events = self.zero_wrong_reveal()
        self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """
                UPDATE quiz_observation_outbox SET delivered = 1
                WHERE profile_id = ? AND batch_id = ?
                """,
                (self.profile_id, transition.machine.batch_id),
            )
            connection.commit()
        finally:
            connection.close()

        with ProfileStore(self.database_path) as profiles:
            with self.assertRaises(OutboxReservationError):
                profiles.append(event.activate(
                    ordinal=1,
                    profile=self.profile_id,
                    session="session-hidden-reservation",
                ))
        with self.assertRaises(QuizStoreCorruptionError):
            self.store.load(transition.machine.batch_id, profile_id=self.profile_id)

    def test_drain_never_calls_profile_store_while_quiz_transaction_is_open(self):
        ready, transition, events = self.zero_wrong_reveal()
        self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        quiz_store = self.store

        class TransactionCheckingProfileStore(ProfileStore):
            def append(self, observation):
                if quiz_store._connection.in_transaction:
                    raise AssertionError("quiz transaction remained open during append")
                return super().append(observation)

        with TransactionCheckingProfileStore(self.database_path) as profiles:
            self.assertEqual(
                self.store.drain_observations(
                    self.profile_id,
                    profile_store=profiles,
                ),
                len(events),
            )

    def test_drain_requires_the_same_database_path(self):
        ready, transition, events = self.zero_wrong_reveal()
        self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )
        other_path = self.database_path.with_name("other-profile.sqlite3")
        with ProfileStore(other_path) as profiles:
            with self.assertRaises(QuizStoreError):
                self.store.drain_observations(
                    self.profile_id,
                    profile_store=profiles,
                )
        self.assertEqual(self.store.pending_observations(self.profile_id), events)

    def test_crash_after_each_partial_append_recovers_exactly_once(self):
        for fail_offset in range(3):
            with self.subTest(fail_offset=fail_offset):
                path = self.database_path.with_name(f"partial-{fail_offset}.sqlite3")
                batch_id = f"batch-partial-{fail_offset}"
                profile_id = f"profile-partial-{fail_offset}"
                store = QuizStore(
                    path,
                    timeout_seconds=0.05,
                    allow_unverified_test_material=True,
                )
                preparing = self.preparing(batch_id)
                store.create(preparing, profile_id=profile_id)
                ready = mark_ready(
                    preparing,
                    sealed_quiz=self.sealed(batch_id),
                    expected_version=preparing.version,
                )
                store.save_transition(
                    ready,
                    profile_id=profile_id,
                    expected_version=preparing.version,
                )
                transition = submit_initial(
                    ready,
                    self.submission("initial-001", batch_id=batch_id),
                    self.sealed(batch_id),
                    expected_version=ready.version,
                )
                events = self.observations_for(
                    transition.machine,
                    profile_id=profile_id,
                )
                store.save_transition(
                    transition.machine,
                    profile_id=profile_id,
                    expected_version=ready.version,
                    receipt=transition.receipt,
                    observation_events=events,
                    observation_session_id=events[0].session_id,
                )
                profiles = ProfileStore(path)
                store._failpoint_stage = (
                    f"after_profile_append:{events[fail_offset].item_id}"
                )
                with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
                    store.drain_observations(profile_id, profile_store=profiles)
                store.close()
                profiles.close()

                store = QuizStore(
                    path,
                    timeout_seconds=0.05,
                    allow_unverified_test_material=True,
                )
                profiles = ProfileStore(path)
                try:
                    delivered = store.drain_observations(
                        profile_id,
                        profile_store=profiles,
                    )
                    self.assertGreaterEqual(delivered, 1)
                    self.assertEqual(profiles.load_events(profile_id), events)
                    self.assertEqual(store.pending_observations(profile_id), ())
                    self.assertEqual(len(profiles.load_events(profile_id)), len(events))
                finally:
                    store.close()
                    profiles.close()

    def test_profile_store_interruption_after_durable_append_recovers_once(self):
        ready, transition, events = self.zero_wrong_reveal()
        self.store.save_transition(
            transition.machine,
            profile_id=self.profile_id,
            expected_version=ready.version,
            receipt=transition.receipt,
            observation_events=events,
            observation_session_id=events[0].session_id,
        )

        class InterruptOnceProfileStore(ProfileStore):
            interrupted = False

            def _write_projection(self, profile_id, state):
                if not self.interrupted:
                    self.interrupted = True
                    raise RuntimeError("crash after durable profile append")
                return super()._write_projection(profile_id, state)

        interrupted = InterruptOnceProfileStore(self.database_path)
        with self.assertRaisesRegex(RuntimeError, "crash after durable profile append"):
            self.store.drain_observations(
                self.profile_id,
                profile_store=interrupted,
            )
        interrupted.close()

        with ProfileStore(self.database_path) as recovered:
            self.assertEqual(
                self.store.drain_observations(
                    self.profile_id,
                    profile_store=recovered,
                ),
                len(events),
            )
            self.assertEqual(recovered.load_events(self.profile_id), events)
        self.assertEqual(self.store.pending_observations(self.profile_id), ())

    def test_profile_store_delete_removes_coexisting_quiz_outbox_and_receipts_only_for_owner(self):
        first_profile = "profile-delete-outbox-001"
        second_profile = "profile-keep-outbox-002"
        first_ready, first_reveal, first_events = self.zero_wrong_reveal(
            batch_id="batch-delete-outbox-001",
            profile_id=first_profile,
        )
        self.store.save_transition(
            first_reveal.machine,
            profile_id=first_profile,
            expected_version=first_ready.version,
            receipt=first_reveal.receipt,
            observation_events=first_events,
            observation_session_id=first_events[0].session_id,
        )
        second_ready, second_reveal, second_events = self.zero_wrong_reveal(
            batch_id="batch-keep-outbox-002",
            profile_id=second_profile,
        )
        self.store.save_transition(
            second_reveal.machine,
            profile_id=second_profile,
            expected_version=second_ready.version,
            receipt=second_reveal.receipt,
            observation_events=second_events,
            observation_session_id=second_events[0].session_id,
        )

        with ProfileStore(self.database_path) as profiles:
            profiles.append(first_events[0])
            profiles.append(second_events[0])
            profiles.delete_profile(first_profile)

        with self.assertRaises(QuizNotFoundError):
            self.store.load(first_reveal.machine.batch_id, profile_id=first_profile)
        self.assertEqual(self.store.pending_observations(first_profile), ())
        self.assertEqual(
            self.store.load(
                second_reveal.machine.batch_id,
                profile_id=second_profile,
            ),
            second_reveal.machine,
        )
        self.assertEqual(
            self.store.pending_observations(second_profile),
            second_events,
        )
        with ProfileStore(self.database_path) as profiles:
            self.assertEqual(profiles.load_events(first_profile), ())
            self.assertEqual(profiles.load_events(second_profile), (second_events[0],))


if __name__ == "__main__":
    unittest.main()
