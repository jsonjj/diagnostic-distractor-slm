from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace
import tempfile
from threading import Barrier
from pathlib import Path
import unittest

from services.wayline_forge.app.contracts import PublicQuizBatch
from services.wayline_forge.app.events import (
    OUTCOME_EVENT_SCHEMA_VERSION,
    BossCompletionEvent,
    SealTrialCompletionEvent,
    SecondWindStartedEvent,
)
from services.wayline_forge.app.profile_store import ProfileStore
from services.wayline_forge.app.progression import (
    AssistedRoutePreparationRequest,
    BattleCompletionRequest,
    ProgressionCommandError,
    ProgressionCommandService,
    RevivedCombatCompletionRequest,
    SealTrialCompletionRequest,
    SealTrialPreparationRequest,
    SecondWindCompletionRequest,
    SecondWindStartRequest,
    WorldActivationRequest,
)
from services.wayline_forge.app.quiz_machine import QuizState


def _public_batch(batch_id: str, item_count: int = 3) -> PublicQuizBatch:
    return PublicQuizBatch.model_validate(
        {
            "schemaVersion": "wayline.v1",
            "batchId": batch_id,
            "itemCount": item_count,
            "items": [
                {
                    "itemId": f"item-{index}",
                    "prompt": f"Question {index}?",
                    "options": [
                        {
                            "optionId": f"item-{index}-option-{option}",
                            "displayText": str(index * 10 + option),
                        }
                        for option in range(1, 5)
                    ],
                }
                for index in range(1, item_count + 1)
            ],
        }
    )


@dataclass(frozen=True)
class _FinalResult:
    batch_id: str
    item_count: int
    final_correct_count: int
    items: tuple[object, ...]


class _QuizAuthority:
    def __init__(self) -> None:
        self.batches: dict[str, tuple[object, object]] = {}

    def add(
        self,
        batch_id: str,
        *,
        profile_id: str,
        session_id: str,
        world_id: str,
        battle_id: str,
        battle_tier: str,
        item_count: int,
        final_correct: int,
        item_ids: tuple[str, ...] | None = None,
    ) -> None:
        ids = item_ids or tuple(f"item-{index}" for index in range(1, item_count + 1))
        final = _FinalResult(
            batch_id=batch_id,
            item_count=item_count,
            final_correct_count=final_correct,
            items=tuple(
                SimpleNamespace(
                    item_id=item_id,
                    final_selection=SimpleNamespace(
                        is_correct=index <= final_correct
                    ),
                )
                for index, item_id in enumerate(ids, start=1)
            ),
        )
        machine = SimpleNamespace(
            batch_id=batch_id,
            state=QuizState.REVEALED,
            final_result=final,
        )
        context = SimpleNamespace(
            profile_id=profile_id,
            session_id=session_id,
            world_id=world_id,
            battle_id=battle_id,
            battle_tier=battle_tier,
        )
        public_batch = PublicQuizBatch.model_validate(
            {
                "schemaVersion": "wayline.v1",
                "batchId": batch_id,
                "itemCount": item_count,
                "items": [
                    {
                        "itemId": item_id,
                        "prompt": f"Question {index}?",
                        "options": [
                            {
                                "optionId": f"{item_id}-option-{option}",
                                "displayText": str(index * 10 + option),
                            }
                            for option in range(1, 5)
                        ],
                    }
                    for index, item_id in enumerate(ids, start=1)
                ],
            }
        )
        sealed_items = tuple(
            SimpleNamespace(
                item_id=item.item_id,
                correct_option_id=item.options[-1].option_id,
                correct_answer=item.options[-1].display_text,
                trusted_steps=("Use the trusted method.",),
                reliable_method="Use the trusted method.",
            )
            for item in public_batch.items
        )
        self.batches[batch_id] = (
            machine,
            SimpleNamespace(
                context=context,
                public_batch=public_batch,
                sealed_quiz=SimpleNamespace(items=sealed_items),
            ),
        )

    def drain_observations(self, profile_id: str, *, profile_store: ProfileStore) -> int:
        return 0

    def load(self, batch_id: str, *, profile_id: str) -> object:
        machine, material = self.batches[batch_id]
        if material.context.profile_id != profile_id:
            raise KeyError(batch_id)
        return machine

    def load_batch_material(self, batch_id: str, *, profile_id: str) -> object:
        machine, material = self.batches[batch_id]
        if material.context.profile_id != profile_id:
            raise KeyError(batch_id)
        return material

    def close_revealed(self, batch_id: str, *, profile_id: str) -> object:
        machine, material = self.batches[batch_id]
        if material.context.profile_id != profile_id:
            raise KeyError(batch_id)
        machine.state = QuizState.CLOSED
        return machine


