from __future__ import annotations

from dataclasses import replace
import sqlite3
import tempfile
import threading
from pathlib import Path
import unittest

from services.wayline_forge.app.profile_store import (
    EventLogCorruptionError,
    EventOrderError,
    IdempotencyConflictError,
    ProfileStore,
)
from services.wayline_forge.tests.fixtures import event


class _InterruptedProjectionStore(ProfileStore):
    def _write_projection(self, profile_id, state):
        raise RuntimeError("simulated interruption after durable event append")


class _PreFixRaceStore(ProfileStore):
    """Synchronize the predecessor read used by the old append implementation."""

    barrier: threading.Barrier

    def load_events(self, profile_id):
        loaded = super().load_events(profile_id)
        self.barrier.wait(timeout=5)
        return loaded


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "profiles.sqlite"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_append_only_log_rebuilds_same_projection(self):
        events = (
            event.wrong("align_by_ends", ordinal=1, template="a"),
            event.wrong("align_by_ends", ordinal=2, template="b"),
        )
        with ProfileStore(self.path) as store:
            for item in events:
                store.append(item)
            before = store.projection_bytes("profile-1")
            store.rebuild_projection("profile-1")
            after = store.projection_bytes("profile-1")

        self.assertEqual(before, after)

    def test_repeating_identical_idempotency_event_does_not_append_twice(self):
        observation = event.correct()
        with ProfileStore(self.path) as store:
            store.append(observation)
            store.append(observation)

            self.assertEqual(len(store.load_events("profile-1")), 1)

    def test_reusing_idempotency_id_for_different_payload_is_rejected(self):
        original = event.correct()
        conflicting = replace(original, final_confidence="certain")
        with ProfileStore(self.path) as store:
            store.append(original)

            with self.assertRaises(IdempotencyConflictError):
                store.append(conflicting)

    def test_new_event_ordinal_must_be_exactly_latest_plus_one(self):
        first = event.correct(ordinal=1)
        skipped = event.correct(ordinal=3, question="skipped-question")
        with ProfileStore(self.path) as store:
            store.append(first)

            with self.assertRaises(EventOrderError):
                store.append(skipped)
            self.assertEqual(store.load_events("profile-1"), (first,))

    def test_two_connections_cannot_commit_events_from_the_same_predecessor(self):
        with ProfileStore(self.path):
            pass
        barrier = threading.Barrier(2)
        _PreFixRaceStore.barrier = barrier
        outcomes: list[BaseException | None] = []
        outcomes_lock = threading.Lock()

        def append_in_thread(observation):
            try:
                with _PreFixRaceStore(self.path) as store:
                    store.append(observation)
            except BaseException as exc:  # captured for deterministic thread joining
                with outcomes_lock:
                    outcomes.append(exc)
            else:
                with outcomes_lock:
                    outcomes.append(None)

        first = event.correct(ordinal=1, question="concurrent-first")
        second = event.correct(ordinal=2, question="concurrent-second")
        threads = (
            threading.Thread(target=append_in_thread, args=(first,)),
            threading.Thread(target=append_in_thread, args=(second,)),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive(), "concurrent append deadlocked")

        with ProfileStore(self.path) as store:
            durable = store.load_events("profile-1")
        self.assertTrue(durable)
        self.assertEqual(durable[0], first)
        self.assertIn(len(durable), (1, 2))
        if len(durable) == 2:
            self.assertEqual(durable, (first, second))
        self.assertEqual(len(outcomes), 2)

    def test_regenerated_observation_identity_cannot_duplicate_batch_item(self):
        from services.wayline_forge.app.profile_store import SemanticEventConflictError

        original = event.correct(ordinal=1, batch="same-batch", question="same-question")
        regenerated = replace(
            original,
            ordinal=2,
            event_id="regenerated-event-id",
            idempotency_id="regenerated-idempotency-id",
        )
        with ProfileStore(self.path) as store:
            store.append(original)

            with self.assertRaises(SemanticEventConflictError):
                store.append(regenerated)
            self.assertEqual(len(store.load_events("profile-1")), 1)

    def test_hash_chain_detects_local_event_tampering(self):
        with ProfileStore(self.path) as store:
            store.append(event.correct())

        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE event_log SET canonical_json = ? WHERE profile_id = ?",
            ('{"tampered":true}', "profile-1"),
        )
        connection.commit()
        connection.close()

        with ProfileStore(self.path) as store:
            with self.assertRaises(EventLogCorruptionError):
                store.load_state("profile-1")

    def test_log_index_event_and_idempotency_ids_must_match_canonical_event(self):
        for column, replacement in (
            ("event_id", "tampered-event-id"),
            ("idempotency_id", "tampered-idempotency-id"),
        ):
            with self.subTest(column=column):
                path = self.path.with_name(f"{column}.sqlite")
                with ProfileStore(path) as store:
                    store.append(event.correct())
                connection = sqlite3.connect(path)
                connection.execute(
                    f"UPDATE event_log SET {column} = ? WHERE profile_id = ?",
                    (replacement, "profile-1"),
                )
                connection.commit()
                connection.close()

                with ProfileStore(path) as store:
                    with self.assertRaises(EventLogCorruptionError):
                        store.load_events("profile-1")

    def test_restart_recovers_when_interrupted_after_log_append_before_projection(self):
        interrupted = _InterruptedProjectionStore(self.path)
        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            interrupted.append(event.wrong("align_by_ends", confidence="certain"))
        interrupted.close()

        with ProfileStore(self.path) as recovered:
            state = recovered.load_state("profile-1")
            self.assertEqual(state.procedure("align_by_ends").status, "suspected")
            self.assertEqual(len(recovered.load_events("profile-1")), 1)

    def test_profile_delete_removes_log_and_projection(self):
        with ProfileStore(self.path) as store:
            store.append(event.correct())
            store.delete_profile("profile-1")

            self.assertEqual(store.load_events("profile-1"), ())
            self.assertEqual(store.load_state("profile-1").answer_records, ())

    def test_profile_delete_scrubs_database_wal_and_migration_backups(self):
        secret = "unique-deleted-learner-secret-93f4b8"
        observation = replace(
            event.correct(),
            canonical_feedback=(secret,),
        )
        with ProfileStore(self.path) as store:
            self.assertEqual(store._connection.execute("PRAGMA secure_delete").fetchone()[0], 1)
            store.append(observation)
            backup = self.path.with_suffix(self.path.suffix + ".backup-v1")
            backup.write_bytes(secret.encode("utf-8"))
            store.delete_profile("profile-1")

        self.assertFalse(backup.exists())
        for candidate in (
            self.path,
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
        ):
            if candidate.exists():
                self.assertNotIn(secret.encode("utf-8"), candidate.read_bytes())

    def test_successful_migration_removes_temporary_backup(self):
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker VALUES ('pre-migration')")
        connection.commit()
        connection.close()

        backup = self.path.with_suffix(self.path.suffix + ".backup-v0")
        with ProfileStore(self.path):
            pass

        self.assertFalse(backup.exists())


if __name__ == "__main__":
    unittest.main()
