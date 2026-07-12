from __future__ import annotations

import hashlib
import errno
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import zipfile

from services.wayline_forge.scripts.build_export_inputs import (
    EXPORT_INPUTS_RECEIPT_SCHEMA_VERSION,
    ExportInputsError,
    build_export_inputs,
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class ExportInputsBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.notebook = self.root / "notebooks/export_wayline_gguf_colab.ipynb"
        self.notebook.parent.mkdir(mode=0o700)
        self.output_parent = self.root / "data/wayline/runtime"
        self.output_parent.mkdir(parents=True, mode=0o700)
        self.bundle = (
            self.output_parent / "wayline_export_inputs_v1.bundle"
        )
        self.archive = self.bundle / "wayline_export_inputs_v1.zip"
        self.receipt = self.bundle / "wayline_export_inputs_v1.receipt.json"
        self.legacy_archive = self.output_parent / self.archive.name
        self.legacy_receipt = self.output_parent / self.receipt.name
        self.payloads = {
            f"verified/section-{index:02d}/input-{index:02d}.txt": (
                f"verified export input {index}\n".encode("ascii")
            )
            for index in range(22)
        }
        for relative, payload in self.payloads.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(payload)
            path.chmod(0o600)
        self.policy = {
            relative: _sha256(payload)
            for relative, payload in self.payloads.items()
        }
        self.write_policy(self.policy)

    def write_notebook_source(self, source: str) -> None:
        self.notebook.write_bytes(
            _canonical_json(
                {
                    "cells": [
                        {
                            "cell_type": "markdown",
                            "metadata": {},
                            "source": ["# fixture\n"],
                        },
                        {
                            "cell_type": "code",
                            "execution_count": None,
                            "metadata": {},
                            "outputs": [],
                            "source": [source],
                        },
                    ],
                    "metadata": {},
                    "nbformat": 4,
                    "nbformat_minor": 5,
                }
            )
        )
        self.notebook.chmod(0o600)

    def write_policy(self, policy: dict[str, str]) -> None:
        self.write_notebook_source(
            "EXPECTED_INPUT_SHA256 = " + repr(policy) + "\n"
        )

    def build(self):
        return build_export_inputs(
            self.root,
            notebook_path="notebooks/export_wayline_gguf_colab.ipynb",
            bundle_path=self.bundle,
        )

    def assert_error(self, code: str) -> ExportInputsError:
        with self.assertRaises(ExportInputsError) as caught:
            self.build()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn(str(self.root), repr(caught.exception))
        return caught.exception

    def test_build_is_byte_deterministic_and_zip_metadata_is_exact(self) -> None:
        first = self.build()
        first_archive = self.archive.read_bytes()
        first_receipt = self.receipt.read_bytes()
        first_identities = tuple(
            (
                path.stat().st_dev,
                path.stat().st_ino,
                path.stat().st_mode,
                path.stat().st_size,
                path.stat().st_mtime_ns,
                path.stat().st_ctime_ns,
            )
            for path in (self.bundle, self.archive, self.receipt)
        )
        second = self.build()

        self.assertEqual(self.archive.read_bytes(), first_archive)
        self.assertEqual(self.receipt.read_bytes(), first_receipt)
        self.assertEqual(first.archive_sha256, second.archive_sha256)
        self.assertEqual(first.archive_size, len(first_archive))
        self.assertEqual(first.item_count, 22)
        self.assertEqual(
            first_identities,
            tuple(
                (
                    path.stat().st_dev,
                    path.stat().st_ino,
                    path.stat().st_mode,
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                    path.stat().st_ctime_ns,
                )
                for path in (self.bundle, self.archive, self.receipt)
            ),
        )
        self.assertEqual(stat.S_IMODE(self.bundle.stat().st_mode), 0o555)
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o444)
        self.assertEqual(stat.S_IMODE(self.receipt.stat().st_mode), 0o444)

        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(archive.comment, b"")
            infos = archive.infolist()
            self.assertEqual(
                [info.filename for info in infos],
                sorted(self.policy),
            )
            self.assertEqual(len(infos), 22)
            self.assertEqual(len({info.filename for info in infos}), 22)
            for info in infos:
                with self.subTest(path=info.filename):
                    self.assertFalse(info.is_dir())
                    self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                    self.assertEqual(info.create_system, 3)
                    self.assertEqual(info.extra, b"")
                    self.assertEqual(info.comment, b"")
                    mode = info.external_attr >> 16
                    self.assertTrue(stat.S_ISREG(mode))
                    self.assertEqual(stat.S_IMODE(mode), 0o444)
                    payload = archive.read(info)
                    self.assertEqual(_sha256(payload), self.policy[info.filename])

        receipt = json.loads(first_receipt)
        self.assertEqual(_canonical_json(receipt), first_receipt)
        self.assertEqual(
            set(receipt),
            {
                "archive",
                "files",
                "notebookSha256",
                "schemaVersion",
            },
        )
        self.assertEqual(
            receipt["schemaVersion"],
            EXPORT_INPUTS_RECEIPT_SCHEMA_VERSION,
        )
        self.assertEqual(
            receipt["notebookSha256"],
            _sha256(self.notebook.read_bytes()),
        )
        self.assertEqual(
            receipt["archive"],
            {
                "fileName": self.archive.name,
                "sha256": _sha256(first_archive),
                "sizeBytes": len(first_archive),
            },
        )
        self.assertEqual(
            receipt["files"],
            [
                {
                    "path": relative,
                    "sha256": self.policy[relative],
                    "sizeBytes": len(self.payloads[relative]),
                }
                for relative in sorted(self.policy)
            ],
        )

    def test_missing_duplicate_or_nonliteral_policy_assignment_is_rejected(
        self,
    ) -> None:
        cases = (
            "value = 1\n",
            (
                "EXPECTED_INPUT_SHA256 = "
                + repr(self.policy)
                + "\nEXPECTED_INPUT_SHA256 = "
                + repr(self.policy)
                + "\n"
            ),
            "EXPECTED_INPUT_SHA256 = dict()\n",
        )
        for source in cases:
            with self.subTest(source=source[:32]):
                self.write_notebook_source(source)
                self.assert_error("invalid_notebook_policy")

    def test_traversal_secret_duplicate_path_and_wrong_count_are_rejected(
        self,
    ) -> None:
        traversal = dict(self.policy)
        traversal.pop(next(iter(traversal)))
        traversal["../outside.txt"] = "0" * 64
        secret = dict(self.policy)
        secret.pop(next(iter(secret)))
        secret[".env"] = "0" * 64
        wrong_count = dict(list(self.policy.items())[:-1])
        duplicate_literal = (
            "EXPECTED_INPUT_SHA256 = {"
            + ",".join(
                repr(key) + ":" + repr(value)
                for key, value in self.policy.items()
            )
            + ","
            + repr(next(iter(self.policy)))
            + ":"
            + repr(next(iter(self.policy.values())))
            + "}\n"
        )
        for policy in (traversal, secret, wrong_count):
            with self.subTest(policy=list(policy)[-1]):
                self.write_policy(policy)
                self.assert_error("invalid_input_allowlist")
        self.write_notebook_source(duplicate_literal)
        self.assert_error("invalid_input_allowlist")

    def test_symlink_and_digest_tamper_fail_closed(self) -> None:
        relative = sorted(self.policy)[0]
        path = self.root / relative
        original = path.read_bytes()
        target = self.root / "outside.txt"
        target.write_bytes(original)
        path.unlink()
        path.symlink_to(target)
        self.assert_error("unsafe_export_input")

        path.unlink()
        path.write_bytes(original + b"tamper")
        path.chmod(0o600)
        self.assert_error("export_input_digest_mismatch")

    def test_validation_failure_leaves_existing_outputs_unchanged(self) -> None:
        archive_sentinel = b"existing archive sentinel"
        receipt_sentinel = b"existing receipt sentinel"
        self.bundle.mkdir(mode=0o700)
        self.archive.write_bytes(archive_sentinel)
        self.receipt.write_bytes(receipt_sentinel)
        self.archive.chmod(0o444)
        self.receipt.chmod(0o444)
        self.bundle.chmod(0o555)
        relative = sorted(self.policy)[-1]
        (self.root / relative).write_bytes(b"tampered")

        self.assert_error("export_input_digest_mismatch")

        self.assertEqual(self.archive.read_bytes(), archive_sentinel)
        self.assertEqual(self.receipt.read_bytes(), receipt_sentinel)
        self.assertEqual(
            list(self.output_parent.glob(".*.stage")),
            [],
        )

    def test_failed_stage_file_creation_never_unlinks_an_unowned_name(
        self,
    ) -> None:
        real_open = os.open
        real_close = os.close
        created_name: list[str] = []

        def fail_after_foreign_name_appears(path, flags, *args, **kwargs):
            if path == self.archive.name and kwargs.get("dir_fd") is not None:
                descriptor = real_open(path, flags, *args, **kwargs)
                real_close(descriptor)
                created_name.append(path)
                raise PermissionError("simulated creation race")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch(
            "services.wayline_forge.scripts.build_export_inputs.os.open",
            side_effect=fail_after_foreign_name_appears,
        ):
            with self.assertRaises(ExportInputsError) as caught:
                self.build()

        self.assertEqual(caught.exception.code, "export_inputs_write_failed")
        self.assertIsInstance(caught.exception.__cause__, ExportInputsError)
        self.assertEqual(
            caught.exception.__cause__.code,
            "export_inputs_cleanup_failed",
        )
        self.assertEqual(len(created_name), 1)
        stages = list(self.output_parent.glob(".*.stage"))
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / created_name[0]).exists())

    def test_publication_uses_one_directory_rename_and_no_file_replace(
        self,
    ) -> None:
        rename_calls: list[tuple[str, str]] = []

        def rename_directory(source_fd, source, destination_fd, destination):
            rename_calls.append((source, destination))
            os.rename(
                source,
                destination,
                src_dir_fd=source_fd,
                dst_dir_fd=destination_fd,
            )

        with mock.patch(
            "services.wayline_forge.scripts.build_export_inputs."
            "_rename_directory_exclusive",
            side_effect=rename_directory,
            create=True,
        ), mock.patch(
            "services.wayline_forge.scripts.build_export_inputs.os.replace",
            side_effect=AssertionError("file replacement is forbidden"),
        ):
            self.build()

        self.assertEqual(
            rename_calls,
            [(rename_calls[0][0], self.bundle.name)],
        )
        self.assertTrue(self.archive.is_file())
        self.assertTrue(self.receipt.is_file())

    def test_failed_directory_rename_leaves_no_partial_bundle(self) -> None:
        with mock.patch(
            "services.wayline_forge.scripts.build_export_inputs."
            "_rename_directory_exclusive",
            side_effect=OSError(errno.EIO, "simulated rename failure"),
            create=True,
        ):
            self.assert_error("export_bundle_publish_failed")

        self.assertFalse(self.bundle.exists())
        self.assertEqual(list(self.output_parent.glob(".*.stage")), [])

    def test_rename_uncertainty_exposes_only_a_complete_exact_bundle(
        self,
    ) -> None:
        def rename_then_raise(source_fd, source, destination_fd, destination):
            os.rename(
                source,
                destination,
                src_dir_fd=source_fd,
                dst_dir_fd=destination_fd,
            )
            raise OSError(errno.EIO, "simulated ambiguous rename result")

        with mock.patch(
            "services.wayline_forge.scripts.build_export_inputs."
            "_rename_directory_exclusive",
            side_effect=rename_then_raise,
            create=True,
        ):
            self.assert_error("export_bundle_durability_uncertain")

        archive_raw = self.archive.read_bytes()
        receipt_raw = self.receipt.read_bytes()
        self.assertEqual(json.loads(receipt_raw)["archive"]["sha256"], _sha256(archive_raw))
        self.assertEqual(stat.S_IMODE(self.bundle.stat().st_mode), 0o555)
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o444)
        self.assertEqual(stat.S_IMODE(self.receipt.stat().st_mode), 0o444)
        self.assertEqual(list(self.output_parent.glob(".*.stage")), [])

        repeated = self.build()
        self.assertEqual(repeated.archive_sha256, _sha256(archive_raw))
        self.assertEqual(self.archive.read_bytes(), archive_raw)
        self.assertEqual(self.receipt.read_bytes(), receipt_raw)

    def test_existing_invalid_bundle_is_never_mutated(self) -> None:
        self.bundle.mkdir(mode=0o700)
        self.archive.write_bytes(b"invalid archive sentinel")
        self.archive.chmod(0o444)
        self.bundle.chmod(0o555)
        before = (
            self.bundle.stat().st_ino,
            self.bundle.stat().st_mode,
            self.archive.stat().st_ino,
            self.archive.read_bytes(),
        )

        self.assert_error("existing_export_bundle_invalid")

        self.assertEqual(
            before,
            (
                self.bundle.stat().st_ino,
                self.bundle.stat().st_mode,
                self.archive.stat().st_ino,
                self.archive.read_bytes(),
            ),
        )
        self.assertFalse(self.receipt.exists())
        self.assertEqual(list(self.output_parent.glob(".*.stage")), [])

    def test_close_failure_is_reported_without_closing_a_reused_fd(self) -> None:
        real_close = os.close
        real_open = os.open
        reused_descriptor: list[int] = []

        def reuse_then_fail(descriptor: int) -> None:
            if reused_descriptor:
                real_close(descriptor)
                return
            real_close(descriptor)
            replacement = real_open(os.devnull, os.O_RDONLY)
            if replacement != descriptor:
                os.dup2(replacement, descriptor)
                real_close(replacement)
            reused_descriptor.append(descriptor)
            raise OSError(errno.EIO, "simulated ambiguous close failure")

        try:
            with mock.patch(
                "services.wayline_forge.scripts.build_export_inputs.os.close",
                side_effect=reuse_then_fail,
            ):
                self.assert_error("export_inputs_cleanup_failed")
            self.assertEqual(len(reused_descriptor), 1)
            os.fstat(reused_descriptor[0])
        finally:
            if reused_descriptor:
                try:
                    real_close(reused_descriptor[0])
                except OSError:
                    pass

    def test_only_an_exact_legacy_pair_is_removed_after_bundle_commit(
        self,
    ) -> None:
        self.build()
        archive_raw = self.archive.read_bytes()
        receipt_raw = self.receipt.read_bytes()
        self.bundle.chmod(0o700)
        self.archive.unlink()
        self.receipt.unlink()
        self.bundle.rmdir()
        self.legacy_archive.write_bytes(archive_raw)
        self.legacy_receipt.write_bytes(receipt_raw)
        self.legacy_archive.chmod(0o444)
        self.legacy_receipt.chmod(0o444)

        with mock.patch(
            "services.wayline_forge.scripts.build_export_inputs."
            "_KNOWN_LEGACY_ARCHIVE_SHA256",
            _sha256(archive_raw),
            create=True,
        ), mock.patch(
            "services.wayline_forge.scripts.build_export_inputs."
            "_KNOWN_LEGACY_RECEIPT_SHA256",
            _sha256(receipt_raw),
            create=True,
        ):
            self.build()

        self.assertEqual(self.archive.read_bytes(), archive_raw)
        self.assertEqual(self.receipt.read_bytes(), receipt_raw)
        self.assertFalse(self.legacy_archive.exists())
        self.assertFalse(self.legacy_receipt.exists())

        self.bundle.chmod(0o700)
        self.archive.unlink()
        self.receipt.unlink()
        self.bundle.rmdir()
        self.legacy_archive.write_bytes(b"foreign legacy archive")
        self.legacy_archive.chmod(0o444)
        self.assert_error("legacy_export_artifact_conflict")
        self.assertEqual(
            self.legacy_archive.read_bytes(),
            b"foreign legacy archive",
        )
        self.assertFalse(self.bundle.exists())


if __name__ == "__main__":
    unittest.main()
