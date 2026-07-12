from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from services.wayline_forge.app import quiz_store
from services.wayline_forge.app.batch_material import VerifiedBatchMaterial
from services.wayline_forge.app.contracts import BattleQuizRequest
from services.wayline_forge.app.quiz_machine import (
    IdempotencyConflictError,
    QuizSelection,
    QuizState,
    QuizSubmission,
    lock_initial,
    new_quiz,
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


class PreparedBatchCreationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
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
        self.request = BattleQuizRequest(
            schemaVersion="wayline.v1",
            requestId="prepare-request-001",
            sessionId=self.material.context.session_id,
            battleId=self.material.context.battle_id,
            worldId=self.material.context.world_id,
            battleTier=self.material.context.battle_tier,
        )
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self._temporary_directory.name) / "wayline.sqlite3"
        self.store = self._open_store()

    def tearDown(self) -> None:
        self.store.close()
        self._temporary_directory.cleanup()

    def _open_store(self) -> quiz_store.QuizStore:
        return quiz_store.QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            compiler=self.material_fixture.verifier.compiler,
            manifest=self.material_fixture.verifier.manifest,
        )

    def restart(self) -> quiz_store.QuizStore:
        self.store.close()
        self.store = self._open_store()
        return self.store

    def request_with(self, **overrides: object) -> BattleQuizRequest:
        values: dict[str, object] = {
            "schemaVersion": self.request.schema_version,
            "requestId": self.request.request_id,
            "sessionId": self.request.session_id,
            "battleId": self.request.battle_id,
            "worldId": self.request.world_id,
            "battleTier": self.request.battle_tier.value,
        }
        values.update(overrides)
        return BattleQuizRequest.model_validate(values)

    def distinct_preparation(
        self,
        *,
        batch_id: str,
        request_id: str,
        profile_id: str | None = None,
        session_id: str = "session-owner-002",
    ) -> tuple[VerifiedBatchMaterial, BattleQuizRequest, str]:
        owner = self.profile_id if profile_id is None else profile_id
        context = replace(
            self.material.context,
            profile_id=owner,
            session_id=session_id,
        )
        material = VerifiedBatchMaterial._create(
            batch_id=batch_id,
            context=context,
            plan_contract=self.material.plan_contract,
            items=self.material.items,
        )
        request = self.request_with(
            requestId=request_id,
            sessionId=context.session_id,
        )
        return material, request, owner

    def row_counts(self) -> tuple[int, int, int]:
        connection = sqlite3.connect(self.database_path)
        try:
            return tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "quiz_machines",
                    "quiz_batch_material",
                    "quiz_preparation_receipts",
                )
            )
        finally:
            connection.close()

    def test_preparation_results_are_explicit_immutable_contracts(self):
        self.assertTrue(
            hasattr(quiz_store, "PreparationReceipt"),
            "PreparationReceipt contract is missing",
        )
        self.assertTrue(
            hasattr(quiz_store, "StoredPreparation"),
            "StoredPreparation contract is missing",
        )
        self.assertTrue(is_dataclass(quiz_store.PreparationReceipt))
        self.assertTrue(is_dataclass(quiz_store.StoredPreparation))
        self.assertTrue(quiz_store.PreparationReceipt.__dataclass_params__.frozen)
        self.assertTrue(quiz_store.StoredPreparation.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(field.name for field in fields(quiz_store.PreparationReceipt)),
            (
                "schema_version",
                "action",
                "profile_id",
                "request_id",
                "batch_id",
                "payload_sha256",
                "batch_material_sha256",
                "plan_sha256",
                "output_sha256",
                "receipt_sha256",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(quiz_store.StoredPreparation)),
            ("machine", "material", "receipt", "replayed"),
        )

    def test_atomic_create_starts_ready_and_restart_replays_exact_output(self):
        self.assertTrue(
            hasattr(self.store, "create_prepared"),
            "atomic prepared-batch creation is missing",
        )
        created = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )

        self.assertFalse(created.replayed)
        self.assertEqual(created.machine.state, QuizState.READY)
        self.assertEqual(created.machine.version, 1)
        self.assertEqual(created.material, self.material)
        self.assertEqual(created.public_output, self.material.public_batch)
        self.assertEqual(created.receipt.action, "prepare")
        self.assertEqual(created.receipt.profile_id, self.profile_id)
        self.assertEqual(created.receipt.request_id, self.request.request_id)
        self.assertEqual(created.receipt.batch_id, self.material.batch_id)
        self.assertEqual(
            created.receipt.batch_material_sha256,
            self.material.batch_material_sha256,
        )
        self.assertEqual(created.receipt.plan_sha256, self.material.plan_sha256)
        request_payload = {
            "schemaVersion": self.request.schema_version,
            "requestId": self.request.request_id,
            "profileId": self.profile_id,
            "sessionId": self.request.session_id,
            "battleId": self.request.battle_id,
            "worldId": self.request.world_id,
            "battleTier": self.request.battle_tier.value,
        }
        self.assertEqual(
            created.receipt.payload_sha256,
            _sha256(_canonical_json(request_payload)),
        )
        self.assertEqual(
            created.receipt.output_sha256,
            _sha256(_canonical_json(self.material.public_payload())),
        )
        receipt_unsigned = {
            "schemaVersion": created.receipt.schema_version,
            "action": created.receipt.action,
            "profileId": created.receipt.profile_id,
            "requestId": created.receipt.request_id,
            "batchId": created.receipt.batch_id,
            "payloadSha256": created.receipt.payload_sha256,
            "batchMaterialSha256": created.receipt.batch_material_sha256,
            "planSha256": created.receipt.plan_sha256,
            "outputSha256": created.receipt.output_sha256,
        }
        self.assertEqual(
            created.receipt.receipt_sha256,
            _sha256(_canonical_json(receipt_unsigned)),
        )

        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT schema_version FROM quiz_store_metadata WHERE singleton = 1"
                ).fetchone()[0],
                6,
            )
            self.assertEqual(
                {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(quiz_preparation_receipts)"
                    )
                },
                {
                    "profile_id",
                    "request_id",
                    "batch_id",
                    "payload_sha256",
                    "output_sha256",
                    "receipt_json",
                    "receipt_sha256",
                },
            )
            self.assertEqual(
                connection.execute(
                    "SELECT state, version FROM quiz_machines"
                ).fetchall(),
                [("ready", 1)],
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM quiz_batch_material").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM quiz_preparation_receipts"
                ).fetchone()[0],
                1,
            )
            table_info = connection.execute(
                "PRAGMA table_info(quiz_preparation_receipts)"
            ).fetchall()
            self.assertEqual(
                {row[1]: row[5] for row in table_info if row[5]},
                {"profile_id": 1, "request_id": 2},
            )
            unique_indexes = [
                row[1]
                for row in connection.execute(
                    "PRAGMA index_list(quiz_preparation_receipts)"
                )
                if row[2]
            ]
            self.assertIn(
                ("batch_id",),
                {
                    tuple(
                        item[2]
                        for item in connection.execute(f"PRAGMA index_info('{index}')")
                    )
                    for index in unique_indexes
                },
            )
            foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(quiz_preparation_receipts)"
            ).fetchall()
            self.assertEqual(
                {(row[2], row[3], row[4], row[6]) for row in foreign_keys},
                {
                    ("quiz_machines", "batch_id", "batch_id", "CASCADE"),
                    ("quiz_machines", "profile_id", "profile_id", "CASCADE"),
                },
            )
        finally:
            connection.close()

        replay = self.restart().load_preparation(
            self.request,
            profile_id=self.profile_id,
        )
        self.assertIsNotNone(replay)
        assert replay is not None
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.machine, created.machine)
        self.assertEqual(replay.material, created.material)
        self.assertEqual(replay.public_output, created.public_output)
        self.assertEqual(replay.receipt, created.receipt)

    def test_exact_replay_preflights_before_touching_new_material(self):
        created = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        self.restart()

        replay = self.store.create_prepared(
            object(),  # type: ignore[arg-type]
            request=self.request,
            profile_id=self.profile_id,
        )

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.machine, created.machine)
        self.assertEqual(replay.material, created.material)
        self.assertEqual(replay.public_output, created.public_output)
        self.assertEqual(self.row_counts(), (1, 1, 1))

    def test_second_distinct_live_preparation_conflicts_without_partial_rows(self):
        first = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        second_material, second_request, owner = self.distinct_preparation(
            batch_id="batch-valuehold-002",
            request_id="prepare-request-002",
        )

        with self.assertRaises(quiz_store.QuizTransitionConflictError):
            self.store.create_prepared(
                second_material,
                request=second_request,
                profile_id=owner,
            )

        self.assertEqual(self.row_counts(), (1, 1, 1))
        self.assertEqual(
            self.store.load(first.machine.batch_id, profile_id=self.profile_id),
            first.machine,
        )
        self.assertIsNone(
            self.store.load_preparation(second_request, profile_id=owner)
        )
        with self.assertRaises(quiz_store.QuizNotFoundError):
            self.store.load(second_material.batch_id, profile_id=owner)

    def test_direct_create_rejects_live_batch_with_forged_closed_index(self):
        created = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET state = ? WHERE batch_id = ?",
                (QuizState.CLOSED.value, created.machine.batch_id),
            )
            connection.commit()
        finally:
            connection.close()
        candidate = new_quiz(
            "batch-valuehold-002",
            created.machine.item_layouts,
        )

        with self.assertRaises(quiz_store.QuizStoreCorruptionError):
            self.store.create(candidate, profile_id=self.profile_id)

        self.assertEqual(self.row_counts(), (1, 1, 1))

    def test_atomic_create_rejects_live_batch_with_forged_closed_index(self):
        created = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        second_material, second_request, owner = self.distinct_preparation(
            batch_id="batch-valuehold-002",
            request_id="prepare-request-002",
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET state = ? WHERE batch_id = ?",
                (QuizState.CLOSED.value, created.machine.batch_id),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(quiz_store.QuizStoreCorruptionError):
            self.store.create_prepared(
                second_material,
                request=second_request,
                profile_id=owner,
            )

        self.assertEqual(self.row_counts(), (1, 1, 1))

    def test_exact_replay_rejects_preexisting_multiple_live_corruption(self):
        created = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        impossible_second = new_quiz(
            "batch-valuehold-002",
            created.machine.item_layouts,
        )
        self.store.create(
            impossible_second,
            profile_id="profile-corruption-staging-002",
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET profile_id = ? WHERE batch_id = ?",
                (self.profile_id, impossible_second.batch_id),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(quiz_store.QuizStoreCorruptionError):
            self.store.load_preparation(
                self.request,
                profile_id=self.profile_id,
            )
        with self.assertRaises(quiz_store.QuizStoreCorruptionError):
            self.store.create_prepared(
                object(),  # type: ignore[arg-type]
                request=self.request,
                profile_id=self.profile_id,
            )

        self.assertEqual(self.row_counts(), (2, 1, 1))

    def test_distinct_profiles_can_each_own_one_live_preparation(self):
        first = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        second_material, second_request, second_owner = self.distinct_preparation(
            batch_id="batch-other-profile-002",
            request_id="prepare-request-other-002",
            profile_id="profile-other-002",
            session_id="session-other-002",
        )

        second = self.store.create_prepared(
            second_material,
            request=second_request,
            profile_id=second_owner,
        )

        self.assertFalse(first.replayed)
        self.assertFalse(second.replayed)
        self.assertEqual(self.row_counts(), (2, 2, 2))
        self.assertEqual(
            self.store.load(second.machine.batch_id, profile_id=second_owner),
            second.machine,
        )

    def test_replay_after_machine_advances_returns_current_persisted_snapshot(self):
        created = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        submission = QuizSubmission(
            schema_version="wayline.v1",
            request_id="initial-lock-prepare-001",
            batch_id=created.machine.batch_id,
            item_count=len(created.machine.item_layouts),
            selections=tuple(
                QuizSelection(
                    item_id=layout.item_id,
                    option_id=layout.option_ids[0],
                    confidence="certain",
                )
                for layout in created.machine.item_layouts
            ),
        )
        locked = lock_initial(
            created.machine,
            submission,
            expected_version=created.machine.version,
        )
        self.store.save_transition(
            locked,
            profile_id=self.profile_id,
            expected_version=created.machine.version,
        )
        self.restart()

        replay = self.store.create_prepared(
            object(),  # type: ignore[arg-type]
            request=self.request,
            profile_id=self.profile_id,
        )

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.machine, locked)
        self.assertEqual(replay.material, self.material)
        self.assertEqual(replay.public_output, self.material.public_batch)

    def test_profile_delete_removes_preparation_material_and_machine(self):
        created = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )

        self.store.delete_profile(self.profile_id)

        self.assertEqual(self.row_counts(), (0, 0, 0))
        self.assertIsNone(
            self.store.load_preparation(
                self.request,
                profile_id=self.profile_id,
            )
        )
        with self.assertRaises(quiz_store.QuizNotFoundError):
            self.store.load(
                created.machine.batch_id,
                profile_id=self.profile_id,
            )

    def test_changed_request_payload_conflicts_without_replacing_winner(self):
        winner = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        changed = self.request_with(sessionId="session-owner-002")

        with self.assertRaises(IdempotencyConflictError):
            self.store.load_preparation(changed, profile_id=self.profile_id)
        with self.assertRaises(IdempotencyConflictError):
            self.store.create_prepared(
                self.material,
                request=changed,
                profile_id=self.profile_id,
            )

        replay = self.store.load_preparation(
            self.request,
            profile_id=self.profile_id,
        )
        self.assertIsNotNone(replay)
        assert replay is not None
        self.assertEqual(replay.machine, winner.machine)
        self.assertEqual(replay.receipt, winner.receipt)
        self.assertEqual(self.row_counts(), (1, 1, 1))

    def test_owner_context_and_material_mismatches_leave_no_rows(self):
        mismatched_request = self.request_with(sessionId="session-owner-002")
        self.store._failpoint_stage = "after_prepared_machine_insert"
        with self.assertRaises(quiz_store.QuizTransitionConflictError):
            self.store.create_prepared(
                self.material,
                request=mismatched_request,
                profile_id=self.profile_id,
            )
        self.assertEqual(self.row_counts(), (0, 0, 0))

        other_context = replace(
            self.material.context,
            profile_id="profile-other-002",
        )
        other_owner_material = VerifiedBatchMaterial._create(
            batch_id=self.material.batch_id,
            context=other_context,
            plan_contract=self.material.plan_contract,
            items=self.material.items,
        )
        with self.assertRaises(quiz_store.QuizTransitionConflictError):
            self.store.create_prepared(
                other_owner_material,
                request=self.request,
                profile_id=self.profile_id,
            )
        with self.assertRaises(quiz_store.QuizTransitionConflictError):
            self.store.create_prepared(
                object(),  # type: ignore[arg-type]
                request=self.request,
                profile_id=self.profile_id,
            )
        self.store._failpoint_stage = None
        self.assertEqual(self.row_counts(), (0, 0, 0))

    def test_failure_after_each_prepared_insert_rolls_back_every_row(self):
        for stage in (
            "after_prepared_machine_insert",
            "after_prepared_material_insert",
            "after_preparation_receipt_insert",
        ):
            with self.subTest(stage=stage):
                self.store._failpoint_stage = stage
                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected transaction failure",
                ):
                    self.store.create_prepared(
                        self.material,
                        request=self.request,
                        profile_id=self.profile_id,
                    )
                self.store._failpoint_stage = None
                self.assertEqual(self.row_counts(), (0, 0, 0))
                self.assertIsNone(
                    self.store.load_preparation(
                        self.request,
                        profile_id=self.profile_id,
                    )
                )

    def test_identical_concurrent_creates_return_one_winner_and_one_replay(self):
        self.store.close()
        barrier = threading.Barrier(2)
        results: list[quiz_store.StoredPreparation] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def create() -> None:
            store = self._open_store()
            try:
                barrier.wait(timeout=5)
                result = store.create_prepared(
                    self.material,
                    request=self.request,
                    profile_id=self.profile_id,
                )
                with lock:
                    results.append(result)
            except BaseException as exc:
                with lock:
                    errors.append(exc)
            finally:
                store.close()

        threads = tuple(threading.Thread(target=create) for _ in range(2))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertEqual(results[0].machine, results[1].machine)
        self.assertEqual(results[0].material, results[1].material)
        self.assertEqual(results[0].receipt, results[1].receipt)
        self.assertEqual(self.row_counts(), (1, 1, 1))
        self.store = self._open_store()

    def test_concurrent_distinct_live_preparations_persist_exactly_one(self):
        second_material, second_request, owner = self.distinct_preparation(
            batch_id="batch-valuehold-002",
            request_id="prepare-request-002",
        )
        self.store.close()
        barrier = threading.Barrier(2)
        results: list[quiz_store.StoredPreparation] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def create(
            material: VerifiedBatchMaterial,
            request: BattleQuizRequest,
        ) -> None:
            store = self._open_store()
            try:
                barrier.wait(timeout=5)
                result = store.create_prepared(
                    material,
                    request=request,
                    profile_id=owner,
                )
                with lock:
                    results.append(result)
            except BaseException as exc:
                with lock:
                    errors.append(exc)
            finally:
                store.close()

        threads = (
            threading.Thread(target=create, args=(self.material, self.request)),
            threading.Thread(target=create, args=(second_material, second_request)),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].replayed)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(
            errors[0],
            quiz_store.QuizTransitionConflictError,
        )
        self.assertEqual(self.row_counts(), (1, 1, 1))

        connection = sqlite3.connect(self.database_path)
        try:
            winner = results[0].machine.batch_id
            for table in (
                "quiz_machines",
                "quiz_batch_material",
                "quiz_preparation_receipts",
            ):
                self.assertEqual(
                    connection.execute(
                        f"SELECT batch_id FROM {table}"
                    ).fetchall(),
                    [(winner,)],
                )
        finally:
            connection.close()
        self.store = self._open_store()

    def test_changed_payload_concurrency_returns_one_winner_and_one_conflict(self):
        other_context = replace(
            self.material.context,
            session_id="session-owner-002",
        )
        other_material = VerifiedBatchMaterial._create(
            batch_id="batch-valuehold-002",
            context=other_context,
            plan_contract=self.material.plan_contract,
            items=self.material.items,
        )
        other_request = self.request_with(sessionId=other_context.session_id)
        self.store.close()
        barrier = threading.Barrier(2)
        results: list[quiz_store.StoredPreparation] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def create(
            material: VerifiedBatchMaterial,
            request: BattleQuizRequest,
        ) -> None:
            store = self._open_store()
            try:
                barrier.wait(timeout=5)
                result = store.create_prepared(
                    material,
                    request=request,
                    profile_id=self.profile_id,
                )
                with lock:
                    results.append(result)
            except BaseException as exc:
                with lock:
                    errors.append(exc)
            finally:
                store.close()

        threads = (
            threading.Thread(target=create, args=(self.material, self.request)),
            threading.Thread(target=create, args=(other_material, other_request)),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].replayed)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], IdempotencyConflictError)
        self.assertEqual(self.row_counts(), (1, 1, 1))
        self.store = self._open_store()
        winning_request = (
            self.request
            if results[0].material.context.session_id == self.request.session_id
            else other_request
        )
        replay = self.store.load_preparation(
            winning_request,
            profile_id=self.profile_id,
        )
        self.assertIsNotNone(replay)
        assert replay is not None
        self.assertEqual(replay.material, results[0].material)

    def test_material_hash_tampering_fails_closed(self):
        self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_batch_material SET private_json_sha256 = ?",
                ("f" * 64,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(quiz_store.QuizStoreCorruptionError):
            self.store.load_preparation(
                self.request,
                profile_id=self.profile_id,
            )

    def test_rehashed_public_output_tampering_fails_against_material(self):
        self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            row = connection.execute(
                "SELECT receipt_json FROM quiz_preparation_receipts"
            ).fetchone()
            payload = json.loads(row[0])
            payload["outputSha256"] = "f" * 64
            unsigned = dict(payload)
            unsigned.pop("receiptSha256")
            payload["receiptSha256"] = _sha256(_canonical_json(unsigned))
            receipt_json = _canonical_json(payload)
            connection.execute(
                "UPDATE quiz_preparation_receipts SET output_sha256 = ?, "
                "receipt_json = ?, receipt_sha256 = ?",
                ("f" * 64, receipt_json, _sha256(receipt_json)),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(quiz_store.QuizStoreCorruptionError):
            self.store.load_preparation(
                self.request,
                profile_id=self.profile_id,
            )

    def test_every_batch_load_fails_closed_on_preparation_receipt_tampering(self):
        created = self.store.create_prepared(
            self.material,
            request=self.request,
            profile_id=self.profile_id,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_preparation_receipts "
                "SET receipt_json = receipt_json || ' ' WHERE batch_id = ?",
                (created.machine.batch_id,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(quiz_store.QuizStoreCorruptionError):
            self.store.load(
                created.machine.batch_id,
                profile_id=self.profile_id,
            )

    def test_schema_v5_is_explicitly_rejected_without_mutation(self):
        self.store.close()
        self.database_path.unlink()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "CREATE TABLE quiz_store_metadata ("
                "singleton INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL)"
            )
            connection.execute("INSERT INTO quiz_store_metadata VALUES (1, 5)")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(
            quiz_store.QuizStoreSchemaError,
            "schema version 5 is not accepted",
        ):
            self._open_store()

        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT schema_version FROM quiz_store_metadata WHERE singleton = 1"
                ).fetchone()[0],
                5,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'quiz_preparation_receipts'"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_schema_v6_rejects_receipt_table_without_required_keys_and_fk(self):
        self.store.close()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "ALTER TABLE quiz_preparation_receipts "
                "RENAME TO malformed_preparation_receipts"
            )
            connection.execute(
                "CREATE TABLE quiz_preparation_receipts ("
                "profile_id TEXT NOT NULL, request_id TEXT NOT NULL, "
                "batch_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL, "
                "output_sha256 TEXT NOT NULL, receipt_json TEXT NOT NULL, "
                "receipt_sha256 TEXT NOT NULL)"
            )
            connection.execute("DROP TABLE malformed_preparation_receipts")
            connection.commit()
        finally:
            connection.close()

        opened: quiz_store.QuizStore | None = None
        try:
            with self.assertRaises(quiz_store.QuizStoreSchemaError):
                opened = self._open_store()
        finally:
            if opened is not None:
                opened.close()


if __name__ == "__main__":
    unittest.main()
