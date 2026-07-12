from __future__ import annotations

from dataclasses import replace
import gzip
import inspect
import json
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]


def _make_writable(root: Path) -> None:
    if not root.exists():
        return
    for directory, directories, files in os.walk(root, topdown=False):
        for name in files:
            try:
                (Path(directory) / name).chmod(0o600)
            except OSError:
                pass
        for name in directories:
            try:
                (Path(directory) / name).chmod(0o700)
            except OSError:
                pass
        try:
            Path(directory).chmod(0o700)
        except OSError:
            pass


class PackagedLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve() / "WaylineForge-macos-arm64"
        self.root.mkdir()
        self.addCleanup(self._cleanup)
        self._assemble_minimal_package()

    def _cleanup(self) -> None:
        _make_writable(Path(self.temporary.name))
        self.temporary.cleanup()

    def _restore_writable_unmanifested_package(self) -> None:
        _make_writable(self.root)
        manifest = self.root / "package_manifest_v1.json"
        if manifest.exists() or manifest.is_symlink():
            manifest.unlink()

    def _write(self, relative: str, payload: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _assemble_minimal_package(self) -> None:
        self._write("WaylineForge", b"fake-mach-o-sidecar")
        server = self._write(
            "bin/llama-server",
            b"fake-pinned-llama-server",
        )
        model = self._write(
            "models/wayline-qwen3-4b-distractor-v7-q4_k_m.gguf",
            b"fake-receipted-q4-k-m-gguf",
        )
        model_manifest = json.loads(
            (
                SERVICE_ROOT / "tests/fixtures/model_manifest/valid.json"
            ).read_text(encoding="utf-8")
        )
        model_manifest["ggufSha256"] = hashlib.sha256(
            model.read_bytes()
        ).hexdigest()
        self._write(
            "resources/model_manifest_v1.json",
            json.dumps(
                model_manifest,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"),
        )
        from services.wayline_forge.app.macos_worker_runtime import (
            DescriptorBindingReleaseReceipt,
        )
        from services.wayline_forge.app.production_spawn import (
            PRODUCTION_SPAWN_ADAPTER_SHA256,
        )

        self.descriptor_receipt = DescriptorBindingReleaseReceipt.attest(
            binary_sha256=hashlib.sha256(server.read_bytes()).hexdigest(),
            model_sha256=model_manifest["ggufSha256"],
            llama_cpp_revision=model_manifest["llamaCppRevision"],
            os_name="Darwin",
            architecture="arm64",
            readiness_protocol_revision="llama.cpp.openai.models.v1",
            spawn_adapter_sha256=PRODUCTION_SPAWN_ADAPTER_SHA256,
        )
        self._write(
            "resources/descriptor_binding_release_receipt_v1.json",
            self.descriptor_receipt.to_json().encode("utf-8"),
        )
        for name in (
            "campaign_catalog_v1.json",
            "curriculum_v1.json",
            "procedure_registry_v1.json",
            "story_templates_v1.json",
        ):
            self._write(f"resources/{name}", b'{"fixture":true}\n')
        cache_manifest = b'{"fixture":true}'
        manifest_sha256 = hashlib.sha256(cache_manifest).hexdigest()
        generation = "generation-" + manifest_sha256
        pointer = {
            "generationId": generation,
            "manifestSha256": manifest_sha256,
            "schemaVersion": "wayline.reviewed-cache-pointer.v1",
        }
        self._write(
            "resources/reviewed_cache_release_v1/current.json",
            json.dumps(
                pointer,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"),
        )
        self._write(
            "resources/reviewed_cache_release_v1/generations/"
            f"{generation}/reviewed_cache_manifest.json",
            cache_manifest,
        )
        self._write(
            "resources/reviewed_cache_release_v1/generations/"
            f"{generation}/reviewed_cache.sqlite3",
            b"SQLite format 3\x00fixture",
        )
        self._write("_internal/runtime.bin", b"pyinstaller-runtime")

    def _write_and_validate(self):
        from services.wayline_forge.scripts.build_mac_sidecar import (
            validate_packaged_layout,
            write_package_manifest,
        )

        written = write_package_manifest(self.root)
        validated = validate_packaged_layout(self.root)
        self.assertEqual(validated, written)
        return validated

    def test_manifest_hashes_every_file_and_records_bundled_model(self) -> None:
        manifest = self._write_and_validate()

        expected_files = {
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and path.name != "package_manifest_v1.json"
        }
        self.assertEqual(
            {entry.relative_path for entry in manifest.entries},
            expected_files,
        )
        self.assertEqual(manifest.platform, "macos-arm64")
        self.assertEqual(manifest.executable, "WaylineForge")
        self.assertEqual(manifest.llama_server, "bin/llama-server")
        self.assertTrue(manifest.optional_model.bundled)
        self.assertEqual(
            manifest.optional_model.file_name,
            "wayline-qwen3-4b-distractor-v7-q4_k_m.gguf",
        )
        for entry in manifest.entries:
            self.assertEqual(len(entry.sha256), 64)
            self.assertGreater(entry.size_bytes, 0)

    def test_missing_descriptor_binding_receipt_fails_closed(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        (
            self.root
            / "resources/descriptor_binding_release_receipt_v1.json"
        ).unlink()

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_file_missing")

    def test_descriptor_receipt_must_bind_every_launch_authority(self) -> None:
        from services.wayline_forge.app.macos_worker_runtime import (
            DescriptorBindingReleaseReceipt,
        )
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        path = (
            self.root
            / "resources/descriptor_binding_release_receipt_v1.json"
        )
        cases = (
            replace(self.descriptor_receipt, binary_sha256="a" * 64),
            replace(self.descriptor_receipt, model_sha256="b" * 64),
            replace(self.descriptor_receipt, llama_cpp_revision="c" * 40),
            replace(self.descriptor_receipt, spawn_adapter_sha256="d" * 64),
            DescriptorBindingReleaseReceipt.attest(
                binary_sha256=self.descriptor_receipt.binary_sha256,
                model_sha256=self.descriptor_receipt.model_sha256,
                llama_cpp_revision=self.descriptor_receipt.llama_cpp_revision,
                os_name="Linux",
                architecture="arm64",
                readiness_protocol_revision="llama.cpp.openai.models.v1",
                spawn_adapter_sha256=(
                    self.descriptor_receipt.spawn_adapter_sha256
                ),
            ),
        )
        for receipt in cases:
            with self.subTest(receipt=receipt):
                path.write_text(receipt.to_json(), encoding="utf-8")
                try:
                    with self.assertRaises(PackageLayoutError) as caught:
                        write_package_manifest(self.root)
                    self.assertEqual(
                        caught.exception.code,
                        "package_descriptor_receipt_invalid",
                    )
                finally:
                    self._restore_writable_unmanifested_package()
                    path.write_text(
                        self.descriptor_receipt.to_json(),
                        encoding="utf-8",
                    )

    def test_unbundled_production_model_fails_closed(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        model = next((self.root / "models").glob("*.gguf"))
        model.unlink()

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_file_missing")

    def test_assembler_requires_model_and_descriptor_receipt_inputs(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            assemble_sidecar,
        )

        parameters = inspect.signature(assemble_sidecar).parameters

        self.assertIn("descriptor_binding_receipt", parameters)
        self.assertIs(
            parameters["descriptor_binding_receipt"].default,
            inspect.Parameter.empty,
        )
        self.assertIs(parameters["gguf"].default, inspect.Parameter.empty)

    def test_zero_byte_distribution_metadata_is_still_hashed(self) -> None:
        metadata = self._write(
            "_internal/pydantic-2.13.4.dist-info/REQUESTED",
            b"",
        )

        manifest = self._write_and_validate()

        entry = next(
            item
            for item in manifest.entries
            if item.relative_path
            == "_internal/pydantic-2.13.4.dist-info/REQUESTED"
        )
        self.assertEqual(entry.size_bytes, 0)
        self.assertEqual(
            entry.sha256,
            hashlib.sha256(b"").hexdigest(),
        )
        self.assertEqual(metadata.stat().st_size, 0)

    def test_required_runtime_files_cannot_be_empty(self) -> None:
        (self.root / "WaylineForge").write_bytes(b"")

        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_file_missing")

    def test_digest_tampering_is_rejected(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            validate_packaged_layout,
            write_package_manifest,
        )

        write_package_manifest(self.root)
        target = self.root / "resources/curriculum_v1.json"
        target.chmod(0o600)
        target.write_bytes(b'{"tampered":true}\n')
        target.chmod(0o400)

        with self.assertRaises(PackageLayoutError) as caught:
            validate_packaged_layout(self.root)

        self.assertEqual(caught.exception.code, "package_digest_mismatch")

    def test_symlink_and_secret_file_are_rejected_before_manifest_write(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        linked = self.root / "resources/linked.json"
        linked.symlink_to(self.root / "resources/curriculum_v1.json")
        with self.assertRaises(PackageLayoutError) as symlink_error:
            write_package_manifest(self.root)
        self.assertEqual(symlink_error.exception.code, "package_symlink_forbidden")

        linked.unlink()
        self._write(".env", b"TFY_API_KEY=must-not-ship")
        with self.assertRaises(PackageLayoutError) as secret_error:
            write_package_manifest(self.root)
        self.assertEqual(secret_error.exception.code, "package_secret_forbidden")

    def test_secret_content_scan_is_case_insensitive(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        self._write("resources/release-note.txt", b"TFY_API_KEY=must-not-ship")

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_secret_forbidden")

    def test_secret_name_markers_are_rejected_in_every_path_component(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        for marker in (
            ".env",
            "api_key",
            "apikey",
            "credentials",
            "hf_token",
            "private_key",
            "secret",
        ):
            with self.subTest(marker=marker):
                marker_root = self.root / "_internal" / marker
                self._write(
                    f"_internal/{marker}/opaque-runtime.bin",
                    b"otherwise-safe-runtime-data",
                )

                try:
                    with self.assertRaises(PackageLayoutError) as caught:
                        write_package_manifest(self.root)
                    self.assertEqual(
                        caught.exception.code,
                        "package_secret_forbidden",
                    )
                finally:
                    self._restore_writable_unmanifested_package()
                    shutil.rmtree(marker_root)

    def test_secret_scan_covers_binary_suffix_after_first_mebibyte(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        self._write(
            "_internal/opaque-runtime.blob",
            b"x" * (1024 * 1024 + 37) + b"TFY_API_KEY=must-not-ship",
        )

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_secret_forbidden")

    def test_secret_scan_detects_marker_split_across_stream_chunks(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        marker = b"AUTHORIZATION: BEARER must-not-ship"
        self._write(
            "_internal/chunked-runtime.data",
            b"x" * (1024 * 1024 - 7) + marker,
        )

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_secret_forbidden")

    def test_deflated_zip_member_secret_is_rejected(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        archive = self.root / "_internal/base_library.zip"
        with zipfile.ZipFile(
            archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as bundle:
            bundle.writestr("config.txt", "TFY_API_KEY=must-not-ship")

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_secret_forbidden")

    def test_python_source_hidden_in_zip_member_is_rejected(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        archive = self.root / "_internal/base_library.zip"
        with zipfile.ZipFile(
            archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as bundle:
            bundle.writestr("heldout.py", "print('must not ship')\n")

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_source_forbidden")

    def test_unrecognized_compressed_outer_payloads_are_rejected(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        cases = (
            gzip.compress(b"TFY_API_KEY=must-not-ship"),
            b"\xfd7zXZ\x00opaque-xz-payload",
            b"BZh9opaque-bzip-payload",
        )
        target = self.root / "_internal/opaque-runtime.bin"

        for payload in cases:
            with self.subTest(magic=payload[:6]):
                target.write_bytes(payload)
                try:
                    with self.assertRaises(PackageLayoutError) as caught:
                        write_package_manifest(self.root)
                    self.assertEqual(
                        caught.exception.code,
                        "package_unexpected_file",
                    )
                finally:
                    self._restore_writable_unmanifested_package()

    def test_prefixed_zip_payload_is_rejected_outside_canonical_path(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        archive = Path(self.temporary.name) / "payload.zip"
        with zipfile.ZipFile(
            archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as bundle:
            bundle.writestr("config.txt", "TFY_API_KEY=must-not-ship")
        self._write(
            "_internal/opaque-runtime.bin",
            b"MZstub" + archive.read_bytes(),
        )

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_unexpected_file")

    def test_data_after_zip_eocd_is_rejected(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        archive = self.root / "_internal/base_library.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("safe.pyc", b"bytecode")
        with archive.open("ab") as output:
            output.write(gzip.compress(b"TFY_API_KEY=must-not-ship"))

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_unexpected_file")

    def test_windows_or_traversal_zip_member_names_are_rejected(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        archive = self.root / "_internal/base_library.zip"
        for member_name in (r"..\evil.pyc", "C:/evil.pyc", "/evil.pyc"):
            with self.subTest(member_name=member_name):
                with zipfile.ZipFile(
                    archive,
                    "w",
                    compression=zipfile.ZIP_STORED,
                ) as bundle:
                    bundle.writestr(member_name, b"bytecode")
                try:
                    with self.assertRaises(PackageLayoutError) as caught:
                        write_package_manifest(self.root)
                    self.assertEqual(
                        caught.exception.code,
                        "package_unexpected_file",
                    )
                finally:
                    self._restore_writable_unmanifested_package()

    def test_oversized_zip_is_rejected_before_zipfile_parsing(self) -> None:
        from services.wayline_forge.scripts import build_mac_sidecar

        archive = self.root / "_internal/base_library.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("oversized.pyc", b"x" * (17 * 1024 * 1024))

        with patch.object(
            build_mac_sidecar.zipfile,
            "ZipFile",
            side_effect=AssertionError("ZipFile must not inspect oversized input"),
        ):
            with self.assertRaises(
                build_mac_sidecar.PackageLayoutError
            ) as caught:
                build_mac_sidecar.write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_unexpected_file")

    def test_oversized_zip_central_directory_is_rejected(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        archive = self.root / "_internal/base_library.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            for index in range(400):
                bundle.writestr(
                    f"pkg/{index:04d}_{'x' * 700}.pyc",
                    b"",
                )

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_unexpected_file")

    def test_research_dataset_and_python_source_are_not_shippable(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        self._write("data/train_v7.jsonl", b"{}\n")
        with self.assertRaises(PackageLayoutError) as dataset_error:
            write_package_manifest(self.root)
        self.assertEqual(dataset_error.exception.code, "package_research_forbidden")

        shutil.rmtree(self.root / "data")
        self._write("_internal/debug.py", b"print('source')\n")
        with self.assertRaises(PackageLayoutError) as source_error:
            write_package_manifest(self.root)
        self.assertEqual(source_error.exception.code, "package_source_forbidden")

    def test_all_source_only_python_suffixes_are_not_shippable(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        for suffix in (".py", ".pyw", ".pyi", ".pyx", ".pxd", ".pxi"):
            with self.subTest(suffix=suffix):
                source = self._write(
                    f"_internal/debug{suffix}",
                    b"print('source-only input')\n",
                )

                try:
                    with self.assertRaises(PackageLayoutError) as caught:
                        write_package_manifest(self.root)
                    self.assertEqual(
                        caught.exception.code,
                        "package_source_forbidden",
                    )
                finally:
                    self._restore_writable_unmanifested_package()
                    source.unlink()

        self._write("_internal/runtime.pyc", b"allowed-bytecode")
        manifest = self._write_and_validate()
        self.assertIn(
            "_internal/runtime.pyc",
            {entry.relative_path for entry in manifest.entries},
        )

    def test_only_reviewed_cache_current_generation_files_are_allowed(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        cache_root = self.root / "resources/reviewed_cache_release_v1"
        stale = "generation-" + "f" * 64
        cases = (
            (
                cache_root / "generations" / stale / "reviewed_cache.sqlite3",
                b"SQLite format 3\x00stale",
            ),
            (cache_root / "release-notes.txt", b"unreviewed extra"),
        )

        for extra_path, payload in cases:
            with self.subTest(path=extra_path.relative_to(cache_root)):
                extra_path.parent.mkdir(parents=True, exist_ok=True)
                extra_path.write_bytes(payload)
                try:
                    with self.assertRaises(PackageLayoutError) as caught:
                        write_package_manifest(self.root)
                    self.assertEqual(
                        caught.exception.code,
                        "package_unexpected_file",
                    )
                finally:
                    self._restore_writable_unmanifested_package()
                    if extra_path.exists():
                        extra_path.unlink()
                    if extra_path.parent.name == stale:
                        extra_path.parent.rmdir()

    def test_cache_staging_copies_only_current_canonical_generation(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            _copy_current_reviewed_cache,
        )

        source = self.root / "resources/reviewed_cache_release_v1"
        stale = source / "generations" / ("generation-" + "e" * 64)
        stale.mkdir(parents=True)
        (stale / "reviewed_cache.sqlite3").write_bytes(
            b"SQLite format 3\x00stale"
        )
        (source / ".publish.lock").write_bytes(b"")
        destination = Path(self.temporary.name) / "staged-cache"

        _copy_current_reviewed_cache(source, destination)

        source_pointer = json.loads(
            (source / "current.json").read_text(encoding="utf-8")
        )
        current = source_pointer["generationId"]
        self.assertEqual(
            {
                path.relative_to(destination).as_posix()
                for path in destination.rglob("*")
                if path.is_file()
            },
            {
                "current.json",
                f"generations/{current}/reviewed_cache_manifest.json",
                f"generations/{current}/reviewed_cache.sqlite3",
            },
        )

    def test_pyinstaller_staging_rejects_unknown_top_level_outputs(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            _copy_pyinstaller_tree,
        )

        source = Path(self.temporary.name) / "pyinstaller-output"
        source.mkdir()
        (source / "WaylineForge").write_bytes(b"executable")
        (source / "_internal").mkdir()
        (source / "_internal/runtime.bin").write_bytes(b"runtime")
        (source / "unreviewed.bin").write_bytes(b"unexpected")

        with self.assertRaises(PackageLayoutError) as caught:
            _copy_pyinstaller_tree(
                source,
                Path(self.temporary.name) / "copied-output",
            )

        self.assertEqual(caught.exception.code, "package_unexpected_file")
    def test_regular_file_hardlink_is_rejected(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        external = Path(self.temporary.name) / "external-sensitive.bin"
        external.write_bytes(b"external-sensitive-value")
        linked = self.root / "_internal/external-alias.bin"
        os.link(external, linked)
        self.assertEqual(linked.stat().st_nlink, 2)

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_hardlink_forbidden")

    def test_same_size_path_swap_during_scan_is_rejected(self) -> None:
        from services.wayline_forge.scripts import build_mac_sidecar

        original_payload = b"a" * 128
        replacement_payload = b"b" * len(original_payload)
        target = self._write("_internal/swap-target.bin", original_payload)
        replacement = Path(self.temporary.name) / "same-size-replacement.bin"
        replacement.write_bytes(replacement_payload)
        original_inode = target.stat().st_ino
        real_read = os.read
        swapped = False

        def swap_then_read(descriptor: int, size: int) -> bytes:
            nonlocal swapped
            if not swapped and os.fstat(descriptor).st_ino == original_inode:
                os.replace(replacement, target)
                swapped = True
            return real_read(descriptor, size)

        with patch.object(
            build_mac_sidecar.os,
            "read",
            side_effect=swap_then_read,
        ):
            with self.assertRaises(build_mac_sidecar.PackageLayoutError) as caught:
                build_mac_sidecar.write_package_manifest(self.root)

        self.assertTrue(swapped)
        self.assertEqual(caught.exception.code, "package_unsafe_path")

    def test_preexisting_regular_package_manifest_is_never_overwritten(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        manifest_path = self._write(
            "package_manifest_v1.json",
            b"preexisting-manifest",
        )

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_manifest_invalid")
        self.assertEqual(manifest_path.read_bytes(), b"preexisting-manifest")

    def test_preexisting_hardlinked_package_manifest_is_never_overwritten(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        external = Path(self.temporary.name) / "external-manifest.json"
        external.write_bytes(b"external-manifest")
        manifest_path = self.root / "package_manifest_v1.json"
        os.link(external, manifest_path)

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_manifest_invalid")
        self.assertEqual(external.read_bytes(), b"external-manifest")

    def test_manifest_and_package_permissions_are_read_only(self) -> None:
        self._write_and_validate()

        for path in (self.root, *self.root.rglob("*")):
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_dir():
                self.assertEqual(mode, 0o500)
            elif path.relative_to(self.root).as_posix() in {
                "WaylineForge",
                "bin/llama-server",
            }:
                self.assertEqual(mode, 0o500)
            else:
                self.assertEqual(mode, 0o400)

    def test_owner_writable_package_directory_is_rejected(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            validate_packaged_layout,
            write_package_manifest,
        )

        write_package_manifest(self.root)
        (self.root / "resources").chmod(0o700)

        with self.assertRaises(PackageLayoutError) as caught:
            validate_packaged_layout(self.root)

        self.assertEqual(caught.exception.code, "package_permissions_invalid")

    def test_missing_llama_server_or_cache_fails_closed(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        (self.root / "bin/llama-server").unlink()
        with self.assertRaises(PackageLayoutError) as missing_binary:
            write_package_manifest(self.root)
        self.assertEqual(missing_binary.exception.code, "package_file_missing")

        self._write("bin/llama-server", b"fake-pinned-llama-server")
        shutil.rmtree(self.root / "resources/reviewed_cache_release_v1")
        with self.assertRaises(PackageLayoutError) as missing_cache:
            write_package_manifest(self.root)
        self.assertEqual(missing_cache.exception.code, "package_file_missing")

    def test_reviewed_cache_pointer_digest_must_match_generation_manifest(
        self,
    ) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        pointer_path = (
            self.root / "resources/reviewed_cache_release_v1/current.json"
        )
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        pointer["manifestSha256"] = "e" * 64
        pointer_path.write_text(
            json.dumps(
                pointer,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_digest_mismatch")

    def test_reviewed_cache_generation_id_must_equal_manifest_digest(self) -> None:
        from services.wayline_forge.scripts.build_mac_sidecar import (
            PackageLayoutError,
            write_package_manifest,
        )

        pointer_path = (
            self.root / "resources/reviewed_cache_release_v1/current.json"
        )
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        self.assertNotEqual(pointer["manifestSha256"], "e" * 64)
        pointer["generationId"] = "generation-" + "e" * 64
        pointer_path.write_text(
            json.dumps(
                pointer,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        with self.assertRaises(PackageLayoutError) as caught:
            write_package_manifest(self.root)

        self.assertEqual(caught.exception.code, "package_digest_mismatch")

    def test_live_dependency_lock_is_hashed_and_excludes_research_stack(self) -> None:
        lock = (SERVICE_ROOT / "requirements-live.lock").read_text(
            encoding="utf-8"
        )
        normalized = lock.casefold()

        self.assertIn("autogenerated by pip-compile with python 3.12", normalized)
        self.assertIn("--hash=sha256:", normalized)
        for package in (
            "fastapi",
            "httpx",
            "jsonschema",
            "pydantic",
            "pyinstaller",
            "uvicorn",
        ):
            self.assertRegex(normalized, rf"(?m)^{package}==[0-9]")
        for banned in (
            "torch",
            "transformers",
            "peft",
            "unsloth",
            "jupyter",
        ):
            self.assertNotRegex(normalized, rf"(?m)^{banned}(?:==|\[)")

    def test_live_input_pins_only_the_six_runtime_tools(self) -> None:
        requirements = {
            line.strip()
            for line in (SERVICE_ROOT / "requirements-live.in")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertEqual(
            requirements,
            {
                "fastapi==0.139.0",
                "httpx==0.28.1",
                "jsonschema==4.26.0",
                "pydantic==2.13.4",
                "pyinstaller==6.21.0",
                "uvicorn[standard]==0.51.0",
            },
        )

    def test_pyinstaller_spec_is_directory_mode_without_upx_or_training_modules(
        self,
    ) -> None:
        spec = (SERVICE_ROOT / "WaylineForge.spec").read_text(encoding="utf-8")

        self.assertIn("COLLECT(", spec)
        self.assertIn("exclude_binaries=True", spec)
        self.assertIn("upx=False", spec)
        for banned in (
            "jupyter",
            "peft",
            "torch",
            "transformers",
            "unsloth",
        ):
            self.assertIn(repr(banned), spec)


if __name__ == "__main__":
    unittest.main()
