from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.wayline_forge.app.assisted_route_store import (
    AssistedRouteStore,
    AssistedRouteStoreError,
)
from services.wayline_forge.app.profile_store import ProfileStore
from services.wayline_forge.app.quiz_machine import QuizItemLayout, new_quiz
from services.wayline_forge.app.quiz_store import (
    QuizStore,
    QuizTransitionConflictError,
)
from services.wayline_forge.tests.test_assisted_route_machine import (
    AssistedRouteMachineTests,
)


class AssistedRouteStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        AssistedRouteMachineTests.setUpClass()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "wayline.sqlite3"
        self.profiles = ProfileStore(self.path)
        self.profile = self.profiles.create_profile(
            request_id="create-profile-assisted-store-001"
        )
        self.session = self.profiles.create_session(
            request_id="create-session-assisted-store-001",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        fixture = AssistedRouteMachineTests()
        self.fixture = fixture
        self.route_plan_sha256 = "9" * 64
        self.material = fixture._material(
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
        )
        self.store = AssistedRouteStore(
            self.path,
            compiler=fixture.verifier.compiler,
            manifest=fixture.verifier.manifest,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.profiles.close()
        self.temporary.cleanup()

    def _head(self) -> tuple[int, str]:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT ordinal, event_hash FROM event_log "
                "WHERE profile_id = ? ORDER BY ordinal DESC LIMIT 1",
                (self.profile.profile_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return int(row[0]), str(row[1])

    def _create(
        self,
        *,
        request_id: str = "prepare-assisted-store-001",
        route_id: str = "assisted-route-store-001",
        payload_sha256: str = "1" * 64,
        route_plan_sha256: str | None = None,
        event_head: tuple[int, str] | None = None,
    ):
        ordinal, digest = event_head or self._head()
        return self.store.create_prepared(
            route_id=route_id,
            profile_id=self.profile.profile_id,
            source_session_id=self.session.session_id,
            world_id="valuehold",
            preparation_request_id=request_id,
            preparation_payload_sha256=payload_sha256,
            event_head_ordinal=ordinal,
            event_head_hash=digest,
            route_plan_sha256=(
                self.route_plan_sha256
                if route_plan_sha256 is None
                else route_plan_sha256
            ),
            material=self.material,
        )

    def test_private_material_persists_and_exactly_replays_after_restart(self):
        stored = self._create()

        self.assertEqual(
            self.store.load(
                "assisted-route-store-001",
                profile_id=self.profile.profile_id,
            ),
            stored,
        )
        self.assertEqual(
            self.store.load_preparation(
                "prepare-assisted-store-001",
                profile_id=self.profile.profile_id,
            ),
            stored,
        )
        self.assertEqual(
            self.store.active_route_id(self.profile.profile_id),
            "assisted-route-store-001",
        )
        self.store.close()
        fixture = AssistedRouteMachineTests()
        self.store = AssistedRouteStore(
            self.path,
            compiler=fixture.verifier.compiler,
            manifest=fixture.verifier.manifest,
        )
        self.assertEqual(
            self.store.load_active(
                self.profile.profile_id,
                world_id="valuehold",
            ),
            stored,
        )

    def test_receipts_are_exact_and_new_request_aliases_active_route(self):
        first = self._create()
        replay = self._create()
        alias = self._create(
            request_id="prepare-assisted-store-002",
            route_id="assisted-route-store-should-not-replace",
        )

        self.assertEqual(replay, first)
        self.assertEqual(alias, first)
        with self.assertRaises(AssistedRouteStoreError) as conflict:
            self._create(payload_sha256="f" * 64)
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        with sqlite3.connect(self.path) as connection:
            material_count = connection.execute(
                "SELECT COUNT(*) FROM assisted_route_material"
            ).fetchone()[0]
            receipt_count = connection.execute(
                "SELECT COUNT(*) FROM assisted_route_preparation_receipts"
            ).fetchone()[0]
        self.assertEqual(material_count, 1)
        self.assertEqual(receipt_count, 2)

    def test_concurrent_preparations_create_one_route_and_two_receipts(self):
        event_head = self._head()

        def create(suffix: str):
            route_store = AssistedRouteStore(
                self.path,
                compiler=self.fixture.verifier.compiler,
                manifest=self.fixture.verifier.manifest,
                timeout_seconds=2.0,
            )
            try:
                return route_store.create_prepared(
                    route_id=f"assisted-concurrent-{suffix}",
                    profile_id=self.profile.profile_id,
                    source_session_id=self.session.session_id,
                    world_id="valuehold",
                    preparation_request_id=f"prepare-assisted-concurrent-{suffix}",
                    preparation_payload_sha256=(
                        "a" * 64 if suffix == "a" else "b" * 64
                    ),
                    event_head_ordinal=event_head[0],
                    event_head_hash=event_head[1],
                    route_plan_sha256=self.route_plan_sha256,
                    material=self.material,
                )
            finally:
                route_store.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(create, "a")
            second = executor.submit(create, "b")
            results = (first.result(timeout=10), second.result(timeout=10))

        self.assertEqual(results[0].route_id, results[1].route_id)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM assisted_route_material"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM assisted_route_preparation_receipts"
                ).fetchone()[0],
                2,
            )

    def test_alias_is_bound_to_current_assisted_route_plan_not_material_plan(self):
        first = self._create()

        with self.assertRaises(AssistedRouteStoreError) as stale:
            self._create(
                request_id="prepare-assisted-store-new-plan",
                route_id="assisted-route-store-new-plan",
                payload_sha256="2" * 64,
                route_plan_sha256="8" * 64,
            )

        self.assertEqual(first.route_plan_sha256, self.route_plan_sha256)
        self.assertEqual(stale.exception.code, "stale_event_head")

    def test_active_route_in_another_world_blocks_instead_of_coexisting(self):
        active = self._create()
        other_world = replace(active, world_id="decimara")

        with patch.object(self.store, "load_active", return_value=other_world) as load:
            with self.assertRaises(AssistedRouteStoreError) as blocked:
                self._create(
                    request_id="prepare-assisted-store-other-world",
                    route_id="assisted-route-store-other-world",
                    payload_sha256="2" * 64,
                )

        load.assert_called_once_with(self.profile.profile_id)
        self.assertEqual(blocked.exception.code, "activity_in_progress")

    def test_tampered_private_json_or_digest_fails_closed(self):
        self._create()
        for statement in (
            "UPDATE assisted_route_material SET material_json = material_json || ' '",
            "UPDATE assisted_route_material SET material_sha256 = '" + "f" * 64 + "'",
        ):
            with self.subTest(statement=statement):
                with sqlite3.connect(self.path) as connection:
                    connection.execute(statement)
                with self.assertRaises(AssistedRouteStoreError) as raised:
                    self.store.load(
                        "assisted-route-store-001",
                        profile_id=self.profile.profile_id,
                    )
                self.assertEqual(raised.exception.code, "integrity_failure")
                self.store.close()
                fixture = AssistedRouteMachineTests()
                self.store = AssistedRouteStore(
                    self.path,
                    compiler=fixture.verifier.compiler,
                    manifest=fixture.verifier.manifest,
                )
                with sqlite3.connect(self.path) as connection:
                    connection.execute(
                        "DELETE FROM assisted_route_preparation_receipts"
                    )
                    connection.execute("DELETE FROM assisted_route_material")
                self._create(
                    request_id="prepare-assisted-store-reset",
                    route_id="assisted-route-store-001",
                )

    def test_tampered_stored_timestamp_fails_closed(self):
        self._create()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE assisted_route_material SET created_at_utc = 'not-a-time'"
            )

        with self.assertRaises(AssistedRouteStoreError) as raised:
            self.store.load(
                "assisted-route-store-001",
                profile_id=self.profile.profile_id,
            )

        self.assertEqual(raised.exception.code, "integrity_failure")

    def _assert_reopen_rejects_schema(self) -> None:
        self.store.close()
        with self.assertRaises(AssistedRouteStoreError) as raised:
            self.store = AssistedRouteStore(
                self.path,
                compiler=self.fixture.verifier.compiler,
                manifest=self.fixture.verifier.manifest,
            )
        self.assertEqual(raised.exception.code, "integrity_failure")

    def test_schema_validation_rejects_extra_index(self):
        self.store.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE INDEX assisted_route_untrusted_extra "
                "ON assisted_route_material(created_at_utc)"
            )
        self._assert_reopen_rejects_schema()

    def test_schema_validation_rejects_wrong_column_type(self):
        self.store.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE assisted_route_store_metadata")
            connection.execute(
                "CREATE TABLE assisted_route_store_metadata ("
                "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                "schema_version TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO assisted_route_store_metadata VALUES (1, '1')"
            )
        self._assert_reopen_rejects_schema()

    def test_schema_validation_requires_receipt_profile_route_ownership_fk(self):
        self.store.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "DROP TABLE assisted_route_preparation_receipts"
            )
            connection.execute(
                """
                CREATE TABLE assisted_route_preparation_receipts (
                    profile_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    output_sha256 TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    PRIMARY KEY (profile_id, request_id),
                    FOREIGN KEY (route_id)
                        REFERENCES assisted_route_material(route_id)
                        ON DELETE CASCADE
                )
                """
            )
        self._assert_reopen_rejects_schema()

    def test_stale_event_head_and_wrong_owner_are_rejected(self):
        ordinal, digest = self._head()
        with self.assertRaises(AssistedRouteStoreError) as stale:
            self._create(event_head=(ordinal, "f" * 64))
        self.assertEqual(stale.exception.code, "stale_event_head")

        self._create(event_head=(ordinal, digest))
        with self.assertRaises(AssistedRouteStoreError) as wrong_owner:
            self.store.load(
                "assisted-route-store-001",
                profile_id="profile-not-owner-001",
            )
        self.assertEqual(wrong_owner.exception.code, "profile_not_found")

    @staticmethod
    def _normal_quiz() -> object:
        layouts = tuple(
            QuizItemLayout(
                item_id=f"normal-item-{index}",
                option_ids=tuple(
                    f"normal-item-{index}-option-{letter}"
                    for letter in "abcd"
                ),
            )
            for index in range(1, 4)
        )
        return new_quiz("batch-normal-001", layouts)

    def test_normal_quiz_and_assisted_route_serialize_one_active_activity(self):
        quizzes = QuizStore(
            self.path,
            allow_unverified_test_material=True,
        )
        try:
            quizzes.create(
                self._normal_quiz(),
                profile_id=self.profile.profile_id,
            )
            with self.assertRaises(AssistedRouteStoreError) as blocked:
                self._create()
            self.assertEqual(blocked.exception.code, "activity_in_progress")
        finally:
            quizzes.close()

        # A fresh database proves the inverse creation order through QuizStore.
        self.store.close()
        self.profiles.close()
        self.temporary.cleanup()
        self.setUp()
        self._create()
        quizzes = QuizStore(
            self.path,
            allow_unverified_test_material=True,
        )
        try:
            with self.assertRaises(QuizTransitionConflictError):
                quizzes.create(
                    self._normal_quiz(),
                    profile_id=self.profile.profile_id,
                )
        finally:
            quizzes.close()

    def test_concurrent_normal_and_assisted_creation_leave_one_active_activity(self):
        event_head = self._head()
        barrier = Barrier(2)

        def create_assisted():
            routes = AssistedRouteStore(
                self.path,
                compiler=self.fixture.verifier.compiler,
                manifest=self.fixture.verifier.manifest,
                timeout_seconds=2.0,
            )
            try:
                barrier.wait(timeout=5)
                try:
                    stored = routes.create_prepared(
                        route_id="assisted-concurrent-activity",
                        profile_id=self.profile.profile_id,
                        source_session_id=self.session.session_id,
                        world_id="valuehold",
                        preparation_request_id="prepare-assisted-concurrent-activity",
                        preparation_payload_sha256="a" * 64,
                        event_head_ordinal=event_head[0],
                        event_head_hash=event_head[1],
                        route_plan_sha256=self.route_plan_sha256,
                        material=self.material,
                    )
                    return ("assisted", stored.route_id)
                except AssistedRouteStoreError as error:
                    return ("assisted_error", error.code)
            finally:
                routes.close()

        def create_normal():
            quizzes = QuizStore(
                self.path,
                timeout_seconds=2.0,
                allow_unverified_test_material=True,
            )
            try:
                barrier.wait(timeout=5)
                try:
                    stored = quizzes.create(
                        self._normal_quiz(),
                        profile_id=self.profile.profile_id,
                    )
                    return ("normal", stored.batch_id)
                except QuizTransitionConflictError:
                    return ("normal_error", "activity_in_progress")
            finally:
                quizzes.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(create_assisted),
                executor.submit(create_normal),
            )
            results = tuple(future.result(timeout=10) for future in futures)

        successes = tuple(
            result for result in results if result[0] in {"assisted", "normal"}
        )
        self.assertEqual(len(successes), 1)
        route_active = self.store.active_route_id(self.profile.profile_id) is not None
        quizzes = QuizStore(
            self.path,
            allow_unverified_test_material=True,
        )
        try:
            quiz_active = quizzes.resumable_batch_id(self.profile.profile_id) is not None
        finally:
            quizzes.close()
        self.assertEqual(int(route_active) + int(quiz_active), 1)

if __name__ == "__main__":
    unittest.main()
