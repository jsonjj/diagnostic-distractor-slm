from __future__ import annotations

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError
from referencing import Registry, Resource

from services.wayline_forge.app.contracts import (
    AssistedRouteComplete,
    AssistedRouteCompleted,
    AssistedRoutePrepare,
    AssistedRoutePrepared,
    BattleComplete,
    BattleCompleted,
    RevivedCombatComplete,
    RevivedCombatCompleted,
    SealTrialComplete,
    SealTrialCompleted,
    SecondWindComplete,
    SecondWindCompleted,
    SecondWindStart,
    SecondWindStarted,
    WorldActivate,
    WorldActivated,
    parse_public_json,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts/wayline/v1"
VALID = CONTRACTS / "fixtures/valid"
INVALID = CONTRACTS / "fixtures/invalid"


class ProgressionContractTests(unittest.TestCase):
    MODELS = {
        "assisted-route-prepare.json": AssistedRoutePrepare,
        "assisted-route-prepared.json": AssistedRoutePrepared,
        "assisted-route-complete.json": AssistedRouteComplete,
        "assisted-route-completed.json": AssistedRouteCompleted,
        "battle-complete.json": BattleComplete,
        "battle-completed.json": BattleCompleted,
        "seal-trial-complete.json": SealTrialComplete,
        "seal-trial-completed.json": SealTrialCompleted,
        "second-wind-start.json": SecondWindStart,
        "second-wind-started.json": SecondWindStarted,
        "second-wind-complete.json": SecondWindComplete,
        "second-wind-completed.json": SecondWindCompleted,
        "revived-combat-complete.json": RevivedCombatComplete,
        "revived-combat-completed.json": RevivedCombatCompleted,
        "world-activate.json": WorldActivate,
        "world-activated.json": WorldActivated,
    }

    def test_all_progression_contracts_have_valid_shared_fixtures_and_schemas(self) -> None:
        for fixture_name, model_type in self.MODELS.items():
            with self.subTest(fixture=fixture_name):
                payload = (VALID / fixture_name).read_text(encoding="utf-8")
                value = parse_public_json(model_type, payload)
                self.assertEqual(
                    value.model_dump(mode="json", by_alias=True),
                    json.loads(payload),
                )
                schema_name = fixture_name.removesuffix(".json") + ".schema.json"
                schema_path = CONTRACTS / schema_name
                self.assertTrue(schema_path.is_file())
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                self.assertFalse(schema.get("additionalProperties", True))
                self.assertEqual(
                    set(schema["properties"]),
                    set(model_type.model_json_schema(by_alias=True)["properties"]),
                )

    def test_draft_2020_12_schemas_validate_every_valid_wire_fixture(self) -> None:
        registry = Registry()
        for schema_path in CONTRACTS.glob("*.schema.json"):
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            registry = registry.with_resource(
                schema["$id"],
                Resource.from_contents(schema),
            )

        for fixture_name in self.MODELS:
            with self.subTest(fixture=fixture_name):
                schema_name = fixture_name.removesuffix(".json") + ".schema.json"
                schema = json.loads((CONTRACTS / schema_name).read_text())
                fixture = json.loads((VALID / fixture_name).read_text())
                Draft202012Validator(schema, registry=registry).validate(fixture)

    def test_command_bodies_never_accept_server_or_path_owned_identity(self) -> None:
        forbidden = {
            "profileId": "profile-attacker",
            "worldId": "decimara",
            "battleId": "forged-battle",
            "batchId": "forged-batch",
            "secondWindId": "forged-wind",
            "combatAttemptId": "forged-attempt",
            "completedWorldId": "decimara",
            "nextWorldId": "valuehold",
            "routeId": "forged-route",
        }
        request_names = (
            "assisted-route-prepare.json",
            "assisted-route-complete.json",
            "battle-complete.json",
            "seal-trial-complete.json",
            "second-wind-start.json",
            "second-wind-complete.json",
            "revived-combat-complete.json",
            "world-activate.json",
        )
        for fixture_name in request_names:
            model_type = self.MODELS[fixture_name]
            baseline = json.loads((VALID / fixture_name).read_text())
            for field, value in forbidden.items():
                if field in baseline:
                    continue
                with self.subTest(fixture=fixture_name, field=field):
                    with self.assertRaises(ValidationError):
                        model_type.model_validate(baseline | {field: value})

    def test_semantic_invalid_fixtures_fail_closed_with_stable_rules(self) -> None:
        cases = {
            "battle-completed-count-mismatch.json": (
                BattleCompleted,
                "finalCorrect cannot exceed itemCount",
            ),
            "seal-trial-completed-pass-mismatch.json": (
                SealTrialCompleted,
                "passed must equal finalCorrect >= 2",
            ),
            "second-wind-started-identity-mismatch.json": (
                SecondWindStarted,
                "Second Wind identities must be derived from combat",
            ),
            "second-wind-completed-shield-mismatch.json": (
                SecondWindCompleted,
                "shieldPercent must match finalCorrect",
            ),
            "revived-combat-completed-state-mismatch.json": (
                RevivedCombatCompleted,
                "battleCompleted must equal combatWon",
            ),
            "world-activated-same-world.json": (
                WorldActivated,
                "activeWorldId must differ from completedWorldId",
            ),
        }
        for fixture_name, (model_type, error_text) in cases.items():
            with self.subTest(fixture=fixture_name):
                with self.assertRaises(ValidationError) as caught:
                    parse_public_json(
                        model_type,
                        (INVALID / fixture_name).read_text(encoding="utf-8"),
                    )
                self.assertIn(error_text, str(caught.exception))

    def test_assisted_preparation_seals_supported_truth(self) -> None:
        prepared = parse_public_json(
            AssistedRoutePrepared,
            (VALID / "assisted-route-prepared.json").read_text(encoding="utf-8"),
        )

        def field_names(value: object) -> set[str]:
            if isinstance(value, dict):
                return {
                    *(str(key).casefold() for key in value),
                    *(
                        name
                        for child in value.values()
                        for name in field_names(child)
                    ),
                }
            if isinstance(value, list):
                return {
                    name
                    for child in value
                    for name in field_names(child)
                }
            return set()

        supported = [
            item.model_dump(mode="json", by_alias=True)
            for item in prepared.batch.items
        ]
        self.assertTrue(
            field_names(supported).isdisjoint(
                {
                    "sourcebatchid",
                    "correctoptionid",
                    "correctanswer",
                    "procedureid",
                    "possibleerror",
                    "reliablemethod",
                    "trustedsteps",
                    "iscorrect",
                }
            )
        )

    def test_assisted_semantic_invalid_fixtures_fail_python_and_schema(self) -> None:
        cases = {
            "assisted-route-complete-duplicate-item.json": (
                AssistedRouteComplete,
                "supported selections must target distinct items",
            ),
            "assisted-route-completed-correctness-mismatch.json": (
                AssistedRouteCompleted,
                "isCorrect must match correctOptionId",
            ),
            "assisted-route-completed-count-mismatch.json": (
                AssistedRouteCompleted,
                "finalCorrect must match item results",
            ),
            "assisted-route-prepared-mcq-key-leak.json": (
                AssistedRoutePrepared,
                "Extra inputs are not permitted",
            ),
            "assisted-route-prepared-world-mismatch.json": (
                AssistedRoutePrepared,
                "batch.worldId must match worldId",
            ),
        }
        registry = Registry()
        for schema_path in CONTRACTS.glob("*.schema.json"):
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            registry = registry.with_resource(
                schema["$id"],
                Resource.from_contents(schema),
            )

        for fixture_name, (model_type, error_text) in cases.items():
            with self.subTest(fixture=fixture_name):
                payload = (INVALID / fixture_name).read_text(encoding="utf-8")
                with self.assertRaises(ValidationError) as caught:
                    parse_public_json(model_type, payload)
                self.assertIn(error_text, str(caught.exception))
                if "prepared" in fixture_name:
                    schema_name = "assisted-route-prepared.schema.json"
                elif "completed" in fixture_name:
                    schema_name = "assisted-route-completed.schema.json"
                else:
                    schema_name = "assisted-route-complete.schema.json"
                schema = json.loads((CONTRACTS / schema_name).read_text())
                with self.assertRaises(JsonSchemaValidationError):
                    Draft202012Validator(schema, registry=registry).validate(
                        json.loads(payload)
                    )


if __name__ == "__main__":
    unittest.main()
