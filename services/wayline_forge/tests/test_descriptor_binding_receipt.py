from __future__ import annotations

import json
import unittest

import services.wayline_forge.app.macos_worker_runtime as runtime


class DescriptorBindingReceiptTests(unittest.TestCase):
    def receipt(self):
        from services.wayline_forge.app.macos_worker_runtime import (
            DescriptorBindingReleaseReceipt,
        )

        return DescriptorBindingReleaseReceipt.attest(
            binary_sha256="1" * 64,
            model_sha256="2" * 64,
            llama_cpp_revision="3" * 40,
            os_name="Darwin",
            architecture="arm64",
            readiness_protocol_revision="llama.cpp.openai.models.v1",
            spawn_adapter_sha256="4" * 64,
        )

    def test_canonical_receipt_round_trips_exactly(self) -> None:
        self.assertTrue(
            hasattr(runtime, "parse_descriptor_binding_release_receipt"),
            "production receipt parser is required",
        )
        parse_descriptor_binding_release_receipt = (
            runtime.parse_descriptor_binding_release_receipt
        )

        receipt = self.receipt()
        payload = receipt.to_json()

        self.assertEqual(
            parse_descriptor_binding_release_receipt(payload.encode("utf-8")),
            receipt,
        )
        self.assertEqual(
            json.loads(payload),
            {
                "architecture": "arm64",
                "binarySha256": "1" * 64,
                "llamaCppRevision": "3" * 40,
                "modelSha256": "2" * 64,
                "osName": "Darwin",
                "readinessProtocolRevision": "llama.cpp.openai.models.v1",
                "schemaVersion": "wayline.descriptor-binding-release-receipt.v1",
                "spawnAdapterSha256": "4" * 64,
            },
        )

    def test_duplicate_or_unknown_fields_are_rejected(self) -> None:
        self.assertTrue(
            hasattr(runtime, "DescriptorBindingReceiptError"),
            "typed receipt failure is required",
        )
        DescriptorBindingReceiptError = runtime.DescriptorBindingReceiptError
        parse_descriptor_binding_release_receipt = (
            runtime.parse_descriptor_binding_release_receipt
        )

        canonical = self.receipt().to_json()
        duplicate = canonical.replace(
            '"architecture":"arm64"',
            '"architecture":"arm64","architecture":"arm64"',
        )
        unknown = canonical[:-1] + ',"extra":true}'

        for payload in (duplicate, unknown):
            with self.subTest(payload=payload), self.assertRaises(
                DescriptorBindingReceiptError
            ):
                parse_descriptor_binding_release_receipt(payload)

    def test_noncanonical_or_wrong_platform_receipts_are_rejected(self) -> None:
        self.assertTrue(
            hasattr(runtime, "DescriptorBindingReceiptError"),
            "typed receipt failure is required",
        )
        DescriptorBindingReceiptError = runtime.DescriptorBindingReceiptError
        parse_descriptor_binding_release_receipt = (
            runtime.parse_descriptor_binding_release_receipt
        )

        canonical = self.receipt().to_json()
        cases = (
            canonical + "\n",
            canonical.replace('"osName":"Darwin"', '"osName":"Linux"'),
            canonical.replace('"architecture":"arm64"', '"architecture":"x86_64"'),
            canonical.replace(
                '"readinessProtocolRevision":"llama.cpp.openai.models.v1"',
                '"readinessProtocolRevision":"unreviewed"',
            ),
        )

        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(
                DescriptorBindingReceiptError
            ):
                parse_descriptor_binding_release_receipt(payload)


if __name__ == "__main__":
    unittest.main()
