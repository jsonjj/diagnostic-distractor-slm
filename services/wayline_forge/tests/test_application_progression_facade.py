from __future__ import annotations

import unittest

from services.wayline_forge.app.application import WaylineApplication
from services.wayline_forge.app.contracts import AssistedSelection
from services.wayline_forge.app.progression import (
    AssistedRouteCompletionRequest,
    AssistedRoutePreparationRequest,
    BattleCompletionRequest,
    RevivedCombatCompletionRequest,
    SealTrialCompletionRequest,
    SealTrialPreparationRequest,
    SecondWindCompletionRequest,
    SecondWindStartRequest,
    WorldActivationRequest,
)


class _ProgressionSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object]] = []

    def _record(self, method: str, request: object) -> object:
        result = object()
        self.calls.append((method, request, result))
        return result

    def complete_battle(self, request: object) -> object:
        return self._record("complete_battle", request)

    async def prepare_seal_trial(self, request: object) -> object:
        return self._record("prepare_seal_trial", request)

    def complete_seal_trial(self, request: object) -> object:
        return self._record("complete_seal_trial", request)

    async def prepare_assisted_route(self, request: object) -> object:
        return self._record("prepare_assisted_route", request)

    def complete_assisted_route(self, request: object) -> object:
        return self._record("complete_assisted_route", request)

    async def start_second_wind(self, request: object) -> object:
        return self._record("start_second_wind", request)

    def complete_second_wind(self, request: object) -> object:
        return self._record("complete_second_wind", request)

    def complete_revived_combat(self, request: object) -> object:
        return self._record("complete_revived_combat", request)

    def activate_world(self, request: object) -> object:
        return self._record("activate_world", request)


class ProgressionFacadeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.spy = _ProgressionSpy()
        self.application = object.__new__(WaylineApplication)
        self.application._progression = self.spy  # type: ignore[attr-defined]

    def _assert_last_call(
        self,
        method: str,
        request: object,
        result: object,
    ) -> None:
        self.assertEqual(self.spy.calls[-1][0], method)
        self.assertIs(self.spy.calls[-1][1], request)
        self.assertIs(result, self.spy.calls[-1][2])

    def test_sync_progression_commands_delegate_without_new_policy(self) -> None:
        selections = (
            AssistedSelection(
                itemId="item-assisted-001",
                optionId="option-assisted-001",
                confidence="certain",
            ),
            AssistedSelection(
                itemId="item-assisted-002",
                optionId="option-assisted-002",
                confidence="leaning",
            ),
        )
        cases = (
            (
                "complete_battle",
                BattleCompletionRequest(
                    "complete-battle-001",
                    "profile-001",
                    "session-001",
                    "valuehold",
                    "valuehold_route_1",
                    "batch-route-001",
                    True,
                ),
            ),
            (
                "complete_seal_trial",
                SealTrialCompletionRequest(
                    "complete-seal-001",
                    "profile-001",
                    "session-001",
                    "valuehold",
                    "batch-seal-001",
                ),
            ),
            (
                "complete_assisted_route",
                AssistedRouteCompletionRequest(
                    "complete-assisted-001",
                    "profile-001",
                    "session-001",
                    "valuehold",
                    "assisted-route-001",
                    selections,
                ),
            ),
            (
                "complete_second_wind",
                SecondWindCompletionRequest(
                    "complete-second-wind-001",
                    "profile-001",
                    "session-001",
                    "second-wind-001",
                    "batch-second-wind-001",
                ),
            ),
            (
                "complete_revived_combat",
                RevivedCombatCompletionRequest(
                    "complete-revived-combat-001",
                    "profile-001",
                    "session-001",
                    "second-wind-001",
                    "combat-attempt-001",
                    True,
                ),
            ),
            (
                "activate_world",
                WorldActivationRequest(
                    "activate-world-001",
                    "profile-001",
                    "session-001",
                    "valuehold",
                    "decimara",
                ),
            ),
        )

        for method, request in cases:
            with self.subTest(method=method):
                result = getattr(self.application, method)(request)
                self._assert_last_call(method, request, result)

    async def test_async_progression_commands_delegate_without_new_policy(self) -> None:
        assisted_request = AssistedRoutePreparationRequest(
            "prepare-assisted-001",
            "profile-001",
            "session-001",
            "valuehold",
        )
        assisted_result = await self.application.prepare_assisted_route(
            assisted_request
        )
        self._assert_last_call(
            "prepare_assisted_route",
            assisted_request,
            assisted_result,
        )

        seal_request = SealTrialPreparationRequest(
            "prepare-seal-001",
            "profile-001",
            "session-001",
            "valuehold",
        )
        seal_result = await self.application.prepare_seal_trial(seal_request)
        self._assert_last_call("prepare_seal_trial", seal_request, seal_result)

        second_wind_request = SecondWindStartRequest(
            "start-second-wind-001",
            "prepare-second-wind-001",
            "profile-001",
            "session-001",
            "valuehold",
            "valuehold_route_1",
            "combat-attempt-001",
        )
        second_wind_result = await self.application.start_second_wind(
            second_wind_request
        )
        self._assert_last_call(
            "start_second_wind",
            second_wind_request,
            second_wind_result,
        )


if __name__ == "__main__":
    unittest.main()
