import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.game_candidate_generation import (
    FROZEN_HOLDOUT_RECORD_COUNT,
    FROZEN_HOLDOUT_SHA256,
)
from src.game_colab_backend import BACKEND_SOURCE_SHA256
from src.game_colab_bundle import (
    BUNDLE_FILES,
    BUNDLE_MANIFEST_PATH,
    BUNDLE_SCHEMA_VERSION,
    ColabBundleError,
    build_colab_bundle,
    verify_colab_bundle,
)


ROOT = Path(__file__).resolve().parents[1]


class ColabBundleTests(unittest.TestCase):
    def test_builds_a_deterministic_allowlisted_archive_with_exact_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.zip"
            second_path = Path(directory) / "second.zip"

            first_manifest = build_colab_bundle(ROOT, first_path)
            second_manifest = build_colab_bundle(ROOT, second_path)

            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            self.assertEqual(first_manifest, second_manifest)
            with zipfile.ZipFile(first_path) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    set(BUNDLE_FILES) | {BUNDLE_MANIFEST_PATH},
                )
                for item in first_manifest["files"]:
                    payload = archive.read(item["path"])
                    self.assertEqual(item["size"], len(payload))
                    self.assertEqual(
                        item["sha256"],
                        hashlib.sha256(payload).hexdigest(),
                    )

    def test_manifest_pins_holdout_and_generator_source(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle.zip"
            manifest = build_colab_bundle(ROOT, output)
            verified = verify_colab_bundle(output)

        self.assertEqual(manifest, verified)
        self.assertEqual(manifest["schema_version"], BUNDLE_SCHEMA_VERSION)
        self.assertEqual(
            manifest["frozen_holdout_count"],
            FROZEN_HOLDOUT_RECORD_COUNT,
        )
        self.assertEqual(manifest["frozen_holdout_sha256"], FROZEN_HOLDOUT_SHA256)
        generator = next(
            item
            for item in manifest["files"]
            if item["path"] == "src/game_candidate_generation.py"
        )
        self.assertEqual(manifest["generator_source_sha256"], generator["sha256"])
        backend = next(
            item
            for item in manifest["files"]
            if item["path"] == "src/game_colab_backend.py"
        )
        self.assertEqual(manifest["backend_source_sha256"], backend["sha256"])
        self.assertEqual(manifest["backend_source_sha256"], BACKEND_SOURCE_SHA256)

    def test_refuses_to_overwrite_a_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle.zip"
            build_colab_bundle(ROOT, output)

            with self.assertRaisesRegex(ColabBundleError, "already exists"):
                build_colab_bundle(ROOT, output)

    def test_verifier_rejects_an_unexpected_traversal_member(self):
        with tempfile.TemporaryDirectory() as directory:
            valid_path = Path(directory) / "valid.zip"
            malicious_path = Path(directory) / "malicious.zip"
            build_colab_bundle(ROOT, valid_path)

            with zipfile.ZipFile(valid_path) as source, zipfile.ZipFile(
                malicious_path,
                "w",
            ) as destination:
                for name in source.namelist():
                    destination.writestr(name, source.read(name))
                destination.writestr("../escape.py", b"raise SystemExit")

            with self.assertRaisesRegex(ColabBundleError, "unsafe|unexpected"):
                verify_colab_bundle(malicious_path)

    def test_verifier_rejects_a_tampered_allowlisted_file(self):
        with tempfile.TemporaryDirectory() as directory:
            valid_path = Path(directory) / "valid.zip"
            tampered_path = Path(directory) / "tampered.zip"
            build_colab_bundle(ROOT, valid_path)

            with zipfile.ZipFile(valid_path) as source, zipfile.ZipFile(
                tampered_path,
                "w",
            ) as destination:
                for name in source.namelist():
                    payload = source.read(name)
                    if name == "src/prompts.py":
                        payload = b"X" + payload[1:]
                    destination.writestr(name, payload)

            with self.assertRaisesRegex(ColabBundleError, "hash mismatch"):
                verify_colab_bundle(tampered_path)


if __name__ == "__main__":
    unittest.main()
