from pathlib import Path
import os
import unittest
from unittest.mock import patch

from services.wayline_forge.app.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_test_defaults_are_loopback_and_secret_free(self):
        runtime_root = Path("/tmp/wayline-runtime").resolve()

        settings = Settings.for_tests(runtime_root)

        self.assertEqual(settings.runtime_root, runtime_root)
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 0)
        self.assertEqual(
            settings.model_manifest,
            runtime_root / "resources/model_manifest_v1.json",
        )
        self.assertEqual(
            settings.reviewed_cache_release_root,
            runtime_root / "resources/reviewed_cache_release_v1",
        )
        self.assertFalse(hasattr(settings, "cache_path"))
        self.assertEqual(
            settings.profile_db,
            runtime_root / "profiles/wayline_profiles_v1.sqlite",
        )
        self.assertIsNone(settings.truefoundry_base_url)
        self.assertIsNone(settings.truefoundry_model)
        self.assertIsNone(settings.truefoundry_api_key)

    def test_environment_uses_packaged_root_and_optional_provider_values(self):
        runtime_root = Path("/tmp/wayline-live").resolve()
        environment = {
            "WAYLINE_RUNTIME_ROOT": str(runtime_root),
            "WAYLINE_PORT": "43117",
            "TFY_BASE_URL": "https://example.invalid/api",
            "TFY_MODEL": "claude-sonnet-5",
            "TFY_API_KEY": "test-secret",
        }

        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_environment()

        self.assertEqual(settings.runtime_root, runtime_root)
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 43117)
        self.assertEqual(
            settings.model_manifest,
            runtime_root / "resources/model_manifest_v1.json",
        )
        self.assertEqual(
            settings.reviewed_cache_release_root,
            runtime_root / "resources/reviewed_cache_release_v1",
        )
        self.assertFalse(hasattr(settings, "cache_path"))
        self.assertEqual(
            settings.profile_db,
            runtime_root / "profiles/wayline_profiles_v1.sqlite",
        )
        self.assertEqual(settings.truefoundry_base_url, environment["TFY_BASE_URL"])
        self.assertEqual(settings.truefoundry_model, environment["TFY_MODEL"])
        self.assertEqual(settings.truefoundry_api_key, environment["TFY_API_KEY"])

    def test_api_key_is_redacted_from_settings_repr(self):
        secret = "never-print-this-secret"
        environment = {
            "WAYLINE_RUNTIME_ROOT": "/tmp/wayline-live",
            "TFY_API_KEY": secret,
        }

        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_environment()

        self.assertNotIn(secret, repr(settings))

    def test_environment_rejects_ports_outside_tcp_bounds(self):
        for invalid_port in ("-1", "65536"):
            with self.subTest(port=invalid_port):
                environment = {
                    "WAYLINE_RUNTIME_ROOT": "/tmp/wayline-live",
                    "WAYLINE_PORT": invalid_port,
                }
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(ValueError):
                        Settings.from_environment()

    def test_environment_root_must_already_be_absolute_and_normalized(self):
        for invalid_root in (
            "relative",
            "~/wayline-live",
            "/tmp/runtime/../runtime",
            "/tmp/runtime/",
            "//tmp/wayline-live",
        ):
            with self.subTest(runtime_root=invalid_root):
                with patch.dict(
                    os.environ,
                    {"WAYLINE_RUNTIME_ROOT": invalid_root},
                    clear=True,
                ):
                    with self.assertRaises(ValueError):
                        Settings.from_environment()

    def test_test_root_must_be_absolute_and_normalized(self):
        for invalid_root in (Path("relative"), Path("/tmp/runtime/../runtime")):
            with self.subTest(runtime_root=invalid_root):
                with self.assertRaises(ValueError):
                    Settings.for_tests(invalid_root)
