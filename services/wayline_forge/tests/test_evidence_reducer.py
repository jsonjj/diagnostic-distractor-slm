from __future__ import annotations

from dataclasses import replace
import unittest

from services.wayline_forge.app.evidence_reducer import reduce_events
from services.wayline_forge.tests.fixtures import event


class EvidenceReducerTests(unittest.TestCase):
    def test_only_world_activation_changes_active_campaign_world_and_core_roster(self):
        state = reduce_events([
            event.activate(
                ordinal=1,
                world="valuehold",
                core_subskills=("place_value", "mental_add_sub"),
            ),
            event.correct(
                ordinal=2,
                world="decimara",
                skill="decimal_add_sub",
                core_subskills=("forged_observation_skill",),
                transfer=True,
            ),
        ])

        self.assertEqual(state.active_world_id, "valuehold")
        self.assertEqual(
            state.world("valuehold").core_subskill_ids,
            ("place_value", "mental_add_sub"),
        )
        self.assertEqual(state.world("decimara").core_subskill_ids, ())

    def test_one_wrong_answer_is_never_an_active_diagnosis(self):
        leaning = reduce_events([event.wrong("align_by_ends", confidence="leaning")])
        certain = reduce_events([event.wrong("align_by_ends", confidence="certain")])

        self.assertEqual(leaning.procedure("align_by_ends").status, "candidate")
        self.assertEqual(certain.procedure("align_by_ends").status, "suspected")
        self.assertNotEqual(certain.procedure("align_by_ends").status, "active")

    def test_two_questions_on_one_template_are_suspected_not_active(self):
        state = reduce_events([
            event.wrong("align_by_ends", ordinal=1, template="same", question="q-1"),
            event.wrong("align_by_ends", ordinal=2, template="same", question="q-2"),
        ])

        self.assertEqual(state.procedure("align_by_ends").status, "suspected")

    def test_two_distinct_templates_activate_with_leaning_or_certain_evidence(self):
        state = reduce_events([
            event.wrong(
                "align_by_ends", ordinal=1, template="template-a", confidence="leaning"
            ),
            event.wrong(
                "align_by_ends", ordinal=2, template="template-b", confidence="guessing"
            ),
        ])

        self.assertEqual(state.procedure("align_by_ends").status, "active")

    def test_three_distinct_templates_activate_at_any_confidence(self):
        state = reduce_events([
            event.wrong(
                "align_by_ends", ordinal=ordinal, template=f"template-{ordinal}", confidence="guessing"
            )
            for ordinal in range(1, 4)
        ])

        self.assertEqual(state.procedure("align_by_ends").status, "active")

    def test_wrong_to_correct_retains_signal_and_marks_skill_fragile(self):
        state = reduce_events([
            event.wrong(
                "align_by_ends", confidence="certain", keep_wrong=False
            )
        ])

        self.assertEqual(state.procedure("align_by_ends").status, "suspected")
        self.assertEqual(state.skill("place_value").status, "fragile")
        self.assertEqual(state.skill("place_value").self_correction_count, 1)

    def test_keeping_same_wrong_answer_raises_route_priority(self):
        kept = reduce_events([event.wrong("align_by_ends", keep_wrong=True)])
        corrected = reduce_events([event.wrong("align_by_ends", keep_wrong=False)])

        self.assertGreater(
            kept.procedure("align_by_ends").priority,
            corrected.procedure("align_by_ends").priority,
        )

    def test_switching_between_wrong_answers_marks_both_routes_ambiguous(self):
        state = reduce_events([
            event.wrong(
                "align_by_ends",
                final_procedure="ignore_decimal",
                keep_wrong=True,
            )
        ])

        self.assertTrue(state.procedure("align_by_ends").ambiguous)
        self.assertTrue(state.procedure("ignore_decimal").ambiguous)
        self.assertIn(
            ("align_by_ends", "ignore_decimal"),
            state.ambiguous_procedure_pairs,
        )

    def test_ambiguous_pairs_are_normalized_and_removed_after_both_routes_resolve(self):
        forward = event.wrong(
            "align_by_ends",
            ordinal=1,
            final_procedure="ignore_decimal",
            keep_wrong=True,
        )
        reverse = event.wrong(
            "ignore_decimal",
            ordinal=2,
            question="reverse-question",
            template="reverse-template",
            final_procedure="align_by_ends",
            keep_wrong=True,
        )
        unresolved = reduce_events((forward, reverse))
        self.assertEqual(
            unresolved.ambiguous_procedure_pairs,
            (("align_by_ends", "ignore_decimal"),),
        )

        transfers = tuple(
            event.correct(
                ordinal=ordinal,
                question=f"resolved-{ordinal}",
                template=f"resolved-template-{ordinal}",
                batch="resolution-a" if ordinal < 5 else "resolution-b",
                transfer=True,
                changed_context=True,
                targeted_procedures=("align_by_ends", "ignore_decimal"),
            )
            for ordinal in range(3, 6)
        )
        resolved = reduce_events((forward, reverse) + transfers)

        self.assertEqual(resolved.procedure("align_by_ends").status, "resolved")
        self.assertEqual(resolved.procedure("ignore_decimal").status, "resolved")
        self.assertEqual(resolved.ambiguous_procedure_pairs, ())
        self.assertFalse(resolved.procedure("align_by_ends").ambiguous)
        self.assertFalse(resolved.procedure("ignore_decimal").ambiguous)

    def test_secure_skill_requires_three_distinct_first_pass_correct_items(self):
        state = reduce_events([
            event.correct(
                ordinal=1,
                question="q-1",
                template="t-1",
                confidence="certain",
            ),
            event.correct(
                ordinal=2,
                question="q-2",
                template="t-2",
                confidence="leaning",
            ),
            event.correct(
                ordinal=3,
                question="q-3",
                template="t-3",
                confidence="leaning",
                changed_context=True,
                transfer=True,
            ),
        ])

        self.assertEqual(state.skill("place_value").status, "secure")

    def test_secure_skill_requires_changed_context_and_two_non_guessing_answers(self):
        no_transfer = reduce_events([
            event.correct(ordinal=i, question=f"q-{i}", template=f"t-{i}")
            for i in range(1, 4)
        ])
        too_many_guesses = reduce_events([
            event.correct(
                ordinal=i,
                question=f"gq-{i}",
                template=f"gt-{i}",
                confidence="guessing" if i < 3 else "certain",
                changed_context=(i == 3),
                transfer=(i == 3),
            )
            for i in range(1, 4)
        ])

        self.assertNotEqual(no_transfer.skill("place_value").status, "secure")
        self.assertNotEqual(too_many_guesses.skill("place_value").status, "secure")

    def test_three_later_targeted_transfers_across_two_quizzes_resolve_hypothesis(self):
        events = [
            event.wrong("align_by_ends", ordinal=1, template="wrong-a"),
            event.wrong("align_by_ends", ordinal=2, template="wrong-b"),
        ]
        for ordinal, batch in ((3, "probe-a"), (4, "probe-a"), (5, "probe-b")):
            events.append(event.correct(
                ordinal=ordinal,
                question=f"target-{ordinal}",
                template=f"target-template-{ordinal}",
                batch=batch,
                transfer=True,
                changed_context=True,
                targeted_procedures=("align_by_ends",),
            ))

        state = reduce_events(events)

        self.assertEqual(state.procedure("align_by_ends").status, "resolved")

    def test_mastery_requires_five_of_six_transfers_across_two_sessions(self):
        observations = []
        for ordinal in range(1, 7):
            kwargs = dict(
                ordinal=ordinal,
                question=f"transfer-{ordinal}",
                template=f"transfer-template-{ordinal}",
                session="session-1" if ordinal <= 3 else "session-2",
                transfer=True,
                changed_context=True,
                confidence="certain",
            )
            if ordinal == 6:
                observations.append(event.wrong("place_shift", **kwargs))
            else:
                observations.append(event.correct(**kwargs))

        state = reduce_events(observations)

        self.assertEqual(state.skill("place_value").status, "mastery")

    def test_later_fragile_evidence_overrides_stale_mastery_until_requalified(self):
        mastery = tuple(
            event.correct(
                ordinal=ordinal,
                question=f"mastery-{ordinal}",
                template=f"mastery-template-{ordinal}",
                session="session-1" if ordinal <= 3 else "session-2",
                transfer=True,
                changed_context=True,
                confidence="certain",
            )
            for ordinal in range(1, 7)
        )
        fragile = event.correct(
            ordinal=7,
            question="later-fragile",
            template="later-fragile-template",
            confidence="guessing",
        )

        stale = reduce_events(mastery + (fragile,))
        self.assertEqual(stale.skill("place_value").status, "fragile")

        recovery = (
            event.correct(
                ordinal=8,
                question="recovery-1",
                template="recovery-template-1",
                confidence="certain",
            ),
            event.correct(
                ordinal=9,
                question="recovery-2",
                template="recovery-template-2",
                confidence="leaning",
            ),
            event.correct(
                ordinal=10,
                question="recovery-3",
                template="recovery-template-3",
                confidence="leaning",
                transfer=True,
                changed_context=True,
            ),
        )
        recovered = reduce_events(mastery + (fragile,) + recovery)
        self.assertEqual(recovered.skill("place_value").status, "secure")

    def test_duplicate_observation_semantic_key_fails_closed(self):
        from services.wayline_forge.app.evidence_reducer import DuplicateSemanticEventError

        original = event.correct(ordinal=1, batch="same-batch", question="same-question")
        regenerated = replace(
            original,
            ordinal=2,
            event_id="regenerated-observation-id",
            idempotency_id="regenerated-request-id",
        )

        self.assertEqual(original.semantic_key, regenerated.semantic_key)
        with self.assertRaises(DuplicateSemanticEventError):
            reduce_events((original, regenerated))

    def test_replay_produces_byte_identical_state(self):
        events = event.ready_valuehold_events()

        first = reduce_events(events).canonical_bytes()
        second = reduce_events(tuple(events)).canonical_bytes()

        self.assertEqual(first, second)

    def test_projection_remembers_answers_confidence_and_explanations_shown(self):
        observation = event.wrong("align_by_ends", confidence="certain")
        state = reduce_events([observation])

        record = state.answer_records[0]
        self.assertEqual(record.first_option_id, observation.first_option_id)
        self.assertEqual(record.first_confidence, "certain")
        self.assertEqual(record.explanations_shown, observation.canonical_feedback)

    def test_assisted_answers_are_retained_without_inflating_unassisted_evidence(self):
        prior_events = (
            event.activate(ordinal=1),
            event.wrong("align_by_ends", ordinal=2, confidence="certain"),
        )
        assisted = event.assisted_completion(ordinal=3)

        before = reduce_events(prior_events)
        after = reduce_events((*prior_events, assisted))

        self.assertEqual(len(after.answer_records), len(before.answer_records) + 2)
        self.assertEqual(after.procedures, before.procedures)
        self.assertEqual(after.skills, before.skills)
        self.assertEqual(
            after.world("valuehold").valid_item_count,
            before.world("valuehold").valid_item_count,
        )
        first, second = after.answer_records[-2:]
        self.assertEqual(first.batch_id, assisted.route_id)
        self.assertEqual(first.item_id, assisted.supported_item_ids[0])
        self.assertEqual(first.question_id, assisted.supported_question_ids[0])
        self.assertEqual(first.first_confidence, "leaning")
        self.assertEqual(first.final_confidence, "leaning")
        self.assertFalse(first.first_correct)
        self.assertEqual(first.explanations_shown, assisted.canonical_feedback[0])
        self.assertEqual(second.explanations_shown, assisted.canonical_feedback[1])


if __name__ == "__main__":
    unittest.main()
