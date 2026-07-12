from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


class _Child:
    def __init__(self, *, terminate_times_out: bool = False) -> None:
        self.terminate_times_out = terminate_times_out
        self.terminated = 0
        self.killed = 0
        self.waited = 0

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1

    def wait(self, *, timeout: float) -> int:
        self.waited += 1
        if self.terminate_times_out and self.killed == 0:
            raise subprocess.TimeoutExpired("llama-server", timeout)
        return -9 if self.killed else -15


class ProductionSpawnAdapterTests(unittest.IsolatedAsyncioTestCase):
    def production_spawn(self):
        self.assertIsNotNone(
            importlib.util.find_spec(
                "services.wayline_forge.app.production_spawn"
            ),
            "production spawn adapter module is required",
        )
        return importlib.import_module(
            "services.wayline_forge.app.production_spawn"
        )

    def test_adapter_has_one_reproducible_callback_contract_identity(self) -> None:
        production_spawn = self.production_spawn()

        expected_payload = {
            "callbackContract": "wayline_child_created.v1",
            "cleanup": "terminate-kill-wait.v1",
            "implementation": "python.subprocess.Popen",
            "schemaVersion": "wayline.production-spawn-adapter.v1",
        }
        expected = hashlib.sha256(
            json.dumps(
                expected_payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        factory = production_spawn.ProductionPopenFactory()

        self.assertIs(factory.wayline_child_created_callback, True)
        self.assertEqual(factory.wayline_spawn_adapter_sha256, expected)
        self.assertEqual(production_spawn.PRODUCTION_SPAWN_ADAPTER_SHA256, expected)

    def test_adapter_publishes_the_exact_created_child(self) -> None:
        production_spawn = self.production_spawn()

        child = _Child()
        observed: list[object] = []
        factory = production_spawn.ProductionPopenFactory()

        with patch.object(production_spawn.subprocess, "Popen", return_value=child) as popen:
            returned = factory(
                ("/dev/fd/10", "--model", "/dev/fd/11"),
                wayline_child_created=observed.append,
                shell=False,
            )

        self.assertIs(returned, child)
        self.assertEqual(observed, [child])
        popen.assert_called_once_with(
            ("/dev/fd/10", "--model", "/dev/fd/11"),
            shell=False,
        )

    def test_callback_failure_terminates_and_reaps_the_created_child(self) -> None:
        production_spawn = self.production_spawn()

        child = _Child()
        factory = production_spawn.ProductionPopenFactory()

        def reject(_child: object) -> None:
            raise RuntimeError("ownership publication failed")

        with patch.object(production_spawn.subprocess, "Popen", return_value=child):
            with self.assertRaisesRegex(RuntimeError, "ownership publication failed"):
                factory(("llama-server",), wayline_child_created=reject)

        self.assertEqual(child.terminated, 1)
        self.assertEqual(child.killed, 0)
        self.assertEqual(child.waited, 1)

    def test_callback_failure_kills_after_bounded_terminate_timeout(self) -> None:
        production_spawn = self.production_spawn()

        child = _Child(terminate_times_out=True)
        factory = production_spawn.ProductionPopenFactory()

        with patch.object(production_spawn.subprocess, "Popen", return_value=child):
            with self.assertRaisesRegex(RuntimeError, "ownership publication failed"):
                factory(
                    ("llama-server",),
                    wayline_child_created=lambda _child: (_ for _ in ()).throw(
                        RuntimeError("ownership publication failed")
                    ),
                )

        self.assertEqual(child.terminated, 1)
        self.assertEqual(child.killed, 1)
        self.assertEqual(child.waited, 2)

    async def test_mac_driver_accepts_only_the_matching_production_adapter_receipt(
        self,
    ) -> None:
        production_spawn = self.production_spawn()
        from services.wayline_forge.app.macos_worker_runtime import (
            DescriptorBindingReleaseReceipt,
            build_macos_worker_driver,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            lock_root = root / "worker"
            lock_root.mkdir(mode=0o700)
            receipt = DescriptorBindingReleaseReceipt.attest(
                binary_sha256="1" * 64,
                model_sha256="2" * 64,
                llama_cpp_revision="3" * 40,
                os_name="Darwin",
                architecture="arm64",
                readiness_protocol_revision="llama.cpp.openai.models.v1",
                spawn_adapter_sha256=(
                    production_spawn.PRODUCTION_SPAWN_ADAPTER_SHA256
                ),
            )
            driver = build_macos_worker_driver(
                binary_root=str(root),
                model_root=str(root),
                lock_path=str(lock_root / "llama.lock"),
                cwd=str(root),
                environment={},
                release_receipt=receipt,
                popen_factory=production_spawn.ProductionPopenFactory(),
            )

            self.assertTrue(driver.descriptor_binding_supported)
            self.assertIs(driver.descriptor_binding_release_receipt, receipt)
            self.assertEqual(
                await driver.shutdown_all(deadline=driver._clock() + 1.0),
                (),
            )


if __name__ == "__main__":
    unittest.main()
