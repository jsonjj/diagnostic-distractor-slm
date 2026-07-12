import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from services.wayline_forge.app import contracts as public_contracts


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts/wayline/v1"
CATALOG_SHA256 = "0123456789abcdef" * 4


def valid_payloads() -> tuple[
    tuple[str, type[public_contracts.StrictModel], dict[str, object]],
    ...,
]:
    return (
        (
            "profile-create.schema.json",
            public_contracts.ProfileCreate,
            {
                "schemaVersion": "wayline.v1",
                "requestId": "request-001",
            },
        ),
        (
            "profile-created.schema.json",
            public_contracts.ProfileCreated,
            {
                "schemaVersion": "wayline.v1",
                "profileId": "profile-001",
                "createdAtUtc": "2026-07-11T12:34:56.123456Z",
            },
        ),
        (
            "session-create.schema.json",
            public_contracts.SessionCreate,
            {
                "schemaVersion": "wayline.v1",
                "requestId": "request-002",
                "profileId": "profile-001",
                "clientBuild": "mac-demo-0.1.0",
            },
        ),
        (
            "session-created.schema.json",
            public_contracts.SessionCreated,
            {
                "schemaVersion": "wayline.v1",
                "profileId": "profile-001",
                "sessionId": "session-001",
                "createdAtUtc": "2026-07-11T12:34:56Z",
                "activeWorldId": "valuehold",
                "campaignCatalogSha256": CATALOG_SHA256,
            },
        ),
        (
            "runtime-state.schema.json",
            public_contracts.RuntimeState,
            {
                "schemaVersion": "wayline.v1",
                "profileId": "profile-001",
                "sessionId": "session-001",
                "activeWorldId": "valuehold",
                "campaignOrdinal": 1,
                "resumableBatchId": None,
                "campaignCatalogSha256": CATALOG_SHA256,
            },
        ),
    )