class _SpecialPreparer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.outputs: dict[tuple[str, str], PublicQuizBatch] = {}

    async def prepare_seal_trial(self, request, **kwargs) -> PublicQuizBatch:
        self.calls.append(("seal_trial", request))
        key = ("seal_trial", request.request_id)
        if key not in self.outputs:
            self.outputs[key] = _public_batch(f"batch-seal-{len(self.calls)}")
        return self.outputs[key]

    async def prepare_second_wind(self, request, **kwargs) -> PublicQuizBatch:
        self.calls.append(("second_wind", request))
        key = ("second_wind", request.request_id)
        if key not in self.outputs:
            self.outputs[key] = _public_batch(
                f"batch-second-wind-{len(self.calls)}"
            )
        return self.outputs[key]


class _BarrierProfileStore(ProfileStore):
    """Release two command writers only after both reached the append seam."""

    def __init__(self, path: Path, barrier: Barrier) -> None:
        self._append_barrier = barrier
        self._append_waited = False
        super().__init__(path)

    def _wait_for_competitor(self) -> None:
        if not self._append_waited:
            self._append_waited = True
            self._append_barrier.wait(timeout=5)

    def append_progression_event(self, event: object) -> object:
        self._wait_for_competitor()
        return super().append_progression_event(event)  # type: ignore[arg-type]


class ProgressionCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "wayline.sqlite"
        self.profiles = ProfileStore(self.path)
        self.profile = self.profiles.create_profile(request_id="create-profile-001")
        self.session = self.profiles.create_session(
            request_id="create-session-001",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.quizzes = _QuizAuthority()
        self.preparer = _SpecialPreparer()
        self.service = ProgressionCommandService(
            self.profiles,
            self.quizzes,
            self.preparer,
            utc_now=lambda: datetime(2026, 7, 12, 18, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        self.profiles.close()
        self.temporary_directory.cleanup()

    def _add_quiz(
        self,
        batch_id: str,
        *,
        battle_id: str,
        tier: str,
        item_count: int,
        final_correct: int,
        item_ids: tuple[str, ...] | None = None,
    ) -> None:
        self.quizzes.add(
            batch_id,
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            world_id="valuehold",
            battle_id=battle_id,
            battle_tier=tier,
            item_count=item_count,
            final_correct=final_correct,
            item_ids=item_ids,
        )

    def _battle_request(self, request_id: str = "complete-route-001") -> BattleCompletionRequest:
        return BattleCompletionRequest(
            request_id=request_id,
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            world_id="valuehold",
            battle_id="valuehold_route_1",
            batch_id="batch-route-001",
            combat_won=True,
        )

    def test_battle_completion_derives_quiz_truth_and_exactly_replays(self) -> None:
        self._add_quiz(
            "batch-route-001",
            battle_id="valuehold_route_1",
            tier="route_1",
            item_count=3,
            final_correct=2,
        )

        first = self.service.complete_battle(self._battle_request())
        replay = self.service.complete_battle(self._battle_request())

        self.assertEqual(first, replay)
        self.assertEqual(first.final_correct, 2)
        outcomes = self.profiles.load_events(self.profile.profile_id)
        self.assertEqual(
            outcomes[-1].schema_version,
            OUTCOME_EVENT_SCHEMA_VERSION,
        )
        self.assertEqual(outcomes[-1].batch_id, "batch-route-001")

    def test_simultaneous_exact_battle_retries_return_one_stable_result(self) -> None:
        self._add_quiz(
            "batch-route-001",
            battle_id="valuehold_route_1",
            tier="route_1",
            item_count=3,
            final_correct=2,
        )
        request = self._battle_request()
        barrier = Barrier(2)

        def complete(second: int) -> tuple[str, object]:
            try:
                with _BarrierProfileStore(self.path, barrier) as profiles:
                    service = ProgressionCommandService(
                        profiles,
                        self.quizzes,
                        self.preparer,
                        utc_now=lambda: datetime(
                            2026,
                            7,
                            12,
                            18,
                            0,
                            second,
                            tzinfo=timezone.utc,
                        ),
                    )
                    return "ok", service.complete_battle(request)
            except ProgressionCommandError as error:
                return "error", error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(complete, (1, 2)))

        self.assertEqual(
            tuple(status for status, _value in outcomes),
            ("ok", "ok"),
            outcomes,
        )
        self.assertEqual(outcomes[0][1], outcomes[1][1])
        completions = tuple(
            event
            for event in self.profiles.load_events(self.profile.profile_id)
            if event.event_type == "battle_completion"
        )
        self.assertEqual(len(completions), 1)
        self.assertIs(
            self.quizzes.load(
                "batch-route-001",
                profile_id=self.profile.profile_id,
            ).state,
            QuizState.CLOSED,
        )

    def test_retry_recovers_interruption_between_append_and_quiz_close(self) -> None:
        self._add_quiz(
            "batch-route-001",
            battle_id="valuehold_route_1",
            tier="route_1",
            item_count=3,
            final_correct=2,
        )
        close_revealed = self.quizzes.close_revealed
        close_calls = 0

        def interrupt_once(batch_id: str, *, profile_id: str) -> object:
            nonlocal close_calls
            close_calls += 1
            if close_calls == 1:
                raise RuntimeError("injected interruption after event append")
            return close_revealed(batch_id, profile_id=profile_id)

        self.quizzes.close_revealed = interrupt_once  # type: ignore[method-assign]
        with self.assertRaises(ProgressionCommandError) as interrupted:
            self.service.complete_battle(self._battle_request())
        self.assertEqual(interrupted.exception.code, "integrity_failure")

        completions_after_interrupt = tuple(
            event
            for event in self.profiles.load_events(self.profile.profile_id)
            if event.event_type == "battle_completion"
        )
        self.assertEqual(len(completions_after_interrupt), 1)
        self.assertIs(
            self.quizzes.load(
                "batch-route-001",
                profile_id=self.profile.profile_id,
            ).state,
            QuizState.REVEALED,
        )

        recovered = self.service.complete_battle(self._battle_request())

        self.assertEqual(recovered.request_id, "complete-route-001")
        self.assertEqual(close_calls, 2)
        self.assertEqual(
            tuple(
                event
                for event in self.profiles.load_events(self.profile.profile_id)
                if event.event_type == "battle_completion"
            ),
            completions_after_interrupt,
        )
        self.assertIs(
            self.quizzes.load(
                "batch-route-001",
                profile_id=self.profile.profile_id,
            ).state,
            QuizState.CLOSED,
        )

    def test_exact_battle_replay_does_not_drift_with_later_world_state(self) -> None:
        self._add_quiz(
            "batch-route-001",
            battle_id="valuehold_route_1",
            tier="route_1",
            item_count=3,
            final_correct=2,
        )
        first = self.service.complete_battle(self._battle_request())
        self.profiles.append(
            BossCompletionEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="later-boss-completion",
                idempotency_id="later-boss-completion-request",
                ordinal=3,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id="valuehold_boss",
                occurred_at="2026-07-12T19:00:00Z",
                combat_won=True,
                final_correct=6,
                item_count=8,
                is_campaign_finale=False,
                batch_id="batch-later-boss",
            )
        )

        replay = self.service.complete_battle(self._battle_request())

        self.assertEqual(replay, first)

    def test_new_request_for_completed_battle_is_a_stable_conflict(self) -> None:
        self._add_quiz(
            "batch-route-001",
            battle_id="valuehold_route_1",
            tier="route_1",
            item_count=3,
            final_correct=2,
        )
        self.service.complete_battle(self._battle_request())

        with self.assertRaises(ProgressionCommandError) as raised:
            self.service.complete_battle(self._battle_request("complete-route-002"))

        self.assertEqual(raised.exception.code, "target_already_completed")
        self.assertEqual(raised.exception.http_status, 409)

    def _append_missed_boss(self, *, ordinal: int = 2) -> None:
        self.profiles.append(
            BossCompletionEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id=f"boss-completion-{ordinal}",
                idempotency_id=f"boss-completion-request-{ordinal}",
                ordinal=ordinal,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id="valuehold_boss",
                occurred_at="2026-07-12T17:00:00Z",
                combat_won=True,
                final_correct=5,
                item_count=8,
                is_campaign_finale=False,
                batch_id="batch-boss-missed",
            )
        )

    def test_seal_trial_preparation_is_authorized_and_two_of_three_passes(self) -> None:
        self._append_missed_boss()
        prepared = asyncio.run(
            self.service.prepare_seal_trial(
                SealTrialPreparationRequest(
                    request_id="prepare-seal-001",
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                    world_id="valuehold",
                )
            )
        )
        self.assertEqual(prepared.attempt_number, 1)
        self.assertEqual(prepared.batch.item_count, 3)
        self._add_quiz(
            prepared.batch.batch_id,
            battle_id=prepared.battle_id,
            tier="seal_trial",
            item_count=3,
            final_correct=2,
        )

        completion_request = SealTrialCompletionRequest(
            request_id="complete-seal-001",
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            world_id="valuehold",
            batch_id=prepared.batch.batch_id,
        )
        result = self.service.complete_seal_trial(completion_request)
        replay = self.service.complete_seal_trial(completion_request)

        self.assertTrue(result.passed)
        self.assertTrue(result.world_cleared)
        self.assertEqual(result.final_correct, 2)
        self.assertEqual(replay, result)
        self.assertIsInstance(
            self.profiles.load_events(self.profile.profile_id)[-1],
            SealTrialCompletionEvent,
        )

    def test_seal_trial_replay_recomputes_gate_recheck_receipt(self) -> None:
        self._append_missed_boss()
        self._add_quiz(
            "batch-seal-invalid-gate-recheck",
            battle_id="valuehold_seal_trial_1",
            tier="seal_trial",
            item_count=3,
            final_correct=2,
        )
        self.profiles.append(
            SealTrialCompletionEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="seal-invalid-gate-recheck",
                idempotency_id="complete-seal-invalid-gate-recheck",
                ordinal=3,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id="valuehold_seal_trial_1",
                occurred_at="2026-07-12T17:01:00Z",
                attempt_number=1,
                passed=True,
                final_correct=2,
                item_count=3,
                batch_id="batch-seal-invalid-gate-recheck",
                gate_recheck_sha256="0" * 64,
            )
        )

        with self.assertRaises(ProgressionCommandError) as invalid_receipt:
            self.service.complete_seal_trial(
                SealTrialCompletionRequest(
                    request_id="complete-seal-invalid-gate-recheck",
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                    world_id="valuehold",
                    batch_id="batch-seal-invalid-gate-recheck",
                )
            )

        self.assertEqual(invalid_receipt.exception.code, "integrity_failure")

    def test_assisted_route_cannot_fall_back_to_reused_seal_material(self) -> None:
        self._append_missed_boss()
        for attempt, ordinal in ((1, 3), (2, 4)):
            self.profiles.append(
                SealTrialCompletionEvent(
                    schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                    event_id=f"seal-miss-{attempt}",
                    idempotency_id=f"seal-miss-request-{attempt}",
                    ordinal=ordinal,
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                    world_id="valuehold",
                    battle_id=f"valuehold_seal_trial_{attempt}",
                    occurred_at=f"2026-07-12T17:0{attempt}:00Z",
                    attempt_number=attempt,
                    passed=False,
                    final_correct=1,
                    item_count=3,
                    batch_id=f"batch-seal-miss-{attempt}",
                    gate_recheck_sha256="a" * 64,
                )
            )
        with self.assertRaises(ProgressionCommandError) as unavailable:
            asyncio.run(self.service.prepare_assisted_route(
                AssistedRoutePreparationRequest(
                    request_id="prepare-assisted-001",
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                    world_id="valuehold",
                )
            ))

        self.assertEqual(unavailable.exception.code, "integrity_failure")

    def test_second_wind_resumes_until_combat_end_and_uses_capped_shield(self) -> None:
        started = asyncio.run(
            self.service.start_second_wind(
                SecondWindStartRequest(
                    request_id="start-second-wind-001",
                    preparation_request_id="prepare-second-wind-001",
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                    world_id="valuehold",
                    battle_id="valuehold_route_1",
                    combat_attempt_id="combat-attempt-001",
                )
            )
        )
        self._add_quiz(
            started.batch.batch_id,
            battle_id=started.quiz_battle_id,
            tier="seal_trial",
            item_count=3,
            final_correct=3,
        )
        completed = self.service.complete_second_wind(
            SecondWindCompletionRequest(
                request_id="complete-second-wind-001",
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                second_wind_id=started.second_wind_id,
                batch_id=started.batch.batch_id,
            )
        )

        self.assertEqual(completed.revive_health_percent, 35)
        self.assertEqual(completed.shield_percent, 15)
        resumed = asyncio.run(
            self.service.start_second_wind(
                SecondWindStartRequest(
                    request_id="start-second-wind-001",
                    preparation_request_id="prepare-second-wind-001",
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                    world_id="valuehold",
                    battle_id="valuehold_route_1",
                    combat_attempt_id="combat-attempt-001",
                )
            )
        )
        self.assertEqual(resumed, started)

        ended = self.service.complete_revived_combat(
            RevivedCombatCompletionRequest(
                request_id="end-revived-combat-001",
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                second_wind_id=started.second_wind_id,
                combat_attempt_id="combat-attempt-001",
                combat_won=True,
            )
        )
        self.assertTrue(ended.battle_completed)
        with self.assertRaises(ProgressionCommandError) as raised:
            asyncio.run(
                self.service.start_second_wind(
                    SecondWindStartRequest(
                        request_id="start-second-wind-001",
                        preparation_request_id="prepare-second-wind-001",
                        profile_id=self.profile.profile_id,
                        session_id=self.session.session_id,
                        world_id="valuehold",
                        battle_id="valuehold_route_1",
                        combat_attempt_id="combat-attempt-001",
                    )
                )
            )
        self.assertEqual(raised.exception.code, "target_already_completed")

    def test_overlapping_second_wind_requests_share_one_combat_attempt(self) -> None:
        barrier = Barrier(2)

        def start(index: int) -> tuple[str, object]:
            request = SecondWindStartRequest(
                request_id=f"start-second-wind-overlap-{index}",
                preparation_request_id=f"prepare-second-wind-overlap-{index}",
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id="valuehold_route_1",
                combat_attempt_id="combat-attempt-overlap-001",
            )
            try:
                with _BarrierProfileStore(self.path, barrier) as profiles:
                    service = ProgressionCommandService(
                        profiles,
                        self.quizzes,
                        self.preparer,
                        utc_now=lambda: datetime(
                            2026,
                            7,
                            12,
                            18,
                            1,
                            index,
                            tzinfo=timezone.utc,
                        ),
                    )
                    return "ok", asyncio.run(service.start_second_wind(request))
            except ProgressionCommandError as error:
                return "error", error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(start, (1, 2)))

        self.assertEqual(
            sorted(status for status, _value in outcomes),
            ["error", "ok"],
            outcomes,
        )
        self.assertEqual(
            next(value for status, value in outcomes if status == "error"),
            "target_in_progress",
        )
        started = tuple(
            event
            for event in self.profiles.load_events(self.profile.profile_id)
            if isinstance(event, SecondWindStartedEvent)
        )
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].combat_attempt_id, "combat-attempt-overlap-001")
        self.assertEqual(
            tuple(kind for kind, _request in self.preparer.calls),
            ("second_wind",),
        )

    def test_world_activation_requires_clear_and_exactly_replays(self) -> None:
        self._append_missed_boss()
        self.profiles.append(
            SealTrialCompletionEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="seal-pass-001",
                idempotency_id="seal-pass-request-001",
                ordinal=3,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id="valuehold_seal_trial_1",
                occurred_at="2026-07-12T17:01:00Z",
                attempt_number=1,
                passed=True,
                final_correct=2,
                item_count=3,
                batch_id="batch-seal-pass-001",
                gate_recheck_sha256="b" * 64,
            )
        )
        request = WorldActivationRequest(
            request_id="activate-decimara-001",
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            completed_world_id="valuehold",
            next_world_id="decimara",
        )

        first = self.service.activate_world(request)
        replay = self.service.activate_world(request)

        self.assertEqual(first, replay)
        self.assertEqual(first.active_world_id, "decimara")
        with self.assertRaises(ProgressionCommandError) as raised:
            self.service.activate_world(
                WorldActivationRequest(
                    request_id="activate-decimara-001",
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                    completed_world_id="decimara",
                    next_world_id="decimara",
                )
            )
        self.assertEqual(raised.exception.code, "idempotency_conflict")


if __name__ == "__main__":
    unittest.main()
