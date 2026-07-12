from __future__ import annotations

from dataclasses import replace
import unittest

from services.wayline_forge.app.quiz_machine import (
    IdempotencyConflictError,
    InitialAlreadySubmittedError,
    InvalidQuizTransitionError,
    QuizItemLayout,
    QuizSelection,
    QuizState,
    QuizSubmission,
    ResultNotRevealedError,
    RevisionAlreadyUsedError,
    SealedQuiz,
    SealedQuizItem,
    StaleQuizStateError,
    SubmissionValidationError,
    close_quiz,
    lock_initial,
    mark_ready,
    new_quiz,
    resolve_initial,
    revealed_result,
    submit_initial,
    submit_revision,
)


class QuizMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layouts = tuple(
            QuizItemLayout(
                item_id=f"item-{number}",
                option_ids=tuple(
                    f"item-{number}-option-{letter}"
                    for letter in ("a", "b", "c", "d")
                ),
            )
            for number in range(1, 4)
        )
        self.sealed = SealedQuiz(
            batch_id="batch-001",
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
                for number, layout in enumerate(self.layouts, 1)
            ),
        )
        self.preparing = new_quiz("batch-001", self.layouts)
        self.ready = mark_ready(
            self.preparing,
            sealed_quiz=self.sealed,
            expected_version=0,
        )

    def submission(
        self,
        request_id: str,
        choices: tuple[int, int, int] = (0, 0, 0),
        confidences: tuple[str, str, str] = ("certain", "leaning", "guessing"),
    ) -> QuizSubmission:
        return QuizSubmission(
            schema_version="wayline.v1",
            request_id=request_id,
            batch_id="batch-001",
            item_count=3,
            selections=tuple(
                QuizSelection(
                    item_id=layout.item_id,
                    option_id=layout.option_ids[choice],
                    confidence=confidence,
                )
                for layout, choice, confidence in zip(
                    self.layouts,
                    choices,
                    confidences,
                    strict=True,
                )
            ),
        )

    def seal_with_first_item(self, **changes: object) -> SealedQuiz:
        return replace(
            self.sealed,
            items=(replace(self.sealed.items[0], **changes), *self.sealed.items[1:]),
        )

    def rekeyed_seal_with_same_initial_wrong_count(self) -> SealedQuiz:
        correct_indexes = (1, 2, 1)
        items = []
        for number, (layout, item, correct_index) in enumerate(
            zip(self.layouts, self.sealed.items, correct_indexes, strict=True),
            1,
        ):
            correct_option_id = layout.option_ids[correct_index]
            items.append(replace(
                item,
                correct_option_id=correct_option_id,
                correct_answer=f"alternate-answer-{number}",
                trusted_steps=(f"alternate-step-{number}",),
                possible_errors=tuple(
                    (option_id, f"alternate-error-{number}-{index}")
                    for index, option_id in enumerate(layout.option_ids, 1)
                    if option_id != correct_option_id
                ),
                reliable_method=f"alternate-method-{number}",
            ))
        return replace(self.sealed, items=tuple(items))

    def seal_content_variants(self) -> tuple[SealedQuiz, ...]:
        first = self.sealed.items[0]
        changed_errors = list(first.possible_errors)
        changed_errors[0] = (changed_errors[0][0], "changed-possible-error")
        return (
            self.seal_with_first_item(correct_answer="changed-answer"),
            self.seal_with_first_item(trusted_steps=("changed-trusted-step",)),
            self.seal_with_first_item(possible_errors=tuple(changed_errors)),
            self.seal_with_first_item(reliable_method="changed-reliable-method"),
            self.rekeyed_seal_with_same_initial_wrong_count(),
        )

    @staticmethod
    def wrong_count(sealed: SealedQuiz, submission: QuizSubmission) -> int:
        key = {item.item_id: item.correct_option_id for item in sealed.items}
        return sum(
            selection.option_id != key[selection.item_id]
            for selection in submission.selections
        )

    def test_explicit_preparing_and_ready_states_are_immutable(self):
        self.assertEqual(self.preparing.state, QuizState.PREPARING)
        self.assertEqual(self.preparing.version, 0)
        self.assertIsNone(self.preparing.sealed_quiz_sha256)
        self.assertEqual(self.ready.state, QuizState.READY)
        self.assertEqual(self.ready.version, 1)
        self.assertEqual(len(self.ready.sealed_quiz_sha256), 64)
        self.assertFalse(hasattr(self.ready, "sealed_quiz"))
        self.assertEqual(self.preparing.state, QuizState.PREPARING)
        for secret in (
            "item-1-option-a",
            "answer-1",
            "trusted-step-1",
            "possible-error-1-1",
        ):
            self.assertNotIn(secret, repr(self.ready))

    def test_only_preparing_can_transition_to_ready(self):
        with self.assertRaises(InvalidQuizTransitionError):
            mark_ready(
                self.ready,
                sealed_quiz=self.sealed,
                expected_version=self.ready.version,
            )

    def test_seal_commitment_binds_all_key_and_feedback_content(self):
        for altered_seal in self.seal_content_variants():
            with self.subTest(altered_seal=altered_seal):
                altered_ready = mark_ready(
                    new_quiz("batch-001", self.layouts),
                    sealed_quiz=altered_seal,
                    expected_version=0,
                )
                self.assertNotEqual(
                    altered_ready.sealed_quiz_sha256,
                    self.ready.sealed_quiz_sha256,
                )

    def test_seal_commitment_is_canonical_across_container_order(self):
        reordered = replace(
            self.sealed,
            items=tuple(
                replace(item, possible_errors=tuple(reversed(item.possible_errors)))
                for item in reversed(self.sealed.items)
            ),
        )

        reordered_ready = mark_ready(
            new_quiz("batch-001", self.layouts),
            sealed_quiz=reordered,
            expected_version=0,
        )

        self.assertEqual(
            reordered_ready.sealed_quiz_sha256,
            self.ready.sealed_quiz_sha256,
        )

    def test_changed_seal_fails_initial_and_locked_resolution(self):
        payload = self.submission("initial-001", choices=(1, 0, 0))
        changed_seal = self.seal_content_variants()[0]

        with self.assertRaises(SubmissionValidationError):
            submit_initial(
                self.ready,
                payload,
                changed_seal,
                expected_version=self.ready.version,
            )

        locked = lock_initial(
            self.ready,
            payload,
            expected_version=self.ready.version,
        )
        with self.assertRaises(SubmissionValidationError):
            resolve_initial(
                locked,
                changed_seal,
                expected_version=locked.version,
            )

    def test_idempotent_replays_still_validate_the_seal_commitment(self):
        initial_payload = self.submission("initial-001", choices=(1, 0, 0))
        initial = submit_initial(
            self.ready,
            initial_payload,
            self.sealed,
            expected_version=self.ready.version,
        )
        changed_seal = self.seal_content_variants()[0]

        with self.assertRaises(SubmissionValidationError):
            submit_initial(
                initial.machine,
                initial_payload,
                changed_seal,
                expected_version=self.ready.version,
            )

        revision_payload = self.submission("revision-001")
        revision = submit_revision(
            initial.machine,
            revision_payload,
            self.sealed,
            expected_version=initial.machine.version,
        )
        with self.assertRaises(SubmissionValidationError):
            submit_revision(
                revision.machine,
                revision_payload,
                changed_seal,
                expected_version=initial.machine.version,
            )

    def test_layout_identifiers_follow_the_frozen_public_contract(self):
        with self.assertRaises(SubmissionValidationError):
            new_quiz("x", self.layouts)

        invalid_layout = replace(self.layouts[0], option_ids=(
            "item-1-option-a",
            "item-1-option-b",
            "item-1-option-c",
            "option with spaces",
        ))
        with self.assertRaises(SubmissionValidationError):
            new_quiz("batch-001", (invalid_layout, *self.layouts[1:]))

    def test_initial_can_be_locked_then_resolved_after_interruption(self):
        submission = self.submission("initial-001", choices=(1, 0, 0))

        locked = lock_initial(
            self.ready,
            submission,
            expected_version=self.ready.version,
        )

        self.assertEqual(locked.state, QuizState.INITIAL_LOCKED)
        self.assertEqual(locked.version, 2)
        with self.assertRaises(ResultNotRevealedError):
            revealed_result(locked)

        resolved = resolve_initial(
            locked,
            self.sealed,
            expected_version=locked.version,
        )
        self.assertEqual(resolved.machine.state, QuizState.REVISION_OPEN)
        self.assertEqual(resolved.machine.version, 3)
        self.assertEqual(resolved.public_result.wrong_count, 1)

    def test_nonzero_initial_returns_only_exact_count_and_opens_one_revision(self):
        transition = submit_initial(
            self.ready,
            self.submission("initial-001", choices=(1, 0, 2)),
            self.sealed,
            expected_version=self.ready.version,
        )

        self.assertEqual(transition.machine.state, QuizState.REVISION_OPEN)
        self.assertEqual(
            transition.public_result.to_public_dict(),
            {
                "schemaVersion": "wayline.v1",
                "batchId": "batch-001",
                "itemCount": 3,
                "wrongCount": 2,
                "revisionRequired": True,
            },
        )
        self.assertNotIn("item-1", repr(transition.public_result))
        with self.assertRaises(ResultNotRevealedError):
            revealed_result(transition.machine)

    def test_zero_wrong_skips_revision_and_reveals_then_closes(self):
        transition = submit_initial(
            self.ready,
            self.submission("initial-001"),
            self.sealed,
            expected_version=self.ready.version,
        )

        self.assertEqual(transition.machine.state, QuizState.REVEALED)
        self.assertFalse(transition.public_result.revision_required)
        final = revealed_result(transition.machine)
        self.assertEqual(final.first_pass_wrong_count, 0)
        self.assertEqual(final.final_correct_count, 3)
        self.assertFalse(final.revision_used)
        self.assertTrue(all(item.first_selection == item.final_selection for item in final.items))

        closed = close_quiz(
            transition.machine,
            expected_version=transition.machine.version,
        )
        self.assertEqual(closed.state, QuizState.CLOSED)
        self.assertEqual(revealed_result(closed), final)

    def test_revision_reveals_immutable_first_pass_and_final_results(self):
        initial = submit_initial(
            self.ready,
            self.submission(
                "initial-001",
                choices=(1, 0, 2),
                confidences=("guessing", "certain", "leaning"),
            ),
            self.sealed,
            expected_version=self.ready.version,
        )
        revision_payload = self.submission(
            "revision-001",
            choices=(0, 0, 3),
            confidences=("certain", "certain", "guessing"),
        )

        revision = submit_revision(
            initial.machine,
            revision_payload,
            self.sealed,
            expected_version=initial.machine.version,
        )

        self.assertEqual(revision.machine.state, QuizState.REVEALED)
        self.assertEqual(revision.final_result.first_pass_wrong_count, 2)
        self.assertEqual(revision.final_result.final_correct_count, 2)
        self.assertTrue(revision.final_result.revision_used)
        first = revision.final_result.items[0]
        self.assertEqual(first.first_selection.option_id, "item-1-option-b")
        self.assertEqual(first.first_selection.confidence, "guessing")
        self.assertFalse(first.first_selection.is_correct)
        self.assertEqual(first.final_selection.option_id, "item-1-option-a")
        self.assertEqual(first.final_selection.confidence, "certain")
        self.assertTrue(first.final_selection.is_correct)
        self.assertTrue(first.self_corrected)

    def test_wrong_to_correct_retains_first_route_teaching_note(self):
        initial = submit_initial(
            self.ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            self.sealed,
            expected_version=self.ready.version,
        )

        revision = submit_revision(
            initial.machine,
            self.submission("revision-001", choices=(0, 0, 0)),
            self.sealed,
            expected_version=initial.machine.version,
        )

        self.assertEqual(
            revision.final_result.items[0].possible_error,
            "possible-error-1-1",
        )

    def test_wrong_to_different_wrong_uses_final_route_teaching_note(self):
        initial = submit_initial(
            self.ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            self.sealed,
            expected_version=self.ready.version,
        )

        revision = submit_revision(
            initial.machine,
            self.submission("revision-001", choices=(2, 0, 0)),
            self.sealed,
            expected_version=initial.machine.version,
        )

        self.assertEqual(
            revision.final_result.items[0].possible_error,
            "possible-error-1-2",
        )

    def test_correct_to_wrong_uses_final_route_teaching_note(self):
        initial = submit_initial(
            self.ready,
            self.submission("initial-001", choices=(0, 1, 0)),
            self.sealed,
            expected_version=self.ready.version,
        )

        revision = submit_revision(
            initial.machine,
            self.submission("revision-001", choices=(2, 0, 0)),
            self.sealed,
            expected_version=initial.machine.version,
        )

        self.assertEqual(
            revision.final_result.items[0].possible_error,
            "possible-error-1-2",
        )

    def test_correct_to_correct_has_no_possible_error(self):
        initial = submit_initial(
            self.ready,
            self.submission("initial-001", choices=(0, 1, 0)),
            self.sealed,
            expected_version=self.ready.version,
        )

        revision = submit_revision(
            initial.machine,
            self.submission("revision-001", choices=(0, 0, 0)),
            self.sealed,
            expected_version=initial.machine.version,
        )

        self.assertIsNone(revision.final_result.items[0].possible_error)

    def test_altered_revision_key_with_same_initial_wrong_count_fails_closed(self):
        initial_payload = self.submission("initial-001", choices=(1, 1, 0))
        altered_seal = self.rekeyed_seal_with_same_initial_wrong_count()
        self.assertEqual(self.wrong_count(self.sealed, initial_payload), 2)
        self.assertEqual(self.wrong_count(altered_seal, initial_payload), 2)
        initial = submit_initial(
            self.ready,
            initial_payload,
            self.sealed,
            expected_version=self.ready.version,
        )

        with self.assertRaises(SubmissionValidationError):
            submit_revision(
                initial.machine,
                self.submission("revision-001"),
                altered_seal,
                expected_version=initial.machine.version,
            )

    def test_same_initial_request_and_payload_replays_deterministic_receipt(self):
        payload = self.submission("initial-001", choices=(1, 0, 0))
        first = submit_initial(
            self.ready,
            payload,
            self.sealed,
            expected_version=self.ready.version,
        )

        replay = submit_initial(
            first.machine,
            payload,
            self.sealed,
            expected_version=self.ready.version,
        )

        self.assertEqual(replay.receipt, first.receipt)
        self.assertEqual(replay.public_result, first.public_result)
        self.assertEqual(replay.machine, first.machine)

    def test_retry_of_exact_locked_initial_finishes_without_a_second_lock(self):
        payload = self.submission("initial-001", choices=(1, 0, 0))
        locked = lock_initial(
            self.ready,
            payload,
            expected_version=self.ready.version,
        )

        recovered = submit_initial(
            locked,
            payload,
            self.sealed,
            expected_version=self.ready.version,
        )

        self.assertEqual(recovered.machine.state, QuizState.REVISION_OPEN)
        self.assertEqual(recovered.machine.version, 3)

    def test_same_initial_request_with_changed_payload_fails_closed(self):
        original = self.submission("initial-001", choices=(1, 0, 0))
        first = submit_initial(
            self.ready,
            original,
            self.sealed,
            expected_version=self.ready.version,
        )
        conflict = self.submission("initial-001", choices=(2, 0, 0))

        with self.assertRaises(IdempotencyConflictError):
            submit_initial(
                first.machine,
                conflict,
                self.sealed,
                expected_version=first.machine.version,
            )
        self.assertEqual(first.machine.state, QuizState.REVISION_OPEN)

    def test_different_second_initial_request_fails_closed(self):
        first = submit_initial(
            self.ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            self.sealed,
            expected_version=self.ready.version,
        )

        with self.assertRaises(InitialAlreadySubmittedError):
            submit_initial(
                first.machine,
                self.submission("initial-002", choices=(1, 0, 0)),
                self.sealed,
                expected_version=first.machine.version,
            )

    def test_same_revision_request_and_payload_replays_even_after_close(self):
        initial = submit_initial(
            self.ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            self.sealed,
            expected_version=self.ready.version,
        )
        payload = self.submission("revision-001")
        first = submit_revision(
            initial.machine,
            payload,
            self.sealed,
            expected_version=initial.machine.version,
        )
        closed = close_quiz(
            first.machine,
            expected_version=first.machine.version,
        )

        replay = submit_revision(
            closed,
            payload,
            self.sealed,
            expected_version=initial.machine.version,
        )

        self.assertEqual(replay.receipt, first.receipt)
        self.assertEqual(replay.final_result, first.final_result)
        self.assertEqual(replay.machine, closed)

    def test_changed_duplicate_or_new_second_revision_fails_closed(self):
        initial = submit_initial(
            self.ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            self.sealed,
            expected_version=self.ready.version,
        )
        first = submit_revision(
            initial.machine,
            self.submission("revision-001"),
            self.sealed,
            expected_version=initial.machine.version,
        )

        with self.assertRaises(IdempotencyConflictError):
            submit_revision(
                first.machine,
                self.submission("revision-001", choices=(0, 1, 0)),
                self.sealed,
                expected_version=first.machine.version,
            )
        with self.assertRaises(RevisionAlreadyUsedError):
            submit_revision(
                first.machine,
                self.submission("revision-002"),
                self.sealed,
                expected_version=first.machine.version,
            )

    def test_stale_expected_version_fails_before_mutation(self):
        with self.assertRaises(StaleQuizStateError):
            submit_initial(
                self.ready,
                self.submission("initial-001"),
                self.sealed,
                expected_version=0,
            )
        self.assertEqual(self.ready.state, QuizState.READY)
        self.assertEqual(self.ready.version, 1)

    def test_incomplete_duplicate_or_tampered_selections_fail_closed(self):
        valid = self.submission("initial-001")
        incomplete = replace(valid, item_count=2, selections=valid.selections[:2])
        duplicate = replace(
            valid,
            selections=(valid.selections[0], valid.selections[0], valid.selections[2]),
        )
        tampered = replace(
            valid,
            selections=(
                replace(valid.selections[0], option_id="forged-option"),
                valid.selections[1],
                valid.selections[2],
            ),
        )

        for payload in (incomplete, duplicate, tampered):
            with self.subTest(payload=payload):
                with self.assertRaises(SubmissionValidationError):
                    submit_initial(
                        self.ready,
                        payload,
                        self.sealed,
                        expected_version=self.ready.version,
                    )

    def test_mismatched_or_invalid_sealed_key_fails_closed(self):
        wrong_batch = replace(self.sealed, batch_id="batch-other")
        invalid_item = replace(
            self.sealed.items[0],
            correct_option_id="unknown-correct-option",
        )
        invalid_key = replace(
            self.sealed,
            items=(invalid_item, *self.sealed.items[1:]),
        )

        for sealed in (wrong_batch, invalid_key):
            with self.subTest(sealed=sealed):
                with self.assertRaises(SubmissionValidationError):
                    submit_initial(
                        self.ready,
                        self.submission("initial-001"),
                        sealed,
                        expected_version=self.ready.version,
                    )

    def test_reveal_material_outside_public_contract_limits_fails_closed(self):
        too_many_steps = replace(
            self.sealed.items[0],
            trusted_steps=tuple(f"step-{index}" for index in range(9)),
        )
        long_answer = replace(
            self.sealed.items[0],
            correct_answer="a" * 257,
        )

        for invalid_item in (too_many_steps, long_answer):
            with self.subTest(invalid_item=invalid_item):
                invalid_sealed = replace(
                    self.sealed,
                    items=(invalid_item, *self.sealed.items[1:]),
                )
                with self.assertRaises(SubmissionValidationError):
                    submit_initial(
                        self.ready,
                        self.submission("initial-001"),
                        invalid_sealed,
                        expected_version=self.ready.version,
                    )

    def test_answer_key_and_item_correctness_are_absent_before_reveal(self):
        initial = submit_initial(
            self.ready,
            self.submission("initial-001", choices=(1, 0, 0)),
            self.sealed,
            expected_version=self.ready.version,
        )

        machine_text = repr(initial.machine)
        public_text = repr(initial.public_result.to_public_dict())
        for secret in (
            "item-1-option-a",
            "answer-1",
            "trusted-step-1",
            "possible-error-1-1",
            "isCorrect",
            "correctOptionId",
        ):
            self.assertNotIn(secret, machine_text)
            self.assertNotIn(secret, public_text)

    def test_revision_is_unavailable_for_zero_wrong_or_before_initial(self):
        zero_wrong = submit_initial(
            self.ready,
            self.submission("initial-001"),
            self.sealed,
            expected_version=self.ready.version,
        )

        for machine in (self.ready, zero_wrong.machine):
            with self.subTest(state=machine.state):
                with self.assertRaises(InvalidQuizTransitionError):
                    submit_revision(
                        machine,
                        self.submission("revision-001"),
                        self.sealed,
                        expected_version=machine.version,
                    )

    def test_only_revealed_can_close(self):
        for machine in (self.preparing, self.ready):
            with self.subTest(state=machine.state):
                with self.assertRaises(InvalidQuizTransitionError):
                    close_quiz(machine, expected_version=machine.version)


if __name__ == "__main__":
    unittest.main()