class IdentityContractTests(unittest.TestCase):
    def _validate_baseline(
        self,
        model_type: type[public_contracts.StrictModel],
        payload: dict[str, object],
    ) -> public_contracts.StrictModel:
        try:
            return model_type.model_validate(payload)
        except ValidationError as error:
            self.fail(f"valid {model_type.__name__} baseline was rejected: {error}")

    def test_public_identity_models_exist(self):
        for model_name in (
            "ProfileCreate",
            "ProfileCreated",
            "SessionCreated",
            "RuntimeState",
        ):
            with self.subTest(model=model_name):
                self.assertTrue(
                    hasattr(public_contracts, model_name),
                    f"missing public contract model: {model_name}",
                )

    def test_valid_payloads_round_trip_with_public_aliases(self):
        for _, model_type, payload in valid_payloads():
            with self.subTest(model=model_type.__name__):
                self._validate_baseline(model_type, payload)
                try:
                    model = public_contracts.parse_public_json(
                        model_type,
                        json.dumps(payload),
                    )
                except ValidationError as error:
                    self.fail(f"valid JSON round trip was rejected: {error}")
                self.assertEqual(model.model_dump(mode="json", by_alias=True), payload)

    def test_new_models_reject_unknown_and_snake_case_fields(self):
        for _, model_type, payload in valid_payloads():
            if model_type is public_contracts.SessionCreate:
                continue
            self._validate_baseline(model_type, payload)
            with self.subTest(model=model_type.__name__, case="unknown"):
                with self.assertRaises(ValidationError):
                    model_type.model_validate(payload | {"displayName": "Child"})

            first_alias = next(iter(payload))
            snake_payload = dict(payload)
            snake_payload["schema_version"] = snake_payload.pop(first_alias)
            with self.subTest(model=model_type.__name__, case="snake_case"):
                with self.assertRaises(ValidationError):
                    model_type.model_validate(snake_payload)

    def test_created_timestamps_require_canonical_real_utc_values(self):
        malformed = (
            "2026-07-11 12:34:56Z",
            "2026-07-11T12:34:56+00:00",
            "2026-07-11T12:34:56.1Z",
            "2026-02-30T12:34:56Z",
        )
        created_contracts = (
            (
                public_contracts.ProfileCreated,
                valid_payloads()[1][2],
            ),
            (
                public_contracts.SessionCreated,
                valid_payloads()[3][2],
            ),
        )
        for model_type, payload in created_contracts:
            self._validate_baseline(model_type, payload)
            for timestamp in malformed:
                with self.subTest(model=model_type.__name__, timestamp=timestamp):
                    with self.assertRaises(ValidationError):
                        model_type.model_validate(payload | {"createdAtUtc": timestamp})

    def test_catalog_hash_requires_lowercase_64_hex(self):
        for model_type, payload in (
            (public_contracts.SessionCreated, valid_payloads()[3][2]),
            (public_contracts.RuntimeState, valid_payloads()[4][2]),
        ):
            self._validate_baseline(model_type, payload)
            for invalid_hash in (
                CATALOG_SHA256.upper(),
                CATALOG_SHA256[:-1],
                "g" * 64,
            ):
                with self.subTest(model=model_type.__name__, value=invalid_hash):
                    with self.assertRaises(ValidationError):
                        model_type.model_validate(
                            payload | {"campaignCatalogSha256": invalid_hash}
                        )

    def test_runtime_state_rejects_numeric_coercion_and_nonpositive_ordinal(self):
        payload = valid_payloads()[4][2]
        self._validate_baseline(public_contracts.RuntimeState, payload)
        for campaign_ordinal in ("1", 1.0, True, 0, -1):
            with self.subTest(campaignOrdinal=campaign_ordinal):
                with self.assertRaises(ValidationError):
                    public_contracts.RuntimeState.model_validate(
                        payload | {"campaignOrdinal": campaign_ordinal}
                    )

    def test_runtime_state_resume_is_required_but_nullable(self):
        payload = valid_payloads()[4][2]
        self._validate_baseline(public_contracts.RuntimeState, payload)
        self.assertIsNone(
            public_contracts.RuntimeState.model_validate(payload).resumable_batch_id
        )
        resumed = public_contracts.RuntimeState.model_validate(
            payload | {"resumableBatchId": "batch-001"}
        )
        self.assertEqual(resumed.resumable_batch_id, "batch-001")

        missing = dict(payload)
        del missing["resumableBatchId"]
        for invalid in (missing, payload | {"resumableBatchId": "x"}):
            with self.subTest(payload=invalid):
                with self.assertRaises(ValidationError):
                    public_contracts.RuntimeState.model_validate(invalid)

    def test_new_models_are_frozen(self):
        for _, model_type, payload in valid_payloads():
            if model_type is public_contracts.SessionCreate:
                continue
            model = self._validate_baseline(model_type, payload)
            field_name = next(iter(model_type.model_fields))
            with self.subTest(model=model_type.__name__):
                with self.assertRaises(ValidationError):
                    setattr(model, field_name, getattr(model, field_name))

    def test_identity_schemas_are_closed_required_and_match_model_aliases(self):
        for schema_name, model_type, _ in valid_payloads():
            with self.subTest(schema=schema_name):
                schema_path = CONTRACTS / schema_name
                self.assertTrue(schema_path.is_file(), f"missing schema: {schema_name}")
                schema = json.loads(schema_path.read_text())
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertEqual(schema["type"], "object")
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(set(schema["required"]), set(schema["properties"]))

                model_schema = model_type.model_json_schema(by_alias=True)
                self.assertEqual(
                    set(schema["properties"]),
                    set(model_schema["properties"]),
                )
                self.assertEqual(
                    set(schema["required"]),
                    set(model_schema["required"]),
                )

    def test_identity_contracts_expose_no_child_name_token_or_secret_fields(self):
        forbidden = {
            "displayName",
            "display_name",
            "name",
            "token",
            "accessToken",
            "refreshToken",
            "secret",
            "apiKey",
        }
        for schema_name, model_type, payload in valid_payloads():
            with self.subTest(model=model_type.__name__):
                aliases = {
                    field.alias or field_name
                    for field_name, field in model_type.model_fields.items()
                }
                self.assertTrue(forbidden.isdisjoint(aliases))
                self.assertTrue(forbidden.isdisjoint(payload))

                schema_path = CONTRACTS / schema_name
                self.assertTrue(schema_path.is_file(), f"missing schema: {schema_name}")
                schema = json.loads(schema_path.read_text())
                self.assertTrue(forbidden.isdisjoint(schema["properties"]))
