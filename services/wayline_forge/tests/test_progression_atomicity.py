from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from services.wayline_forge.app.events import (
    OUTCOME_EVENT_SCHEMA_VERSION,
    BattleCompletionEvent,
)
from services.wayline_forge.app.profile_store import (
    CampaignStateConflictError,
    ProfileNotFoundError,
    ProfileStore,
)


class ProgressionAppendAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "wayline.sqlite3"
        self.store = ProfileStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_authoritative_outcome_rejects_a_session_rotated_before_append(self) -> None:
        profile = self.store.create_profile(request_id="create-profile-race-001")
        stale = self.store.create_session(
            request_id="create-session-race-001",
            profile_id=profile.profile_id,
            client_build="mac-demo-001",
        )
        event = BattleCompletionEvent(
            schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
            event_id="battle-completion-race-001",
            idempotency_id="complete-battle-race-001",
            ordinal=2,
            profile_id=profile.profile_id,
            session_id=stale.session_id,
            world_id="valuehold",
            battle_id="valuehold_route_1",
            occurred_at=stale.opened_at,
            won=True,
            is_lead_in=True,
            batch_id="batch-race-001",
            final_correct=3,
            item_count=3,
        )
        current = self.store.create_session(
            request_id="create-session-race-002",
            profile_id=profile.profile_id,
            client_build="mac-demo-002",
        )

        with self.assertRaises(CampaignStateConflictError):
            self.store.append(event)

        self.assertEqual(self.store.load_open_session(profile.profile_id), current)
        self.assertFalse(
            any(
                isinstance(item, BattleCompletionEvent)
                for item in self.store.load_events(profile.profile_id)
            )
        )

    def test_append_cannot_resurrect_deleted_profile_events_or_projection(self) -> None:
        profile = self.store.create_profile(request_id="create-profile-delete-race")
        event = BattleCompletionEvent(
            schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
            event_id="battle-completion-delete-race",
            idempotency_id="complete-battle-delete-race",
            ordinal=1,
            profile_id=profile.profile_id,
            session_id="session-delete-race",
            world_id="valuehold",
            battle_id="valuehold_route_1",
            occurred_at=profile.created_at,
            won=True,
            is_lead_in=True,
            batch_id="batch-delete-race",
            final_correct=3,
            item_count=3,
        )
        self.store.delete_profile(profile.profile_id)

        with self.assertRaises(ProfileNotFoundError):
            self.store.append(event)

        with sqlite3.connect(self.path) as connection:
            event_count = connection.execute(
                "SELECT COUNT(*) FROM event_log WHERE profile_id = ?",
                (profile.profile_id,),
            ).fetchone()[0]
            projection_count = connection.execute(
                "SELECT COUNT(*) FROM learner_projection WHERE profile_id = ?",
                (profile.profile_id,),
            ).fetchone()[0]
        self.assertEqual(event_count, 0)
        self.assertEqual(projection_count, 0)


if __name__ == "__main__":
    unittest.main()
