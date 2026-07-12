from __future__ import annotations

import unittest

from services.wayline_forge.app.boss_gate import evaluate_world_clear
from services.wayline_forge.app.evidence_reducer import reduce_events
from services.wayline_forge.app.events import (
    OUTCOME_EVENT_SCHEMA_VERSION,
    BossCompletionEvent,
    SealTrialCompletionEvent,
)
from services.wayline_forge.tests.fixtures import event


class ProgressionRuleTests(unittest.TestCase):
    def _boss_miss(self, ordinal: int = 2) -> BossCompletionEvent:
        return BossCompletionEvent(
            schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
            event_id="boss-completion-rule",
            idempotency_id="boss-completion-rule-request",
            ordinal=ordinal,
            profile_id="profile-1",
            session_id="session-1",
            world_id="valuehold",
            battle_id="valuehold_boss",
            occurred_at="2026-07-12T17:00:00Z",
            combat_won=True,
            final_correct=5,
            item_count=8,
            is_campaign_finale=False,
            batch_id="batch-boss-rule",
        )

    def _seal_miss(self, ordinal: int, attempt: int) -> SealTrialCompletionEvent:
        return SealTrialCompletionEvent(
            schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
            event_id=f"seal-miss-rule-{attempt}",
            idempotency_id=f"seal-miss-rule-request-{attempt}",
            ordinal=ordinal,
            profile_id="profile-1",
            session_id="session-1",
            world_id="valuehold",
            battle_id=f"valuehold_seal_trial_{attempt}",
            occurred_at=f"2026-07-12T17:0{attempt}:00Z",
            attempt_number=attempt,
            passed=False,
            final_correct=1,
            item_count=3,
            batch_id=f"batch-seal-rule-{attempt}",
            gate_recheck_sha256="a" * 64,
        )

    def test_assisted_plan_is_one_worked_example_plus_two_supported_mcqs(self) -> None:
        state = reduce_events(
            (
                event.activate(1),
                self._boss_miss(),
                self._seal_miss(3, 1),
                self._seal_miss(4, 2),
            )
        )

        plan = evaluate_world_clear(state, "valuehold").assisted_route_plan

        self.assertIsNotNone(plan)
        self.assertEqual(
            tuple(slot.kind for slot in plan.slots),
            ("worked_example", "supported_mcq", "supported_mcq"),
        )
        self.assertTrue(all(slot.support_provided for slot in plan.slots))

    def test_assisted_route_completion_clears_regardless_of_mcq_score(self) -> None:
        events = (
            event.activate(1),
            self._boss_miss(),
            self._seal_miss(3, 1),
            self._seal_miss(4, 2),
            event.assisted_completion(
                ordinal=5,
                request="assisted-completion-rule-request",
            ),
        )

        state = reduce_events(events)
        result = evaluate_world_clear(state, "valuehold")

        self.assertTrue(result.cleared)
        self.assertFalse(result.seal_trial_required)
        self.assertIsNone(result.assisted_route_plan)
        self.assertEqual(state.skills, ())


if __name__ == "__main__":
    unittest.main()
