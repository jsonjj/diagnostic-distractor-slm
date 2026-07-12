from __future__ import annotations

import unittest

from services.wayline_forge.app.boss_gate import (
    evaluate_boss_gate,
    evaluate_world_clear,
)
from services.wayline_forge.app.evidence_reducer import reduce_events
from services.wayline_forge.tests.fixtures import event


class BossGateTests(unittest.TestCase):
    def test_gate_uses_four_wins_sixteen_items_latest_ten_and_core_coverage(self):
        state = event.ready_valuehold_state(latest_ten_correct=7)

        result = evaluate_boss_gate(state, "valuehold")

        self.assertTrue(result.unlocked)
        self.assertEqual(result.lead_in_wins, 4)
        self.assertEqual(result.valid_world_items, 16)
        self.assertEqual(result.latest_ten_correct_count, 7)
        self.assertEqual(result.ready_core_subskill_count, 2)

    def test_gate_core_roster_comes_only_from_authoritative_world_activation(self):
        events = list(event.ready_valuehold_events())
        events.append(event.correct(
            ordinal=max(item.ordinal for item in events) + 1,
            world="valuehold",
            skill="observation_claimed_core",
            core_subskills=("observation_claimed_core",),
            question="untrusted-roster-question",
            template="untrusted-roster-template",
        ))

        result = evaluate_boss_gate(reduce_events(events), "valuehold")

        self.assertEqual(result.core_subskill_count, 2)

    def test_gate_fails_closed_without_authoritative_world_activation(self):
        state = reduce_events([
            event.correct(ordinal=1, skill="observation_only_skill"),
            event.correct(
                ordinal=2,
                skill="observation_only_skill",
                question="second-observation",
                template="second-template",
            ),
        ])

        result = evaluate_boss_gate(state, "valuehold")

        self.assertEqual(result.core_subskill_count, 1)
        self.assertEqual(result.ready_core_subskill_count, 0)
        self.assertIn("core_subskill_coverage", result.unmet_requirements)

    def test_six_of_latest_ten_keeps_gate_locked(self):
        result = evaluate_boss_gate(
            event.ready_valuehold_state(latest_ten_correct=6), "valuehold"
        )

        self.assertFalse(result.unlocked)
        self.assertIn("latest_ten_accuracy", result.unmet_requirements)

    def test_final_pass_correctness_does_not_replace_first_pass_for_gate(self):
        events = list(event.ready_valuehold_events(latest_ten_correct=7))
        observations = [item for item in events if hasattr(item, "skill_id")]
        latest_correct = next(item for item in observations[-10:] if item.first_correct)
        replacement_index = events.index(latest_correct)
        events[replacement_index] = event.wrong(
            "error-mental_add_sub",
            ordinal=latest_correct.ordinal,
            world="valuehold",
            battle=latest_correct.battle_id,
            batch=latest_correct.batch_id,
            skill=latest_correct.skill_id,
            template=latest_correct.template_id,
            question=latest_correct.question_id,
            keep_wrong=False,
            core_subskills=("place_value", "mental_add_sub"),
        )

        result = evaluate_boss_gate(reduce_events(events), "valuehold")

        self.assertEqual(result.latest_ten_correct_count, 6)
        self.assertFalse(result.unlocked)

    def test_core_subskill_needs_two_exposures_and_one_first_pass_correct(self):
        events = list(event.ready_valuehold_events())
        observations = [item for item in events if hasattr(item, "skill_id")]
        first_mental = next(item for item in observations if item.skill_id == "mental_add_sub")
        replacement = event.correct(
            ordinal=first_mental.ordinal,
            world="valuehold",
            battle=first_mental.battle_id,
            batch=first_mental.batch_id,
            skill="mental_add_sub",
            question=first_mental.question_id,
            template=first_mental.template_id,
            core_subskills=("place_value", "mental_add_sub"),
        )
        changed: list[object] = []
        for item in events:
            if getattr(item, "skill_id", None) != "mental_add_sub":
                changed.append(item)
            elif item.ordinal == first_mental.ordinal:
                changed.append(replacement)
            else:
                changed.append(event.wrong(
                    "error-mental_add_sub",
                    ordinal=item.ordinal,
                    world="valuehold",
                    battle=item.battle_id,
                    batch=item.batch_id,
                    skill="mental_add_sub",
                    question=item.question_id,
                    template=item.template_id,
                    core_subskills=("place_value", "mental_add_sub"),
                ))

        result = evaluate_boss_gate(reduce_events(changed), "valuehold")

        self.assertEqual(result.ready_core_subskill_count, 2)
        self.assertNotIn("core_subskill_coverage", result.unmet_requirements)

    def test_duplicate_win_for_same_battle_counts_once(self):
        events = list(event.ready_valuehold_events())
        events.insert(4, event.battle_win(21, battle="lead-in-4"))

        result = evaluate_boss_gate(reduce_events(events), "valuehold")

        self.assertEqual(result.lead_in_wins, 4)

    def test_world_clears_at_six_of_eight_after_revision(self):
        state = reduce_events([event.boss(1, final_correct=6)])

        result = evaluate_world_clear(state, "valuehold")

        self.assertTrue(result.cleared)
        self.assertEqual(result.required_final_correct, 6)
        self.assertFalse(result.seal_trial_required)

    def test_missed_boss_threshold_preserves_victory_and_creates_three_item_seal_trial(self):
        state = reduce_events([event.boss(1, final_correct=5)])

        result = evaluate_world_clear(state, "valuehold")

        self.assertFalse(result.cleared)
        self.assertTrue(result.combat_victory_preserved)
        self.assertTrue(result.seal_trial_required)
        self.assertEqual(result.seal_trial_item_count, 3)
        self.assertFalse(result.boss_replay_required)

    def test_campaign_finale_requires_eight_of_ten(self):
        seven = evaluate_world_clear(
            reduce_events([event.boss(1, final_correct=7, item_count=10, finale=True)]),
            "valuehold",
        )
        eight = evaluate_world_clear(
            reduce_events([event.boss(1, final_correct=8, item_count=10, finale=True)]),
            "valuehold",
        )

        self.assertFalse(seven.cleared)
        self.assertTrue(eight.cleared)

    def test_two_missed_seal_trials_unlock_assisted_route(self):
        state = reduce_events([
            event.activate(ordinal=1),
            event.boss(2, final_correct=5),
            event.seal_trial(3, passed=False, attempt=1),
            event.seal_trial(4, passed=False, attempt=2),
        ])

        result = evaluate_world_clear(state, "valuehold")

        self.assertTrue(result.combat_victory_preserved)
        self.assertTrue(result.assisted_route_unlocked)
        self.assertFalse(result.boss_replay_required)
        self.assertIsNotNone(result.assisted_route_plan)
        self.assertEqual(result.assisted_route_plan.item_count, 3)
        self.assertEqual(result.assisted_route_plan.slots[0].kind, "worked_example")
        self.assertTrue(result.assisted_route_plan.slots[0].support_provided)
        self.assertTrue(all(
            slot.difficulty_delta < 0
            for slot in result.assisted_route_plan.slots[1:]
        ))
        self.assertTrue(all(
            slot.skill_id in {"place_value", "mental_add_sub"}
            for slot in result.assisted_route_plan.slots
        ))

    def test_assisted_route_fails_closed_without_authoritative_curriculum(self):
        state = reduce_events([
            event.boss(1, final_correct=5),
            event.seal_trial(2, passed=False, attempt=1),
            event.seal_trial(3, passed=False, attempt=2),
        ])

        with self.assertRaisesRegex(ValueError, "authoritative curriculum"):
            evaluate_world_clear(state, "valuehold")

    def test_duplicate_or_skipped_seal_trial_attempt_number_fails_closed(self):
        from services.wayline_forge.app.evidence_reducer import InvalidEventSequenceError

        duplicate = (
            event.boss(1, final_correct=5),
            event.seal_trial(2, passed=False, attempt=1),
            event.seal_trial(3, passed=False, attempt=1),
        )
        skipped = (
            event.boss(1, final_correct=5),
            event.seal_trial(2, passed=False, attempt=2),
        )

        with self.assertRaises(InvalidEventSequenceError):
            reduce_events(duplicate)
        with self.assertRaises(InvalidEventSequenceError):
            reduce_events(skipped)

    def test_new_boss_event_cannot_reset_seal_trial_attempt_numbers(self):
        from services.wayline_forge.app.evidence_reducer import InvalidEventSequenceError

        invalid_reset = (
            event.boss(1, final_correct=5),
            event.seal_trial(2, passed=False, attempt=1),
            event.boss(3, final_correct=5),
            event.seal_trial(4, passed=False, attempt=1),
        )

        with self.assertRaises(InvalidEventSequenceError):
            reduce_events(invalid_reset)

    def test_passing_seal_trial_clears_without_replaying_won_boss(self):
        state = reduce_events([
            event.boss(1, final_correct=5),
            event.seal_trial(2, passed=True, attempt=1),
        ])

        result = evaluate_world_clear(state, "valuehold")

        self.assertTrue(result.cleared)
        self.assertFalse(result.boss_replay_required)

    def test_later_passing_seal_trial_closes_assisted_route(self):
        state = reduce_events([
            event.activate(ordinal=1),
            event.boss(2, final_correct=5),
            event.seal_trial(3, passed=False, attempt=1),
            event.seal_trial(4, passed=False, attempt=2),
            event.seal_trial(5, passed=True, attempt=3),
        ])

        result = evaluate_world_clear(state, "valuehold")

        self.assertTrue(result.cleared)
        self.assertIsNone(result.assisted_route_plan)


if __name__ == "__main__":
    unittest.main()
