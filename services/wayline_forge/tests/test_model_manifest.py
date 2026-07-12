import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from services.wayline_forge.app.model_manifest import (
    DuplicateManifestKeyError,
    ModelManifest,
    parse_model_manifest,
)


SERVICE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = SERVICE_ROOT / "tests/fixtures/model_manifest"
SCHEMA = SERVICE_ROOT / "model_manifest.schema.json"


class ModelManifestTests(unittest.TestCase):
    def test_valid_manifest_is_immutable_and_exactly_pinned(self):
        manifest = parse_model_manifest((FIXTURES / "valid.json").read_text())

        self.assertEqual(manifest.schema_version, "wayline.model-manifest.v1")
        self.assertEqual(manifest.base_model_id, "unsloth/Qwen3-4B-bnb-4bit")
        self.assertEqual(
            manifest.adapter_id,
            "j2ampn/qwen3-4b-distractor-lora-v7",
        )
        self.assertEqual(manifest.quantization, "Q4_K_M")
        self.assertEqual(manifest.platform, "macos-arm64")
        with self.assertRaises(ValidationError):
            manifest.context_size = 4096

    def test_invalid_fixtures_are_rejected(self):
        for fixture_name in (
            "mutable-revision.json",
            "bad-digest.json",
            "unknown-field.json",
        ):
            with self.subTest(fixture=fixture_name):
                with self.assertRaises(ValidationError):
                    parse_model_manifest((FIXTURES / fixture_name).read_text())

    def test_duplicate_keys_are_rejected_before_validation(self):
        payload = (FIXTURES / "valid.json").read_text().replace(
            '"contextSize": 2048,',
            '"contextSize": 2048, "contextSize": 4096,',
        )

        with self.assertRaises(DuplicateManifestKeyError) as caught:
            parse_model_manifest(payload)

        self.assertEqual(caught.exception.key, "contextSize")

    def test_versions_ids_quantization_and_platform_are_exact(self):
        original = json.loads((FIXTURES / "valid.json").read_text())
        mutations = (
            ("schemaVersion", "wayline.model-manifest.v2"),
            ("baseModelId", "Qwen/Qwen3-4B"),
            ("adapterId", "j2ampn/qwen3-4b-distractor-lora-latest"),
            ("quantization", "Q8_0"),
            ("platform", "linux-x86_64"),
        )

        for field, value in mutations:
            with self.subTest(field=field):
                payload = original | {field: value}
                with self.assertRaises(ValidationError):
                    ModelManifest.model_validate(payload)

    def test_revisions_digests_and_runtime_settings_are_strictly_bounded(self):
        original = json.loads((FIXTURES / "valid.json").read_text())
        mutations = (
            ("baseModelRevision", "A" * 40),
            ("adapterRevision", "a" * 39),
            ("llamaCppRevision", "main"),
            ("ggufSha256", "A" * 64),
            ("promptSha256", "a" * 63),
            ("tokenizerSha256", "g" * 64),
            ("contextSize", 511),
            ("contextSize", 8193),
            ("threadCount", 0),
            ("threadCount", 33),
            ("threadCount", "8"),
        )

        for field, value in mutations:
            with self.subTest(field=field, value=value):
                payload = original | {field: value}
                with self.assertRaises(ValidationError):
                    ModelManifest.model_validate(payload)

    def test_manifest_rejects_urls_secrets_and_latest_names(self):
        original = json.loads((FIXTURES / "valid.json").read_text())
        filenames = (
            "https://example.invalid/model.gguf",
            "latest.gguf",
            "hf_secret.gguf",
        )

        for filename in filenames:
            with self.subTest(filename=filename):
                with self.assertRaises(ValidationError):
                    ModelManifest.model_validate(
                        original | {"ggufFileName": filename}
                    )

        serialized = parse_model_manifest(
            (FIXTURES / "valid.json").read_text()
        ).model_dump_json(by_alias=True).lower()
        for banned in ("http://", "https://", "latest", "api_key", "apikey", "hf_"):
            self.assertNotIn(banned, serialized)

    def test_frozen_json_schema_is_closed_and_matches_code_fields(self):
        schema = json.loads(SCHEMA.read_text())
        code_schema = ModelManifest.model_json_schema(by_alias=True)

        self.assertEqual(
            schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )
        self.assertEqual(schema["$id"], "urn:wayline:model-manifest:v1")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(set(schema["properties"]), set(code_schema["properties"]))
        self.assertEqual(set(schema["required"]), set(code_schema["required"]))

