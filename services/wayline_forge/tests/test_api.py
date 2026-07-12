from __future__ import annotations

import unittest

from services.wayline_forge.app.contracts import (
    BattleQuizRequest,
    InitialSubmission,
    RevisionSubmission,
)
from services.wayline_forge.app.progression import SealTrialPreparationRequest
from services.wayline_forge.tests.api_fixtures import (
    ApiFixture,
    BATCH_ID,
    FacadeFailure,
    PROFILE_ID,
    SESSION_ID,
    TOKEN,
    UNITY_ORIGIN,
)


class WaylineApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = ApiFixture()

    async def asyncTearDown(self) -> None:
        await self.fixture.close()

    async def test_authenticated_health_is_minimal_and_docs_are_disabled(self) -> None:
        health = await self.fixture.client.get(
            "/v1/health",
            headers=self.fixture.public_headers(),
        )
        docs = await self.fixture.client.get(
            "/openapi.json",
            headers=self.fixture.public_headers(),
        )

        self.assertEqual(health.status_code, 200)
        self.assertEqual(
            health.json(),
            {"schemaVersion": "wayline.health.v1", "status": "ready"},
        )
        self.assertEqual(docs.status_code, 404)
        self.assertEqual(docs.json()["code"], "route_not_found")

    async def test_missing_or_duplicate_bearer_token_is_rejected(self) -> None:
        missing = await self.fixture.client.post(
            "/v1/profiles",
            json=self.fixture.profile_payload(),
            headers={"Origin": UNITY_ORIGIN},
        )
        duplicate = await self.fixture.client.post(
            "/v1/profiles",
            json=self.fixture.profile_payload(),
            headers=[
                ("Authorization", f"Bearer {TOKEN}"),
                ("Authorization", f"Bearer {TOKEN}"),
                ("Origin", UNITY_ORIGIN),
            ],
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["code"], "authorization_required")
        self.assertEqual(duplicate.status_code, 401)
        self.assertEqual(duplicate.json()["code"], "authorization_required")
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_non_allowlisted_origin_is_rejected_before_dispatch(self) -> None:
        response = await self.fixture.client.post(
            "/v1/profiles",
            json=self.fixture.profile_payload(),
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Origin": "http://127.0.0.1:49153",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "origin_forbidden")
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_request_body_larger_than_64_kib_is_rejected(self) -> None:
        response = await self.fixture.client.post(
            "/v1/profiles",
            content=b"x" * (64 * 1024 + 1),
            headers={
                **self.fixture.public_headers(),
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "body_too_large")
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_duplicate_json_keys_are_rejected_before_contract_validation(self) -> None:
        response = await self.fixture.client.post(
            "/v1/profiles",
            content=self.fixture.duplicate_profile_json(),
            headers={
                **self.fixture.public_headers(),
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "request_malformed")
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_unknown_contract_field_is_rejected_without_echoing_input(self) -> None:
        payload = self.fixture.profile_payload() | {"learnerName": "private"}

        response = await self.fixture.client.post(
            "/v1/profiles",
            json=payload,
            headers=self.fixture.public_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "contract_invalid")
        self.assertNotIn("private", response.text)
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_non_json_content_type_is_rejected(self) -> None:
        response = await self.fixture.client.post(
            "/v1/profiles",
            content=b"{}",
            headers={
                **self.fixture.public_headers(),
                "Content-Type": "text/plain",
            },
        )

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["code"], "content_type_unsupported")

    async def test_profile_and_session_creation_use_strict_facade_contracts(self) -> None:
        profile = await self.fixture.client.post(
            "/v1/profiles",
            json=self.fixture.profile_payload(),
            headers=self.fixture.public_headers(),
        )
        session = await self.fixture.client.post(
            "/v1/sessions",
            json=self.fixture.session_payload(),
            headers=self.fixture.public_headers(),
        )

        self.assertEqual(profile.status_code, 201)
        self.assertEqual(profile.json()["profileId"], PROFILE_ID)
        self.assertEqual(session.status_code, 201)
        self.assertEqual(session.json()["sessionId"], SESSION_ID)
        self.assertEqual(self.fixture.resolved_sessions, [])
        self.assertEqual(
            [call[0] for call in self.fixture.facade.calls],
            ["create_profile", "create_session"],
        )

    async def test_prepare_resolves_profile_from_session_and_ignores_forged_profile_header(
        self,
    ) -> None:
        headers = self.fixture.public_headers(session=True) | {
            "X-Wayline-Profile-Id": "profile-attacker"
        }

        response = await self.fixture.client.post(
            "/v1/quiz-batches",
            json=self.fixture.battle_payload(),
            headers=headers,
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["batchId"], BATCH_ID)
        self.assertEqual(self.fixture.resolved_sessions, [SESSION_ID])
        method, args, kwargs = self.fixture.facade.calls[-1]
        self.assertEqual(method, "prepare_battle")
        self.assertIs(type(args[0]), BattleQuizRequest)
        self.assertEqual(kwargs["profile_id"], PROFILE_ID)
        self.assertEqual(kwargs["current_session_id"], SESSION_ID)

    async def test_prepare_rejects_body_session_that_differs_from_authenticated_header(
        self,
    ) -> None:
        payload = self.fixture.battle_payload() | {"sessionId": "session-other"}

        response = await self.fixture.client.post(
            "/v1/quiz-batches",
            json=payload,
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "session_not_current")
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_initial_submission_returns_only_truthful_batch_count(self) -> None:
        response = await self.fixture.client.post(
            f"/v1/quiz-batches/{BATCH_ID}/initial",
            json=self.fixture.initial_payload(),
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "schemaVersion": "wayline.v1",
                "batchId": BATCH_ID,
                "itemCount": 3,
                "wrongCount": 2,
                "revisionRequired": True,
                "finalResult": None,
            },
        )
        serialized = response.text.casefold()
        self.assertNotIn("correctanswer", serialized)
        self.assertNotIn("iscorrect", serialized)
        method, args, _kwargs = self.fixture.facade.calls[-1]
        self.assertEqual(method, "submit_initial")
        self.assertIs(type(args[0]), InitialSubmission)

    async def test_revision_and_snapshot_dispatch_with_authenticated_identity(self) -> None:
        revision = await self.fixture.client.post(
            f"/v1/quiz-batches/{BATCH_ID}/revision",
            json=self.fixture.revision_payload(),
            headers=self.fixture.public_headers(session=True),
        )
        snapshot = await self.fixture.client.get(
            f"/v1/quiz-batches/{BATCH_ID}",
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(revision.status_code, 200)
        self.assertEqual(revision.json()["finalCorrectCount"], 3)
        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(snapshot.json()["quizState"], "ready")
        self.assertIs(
            type(self.fixture.facade.calls[0][1][0]),
            RevisionSubmission,
        )
        self.assertEqual(
            [call[0] for call in self.fixture.facade.calls],
            ["submit_revision", "get_quiz_snapshot"],
        )

    async def test_runtime_state_and_gate_are_read_only_session_scoped_queries(self) -> None:
        runtime = await self.fixture.client.get(
            "/v1/runtime-state",
            headers=self.fixture.public_headers(session=True),
        )
        gate = await self.fixture.client.get(
            "/v1/worlds/valuehold/gate",
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(runtime.status_code, 200)
        self.assertEqual(runtime.json()["profileId"], PROFILE_ID)
        self.assertEqual(gate.status_code, 200)
        self.assertFalse(gate.json()["unlocked"])
        self.assertEqual(
            [call[0] for call in self.fixture.facade.calls],
            ["get_runtime_state", "get_boss_gate"],
        )

    async def test_delete_requires_path_profile_to_match_server_resolved_profile(self) -> None:
        forged = await self.fixture.client.delete(
            "/v1/profiles/profile-attacker",
            headers=self.fixture.public_headers(session=True),
        )
        deleted = await self.fixture.client.delete(
            f"/v1/profiles/{PROFILE_ID}",
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(forged.status_code, 401)
        self.assertEqual(forged.json()["code"], "session_not_current")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(deleted.content, b"")
        self.assertEqual(
            [call[0] for call in self.fixture.facade.calls],
            ["delete_profile"],
        )

    async def test_export_uses_server_resolved_profile_and_is_read_only(self) -> None:
        response = await self.fixture.client.get(
            f"/v1/profiles/{PROFILE_ID}/export",
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profileId"], PROFILE_ID)
        self.assertEqual(response.json()["schemaVersion"], "wayline.profile-export.v1")
        self.assertEqual(
            [call[0] for call in self.fixture.facade.calls],
            ["export_profile"],
        )

    async def test_cross_profile_export_fails_without_existence_leakage(self) -> None:
        response = await self.fixture.client.get(
            "/v1/profiles/profile-attacker/export",
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {
                "schemaVersion": "wayline.error.v1",
                "code": "session_not_current",
            },
        )
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_domain_failures_are_redacted_to_stable_public_errors(self) -> None:
        self.fixture.facade.failure_by_method["submit_initial"] = FacadeFailure(
            "storage_busy"
        )

        response = await self.fixture.client.post(
            f"/v1/quiz-batches/{BATCH_ID}/initial",
            json=self.fixture.initial_payload(),
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"schemaVersion": "wayline.error.v1", "code": "storage_busy"},
        )
        self.assertNotIn("detail", response.text.casefold())

    async def test_path_body_identity_mismatch_fails_before_facade_dispatch(self) -> None:
        payload = self.fixture.initial_payload() | {"batchId": "batch-other"}

        response = await self.fixture.client.post(
            f"/v1/quiz-batches/{BATCH_ID}/initial",
            json=payload,
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "contract_invalid")
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_seal_trial_preparation_uses_authoritative_progression_hook(self) -> None:
        response = await self.fixture.client.post(
            "/v1/worlds/valuehold/seal-trials",
            json=self.fixture.seal_trial_payload(),
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json(),
            {
                "schemaVersion": "wayline.v1",
                "requestId": "seal-request-001",
                "worldId": "valuehold",
                "attemptNumber": 1,
                "battleId": "valuehold_seal_trial_1",
                "batch": self.fixture.public_batch_payload(),
            },
        )
        method, args, kwargs = self.fixture.facade.calls[-1]
        self.assertEqual(method, "prepare_seal_trial")
        self.assertEqual(kwargs, {})
        self.assertIs(type(args[0]), SealTrialPreparationRequest)
        self.assertEqual(args[0].profile_id, PROFILE_ID)
        self.assertEqual(args[0].session_id, SESSION_ID)
        self.assertEqual(args[0].world_id, "valuehold")

    async def test_seal_trial_rejects_client_owned_profile_or_world(self) -> None:
        payload = self.fixture.seal_trial_payload() | {
            "profileId": "profile-attacker",
            "worldId": "decimara",
        }

        response = await self.fixture.client.post(
            "/v1/worlds/valuehold/seal-trials",
            json=payload,
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "contract_invalid")
        self.assertEqual(self.fixture.facade.calls, [])

    async def test_seal_trial_preserves_stable_progression_status(self) -> None:
        error = FacadeFailure("invalid_transition")
        error.http_status = 409
        self.fixture.facade.failure_by_method["prepare_seal_trial"] = error

        response = await self.fixture.client.post(
            "/v1/worlds/valuehold/seal-trials",
            json=self.fixture.seal_trial_payload(),
            headers=self.fixture.public_headers(session=True),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "quiz_state_conflict")

    async def test_internal_progression_conflicts_are_coalesced_not_leaked(self) -> None:
        cases = {
            "target_already_completed": "quiz_state_conflict",
            "target_in_progress": "quiz_in_progress",
            "invalid_transition": "quiz_state_conflict",
            "quiz_not_revealed": "quiz_state_conflict",
            "quiz_context_mismatch": "quiz_state_conflict",
            "legacy_profile_blocked": "catalog_conflict",
        }

        for internal_code, public_code in cases.items():
            with self.subTest(internal_code=internal_code):
                self.fixture.facade.failure_by_method["prepare_seal_trial"] = (
                    FacadeFailure(internal_code)
                )
                response = await self.fixture.client.post(
                    "/v1/worlds/valuehold/seal-trials",
                    json=self.fixture.seal_trial_payload(),
                    headers=self.fixture.public_headers(session=True),
                )
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["code"], public_code)
                self.assertNotIn(internal_code, response.text)

    async def test_unknown_route_and_wrong_method_use_redacted_errors(self) -> None:
        missing = await self.fixture.client.get(
            "/v1/not-a-route",
            headers=self.fixture.public_headers(),
        )
        wrong_method = await self.fixture.client.get(
            "/v1/profiles",
            headers=self.fixture.public_headers(),
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "route_not_found")
        self.assertEqual(wrong_method.status_code, 405)
        self.assertEqual(wrong_method.json()["code"], "method_not_allowed")


if __name__ == "__main__":
    unittest.main()
