from __future__ import annotations

from dataclasses import replace
import unittest

from services.wayline_forge.app.progression import (
    AssistedRouteCompletionRequest,
    AssistedRoutePreparationRequest,
    BattleCompletionRequest,
    RevivedCombatCompletionRequest,
    SealTrialCompletionRequest,
    SecondWindCompletionRequest,
    SecondWindStartRequest,
    WorldActivationRequest,
)
from services.wayline_forge.tests.api_fixtures import (
    ApiFixture,
    BATCH_ID,
    FacadeFailure,
    PROFILE_ID,
    SESSION_ID,
)


class ProgressionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = ApiFixture()

    async def asyncTearDown(self) -> None:
        await self.fixture.close()

    @staticmethod
    def common(request_id: str) -> dict[str, object]:
        return {
            "schemaVersion": "wayline.v1",
            "requestId": request_id,
            "sessionId": SESSION_ID,
        }

    async def test_battle_and_seal_completions_bind_all_path_identities(self) -> None:
        battle = await self.fixture.client.post(
            f"/v1/worlds/valuehold/battles/valuehold_route_1/quiz-batches/{BATCH_ID}/completion",
            json=self.common("complete-battle-001") | {"combatWon": True},
            headers=self.fixture.public_headers(session=True),
        )
        seal = await self.fixture.client.post(
            f"/v1/worlds/valuehold/seal-trials/{BATCH_ID}/completion",
            json=self.common("complete-seal-001"),
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(battle.status_code, 200)
        self.assertEqual(battle.json()["battleId"], "valuehold_route_1")
        self.assertEqual(battle.json()["batchId"], BATCH_ID)
        self.assertEqual(seal.status_code, 200)
        self.assertTrue(seal.json()["passed"])
        self.assertEqual(seal.json()["itemCount"], 3)
        battle_request = self.fixture.facade.calls[0][1][0]
        seal_request = self.fixture.facade.calls[1][1][0]
        self.assertIs(type(battle_request), BattleCompletionRequest)
        self.assertIs(type(seal_request), SealTrialCompletionRequest)
        self.assertEqual(battle_request.profile_id, PROFILE_ID)
        self.assertEqual(battle_request.world_id, "valuehold")
        self.assertEqual(battle_request.battle_id, "valuehold_route_1")
        self.assertEqual(battle_request.batch_id, BATCH_ID)
        self.assertEqual(seal_request.batch_id, BATCH_ID)

    async def test_second_wind_lifecycle_binds_attempt_batch_and_wind_ids(self) -> None:
        start = await self.fixture.client.post(
            "/v1/worlds/valuehold/battles/valuehold_route_1/"
            "combat-attempts/combat-attempt-001/second-winds",
            json=self.common("start-second-wind-001")
            | {"preparationRequestId": "prepare-second-wind-001"},
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(start.status_code, 201)
        started = start.json()
        second_wind_id = started["secondWindId"]
        self.assertEqual(started["combatAttemptId"], "combat-attempt-001")
        self.assertEqual(started["batch"]["itemCount"], 3)
        request = self.fixture.facade.calls[-1][1][0]
        self.assertIs(type(request), SecondWindStartRequest)
        self.assertEqual(request.world_id, "valuehold")
        self.assertEqual(request.battle_id, "valuehold_route_1")
        self.assertEqual(request.combat_attempt_id, "combat-attempt-001")

        quiz = await self.fixture.client.post(
            f"/v1/second-winds/{second_wind_id}/quiz-batches/{BATCH_ID}/completion",
            json=self.common("complete-second-wind-001"),
            headers=self.fixture.public_headers(session=True),
        )
        combat = await self.fixture.client.post(
            f"/v1/second-winds/{second_wind_id}/combat-attempts/combat-attempt-001/completion",
            json=self.common("complete-revived-001") | {"combatWon": True},
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(quiz.status_code, 200)
        self.assertEqual(quiz.json()["shieldPercent"], 15)
        self.assertEqual(combat.status_code, 200)
        self.assertTrue(combat.json()["battleCompleted"])
        self.assertIs(
            type(self.fixture.facade.calls[-2][1][0]),
            SecondWindCompletionRequest,
        )
        self.assertIs(
            type(self.fixture.facade.calls[-1][1][0]),
            RevivedCombatCompletionRequest,
        )

    async def test_world_activation_binds_completed_and_next_world_paths(self) -> None:
        response = await self.fixture.client.post(
            "/v1/worlds/valuehold/successors/decimara/activation",
            json=self.common("activate-world-001"),
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["completedWorldId"], "valuehold")
        self.assertEqual(response.json()["activeWorldId"], "decimara")
        request = self.fixture.facade.calls[-1][1][0]
        self.assertIs(type(request), WorldActivationRequest)
        self.assertEqual(request.completed_world_id, "valuehold")
        self.assertEqual(request.next_world_id, "decimara")

    @staticmethod
    def assisted_completion(request_id: str = "complete-assisted-001") -> dict[str, object]:
        return {
            "schemaVersion": "wayline.v1",
            "requestId": request_id,
            "sessionId": SESSION_ID,
            "selections": [
                {
                    "itemId": "item-supported-001",
                    "optionId": "opt-supported-001-d",
                    "confidence": "certain",
                },
                {
                    "itemId": "item-supported-002",
                    "optionId": "opt-supported-002-b",
                    "confidence": "leaning",
                },
            ],
        }

    async def test_assisted_route_lifecycle_is_path_bound_keyless_and_isolated(self) -> None:
        prepared = await self.fixture.client.post(
            "/v1/worlds/valuehold/assisted-routes",
            json=self.common("prepare-assisted-001"),
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(prepared.status_code, 201)
        body = prepared.json()
        route_id = body["batch"]["routeId"]
        self.assertEqual(body["worldId"], "valuehold")
        self.assertEqual(len(body["batch"]["items"]), 2)
        for item in body["batch"]["items"]:
            self.assertEqual(set(item), {"itemId", "prompt", "options"})
            self.assertEqual(len(item["options"]), 4)
            self.assertTrue(all(
                set(option) == {"optionId", "displayText"}
                for option in item["options"]
            ))
        request = self.fixture.facade.calls[-1][1][0]
        self.assertIs(type(request), AssistedRoutePreparationRequest)
        self.assertEqual(request.profile_id, PROFILE_ID)
        self.assertEqual(request.session_id, SESSION_ID)
        self.assertEqual(request.world_id, "valuehold")

        generic = await self.fixture.client.get(
            f"/v1/quiz-batches/{route_id}",
            headers=self.fixture.public_headers(session=True),
        )
        self.assertEqual(generic.status_code, 404)
        self.assertEqual(generic.json()["code"], "batch_unavailable")

        completion_path = (
            f"/v1/worlds/valuehold/assisted-routes/{route_id}/completion"
        )
        completed = await self.fixture.client.post(
            completion_path,
            json=self.assisted_completion(),
            headers=self.fixture.public_headers(session=True),
        )
        replay = await self.fixture.client.post(
            completion_path,
            json=self.assisted_completion(),
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(completed.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), completed.json())
        self.assertTrue(completed.json()["worldCleared"])
        self.assertEqual(completed.json()["finalCorrect"], 1)
        completion_request = self.fixture.facade.calls[-1][1][0]
        self.assertIs(type(completion_request), AssistedRouteCompletionRequest)
        self.assertEqual(completion_request.profile_id, PROFILE_ID)
        self.assertEqual(completion_request.world_id, "valuehold")
        self.assertEqual(completion_request.route_id, route_id)

        conflict = await self.fixture.client.post(
            completion_path,
            json=self.assisted_completion("complete-assisted-new-002"),
            headers=self.fixture.public_headers(session=True),
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "quiz_state_conflict")

    async def test_assisted_route_rejects_forgery_duplicates_and_stale_session(self) -> None:
        prepared = await self.fixture.client.post(
            "/v1/worlds/valuehold/assisted-routes",
            json=self.common("prepare-assisted-security"),
            headers=self.fixture.public_headers(session=True),
        )
        route_id = prepared.json()["batch"]["routeId"]
        path = f"/v1/worlds/valuehold/assisted-routes/{route_id}/completion"

        forged_authority = await self.fixture.client.post(
            path,
            json=self.assisted_completion() | {
                "profileId": "profile-attacker",
                "worldId": "decimara",
                "routeId": "assisted-attacker-route",
            },
            headers=self.fixture.public_headers(session=True),
        )
        forged_option_payload = self.assisted_completion("complete-forged-option")
        forged_option_payload["selections"][0]["optionId"] = "forged-option-id"
        forged_option = await self.fixture.client.post(
            path,
            json=forged_option_payload,
            headers=self.fixture.public_headers(session=True),
        )
        forged_route = await self.fixture.client.post(
            "/v1/worlds/valuehold/assisted-routes/assisted-forged-route/completion",
            json=self.assisted_completion("complete-forged-route"),
            headers=self.fixture.public_headers(session=True),
        )
        duplicate = await self.fixture.client.post(
            path,
            content=(
                b'{"schemaVersion":"wayline.v1","requestId":"one-001",'
                b'"requestId":"two-002","sessionId":"session-001",'
                b'"selections":[]}'
            ),
            headers=self.fixture.public_headers(session=True)
            | {"Content-Type": "application/json"},
        )
        stale = await self.fixture.client.post(
            path,
            json=self.assisted_completion("complete-stale-session")
            | {"sessionId": "session-attacker"},
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(forged_authority.status_code, 422)
        self.assertEqual(forged_option.status_code, 409)
        self.assertEqual(forged_route.status_code, 409)
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(stale.status_code, 401)

    async def test_assisted_safe_content_unavailable_is_a_redacted_503(self) -> None:
        self.fixture.facade.failure_by_method["prepare_assisted_route"] = (
            FacadeFailure("safe_content_unavailable")
        )

        response = await self.fixture.client.post(
            "/v1/worlds/valuehold/assisted-routes",
            json=self.common("prepare-assisted-unavailable"),
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "safe_content_unavailable")

    async def test_progression_bodies_reject_client_owned_authority_and_duplicates(self) -> None:
        forged = self.common("complete-battle-001") | {
            "combatWon": True,
            "profileId": "profile-attacker",
            "worldId": "decimara",
            "battleId": "forged-battle",
            "batchId": "forged-batch",
        }
        response = await self.fixture.client.post(
            f"/v1/worlds/valuehold/battles/valuehold_route_1/quiz-batches/{BATCH_ID}/completion",
            json=forged,
            headers=self.fixture.public_headers(session=True),
        )
        duplicate = await self.fixture.client.post(
            f"/v1/worlds/valuehold/battles/valuehold_route_1/quiz-batches/{BATCH_ID}/completion",
            content=(
                b'{"schemaVersion":"wayline.v1","requestId":"one-001",'
                b'"requestId":"two-002","sessionId":"session-001",'
                b'"combatWon":true}'
            ),
            headers=self.fixture.public_headers(session=True)
            | {"Content-Type": "application/json"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "contract_invalid")
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(duplicate.json()["code"], "request_malformed")
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_progression_body_session_must_match_authenticated_header(self) -> None:
        payload = self.common("complete-battle-001") | {
            "sessionId": "session-attacker",
            "combatWon": True,
        }

        response = await self.fixture.client.post(
            f"/v1/worlds/valuehold/battles/valuehold_route_1/quiz-batches/{BATCH_ID}/completion",
            json=payload,
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "session_not_current")
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_new_target_conflicts_are_public_409_without_internal_leak(self) -> None:
        self.fixture.facade.failure_by_method["complete_battle"] = FacadeFailure(
            "target_already_completed"
        )

        response = await self.fixture.client.post(
            f"/v1/worlds/valuehold/battles/valuehold_route_1/quiz-batches/{BATCH_ID}/completion",
            json=self.common("new-target-001") | {"combatWon": True},
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "quiz_state_conflict")
        self.assertNotIn("target_already_completed", response.text)

    async def test_exact_replay_is_stable_and_mismatched_result_fails_closed(self) -> None:
        path = (
            f"/v1/worlds/valuehold/battles/valuehold_route_1/"
            f"quiz-batches/{BATCH_ID}/completion"
        )
        payload = self.common("replay-battle-001") | {"combatWon": True}

        first = await self.fixture.client.post(
            path,
            json=payload,
            headers=self.fixture.public_headers(session=True),
        )
        replay = await self.fixture.client.post(
            path,
            json=payload,
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(
            self.fixture.facade.calls[-2][1][0],
            self.fixture.facade.calls[-1][1][0],
        )

        original = self.fixture.facade.complete_battle

        def mismatched(request: BattleCompletionRequest):
            return replace(original(request), batch_id="batch-other")

        self.fixture.facade.complete_battle = mismatched  # type: ignore[method-assign]
        rejected = await self.fixture.client.post(
            path,
            json=self.common("mismatch-battle-001") | {"combatWon": True},
            headers=self.fixture.public_headers(session=True),
        )
        self.assertEqual(rejected.status_code, 500)
        self.assertEqual(rejected.json()["code"], "integrity_failure")


if __name__ == "__main__":
    unittest.main()
