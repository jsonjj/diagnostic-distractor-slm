from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.wayline_forge.app.curriculum import Curriculum
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.llama_worker import WorkerState
from services.wayline_forge.app.model_manifest import parse_model_manifest
from services.wayline_forge.app.procedure_registry import ProcedureRegistry
from services.wayline_forge.app.providers.distractor import (
    PinnedSlmManifest,
    RawSlmGeneration,
)
from services.wayline_forge.app.question_kernel import (
    CompileRequest,
    QuestionCompiler,
)
from services.wayline_forge.app.slm_prompt import (
    INFERENCE_PARAMETERS,
    PROMPT_TEMPLATE_SHA256,
    build_slm_request,
)
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle
from services.wayline_forge.scripts.build_reviewed_cache import (
    BUILD_APPROVAL_SCHEMA_VERSION,
    BUILD_INPUT_SCHEMA_VERSION,
)
from services.wayline_forge.scripts.publish_reviewed_cache import (
    publish_reviewed_cache,
)


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


class _DriverProbe:
    def __init__(self, *, descriptor_binding_supported: bool = True) -> None:
        self.descriptor_binding_supported = descriptor_binding_supported
        self.shutdown_deadlines: list[float] = []

    async def shutdown_all(self, *, deadline: float) -> tuple[()]:
        self.shutdown_deadlines.append(deadline)
        return ()


class _DriverFailureProbe:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def shutdown_all(self, *, deadline: float) -> tuple[()]:
        del deadline
        raise self.error


class _WorkerShutdownProbe:
    state = WorkerState.READY_IDLE

    def __init__(self) -> None:
        self.shutdown_deadlines: list[float] = []

    async def shutdown(self, *, deadline: float) -> None:
        self.shutdown_deadlines.append(deadline)
        raise RuntimeError("worker cleanup failed")


class ProductionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.production_runtime_module = importlib.import_module(
            "services.wayline_forge.app.production_runtime"
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.package_root = self.root / "package"
        self.runtime_root = self.root / "runtime"
        self.package_root.mkdir(mode=0o700)
        self.runtime_root.mkdir(mode=0o700)

    def production_runtime(self):
        return self.production_runtime_module

    def write_package_file(self, relative: str, payload: bytes) -> Path:
        path = self.package_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def build_valid_package(self) -> None:
        from services.wayline_forge.app.macos_worker_runtime import (
            DescriptorBindingReleaseReceipt,
        )
        from services.wayline_forge.app.production_spawn import (
            PRODUCTION_SPAWN_ADAPTER_SHA256,
        )
        from services.wayline_forge.scripts.build_mac_sidecar import (
            write_package_manifest,
        )

        self.write_package_file("WaylineForge", b"fake-arm64-sidecar")
        server = self.write_package_file(
            "bin/llama-server",
            b"fake-arm64-llama-server",
        )
        model_name = "wayline-qwen3-4b-v7-q4_k_m.gguf"
        model = self.write_package_file(
            f"models/{model_name}",
            b"fake-receipted-q4-k-m-model",
        )
        for name in (
            "campaign_catalog_v1.json",
            "curriculum_v1.json",
            "procedure_registry_v1.json",
            "story_templates_v1.json",
        ):
            self.write_package_file(
                f"resources/{name}",
                (SERVICE_ROOT / "resources" / name).read_bytes(),
            )

        model_payload = {
            "adapterId": "j2ampn/qwen3-4b-distractor-lora-v7",
            "adapterRevision": "89abcdef0123456789abcdef0123456789abcdef",
            "baseModelId": "unsloth/Qwen3-4B-bnb-4bit",
            "baseModelRevision": "0123456789abcdef0123456789abcdef01234567",
            "contextSize": 2048,
            "ggufFileName": model_name,
            "ggufSha256": hashlib.sha256(model.read_bytes()).hexdigest(),
            "llamaCppRevision": "fedcba9876543210fedcba9876543210fedcba98",
            "platform": "macos-arm64",
            "promptSha256": PROMPT_TEMPLATE_SHA256,
            "quantization": "Q4_K_M",
            "schemaVersion": "wayline.model-manifest.v1",
            "threadCount": 8,
            "tokenizerSha256": "a" * 64,
        }
        model_manifest_path = self.write_package_file(
            "resources/model_manifest_v1.json",
            _canonical_json(model_payload).encode("utf-8"),
        )
        model_manifest = parse_model_manifest(model_manifest_path.read_bytes())
        registry = ProcedureRegistry.packaged_v1(
            resource_path=self.package_root
            / "resources/procedure_registry_v1.json"
        )
        curriculum = Curriculum.packaged_v1(
            resource_path=self.package_root / "resources/curriculum_v1.json"
        )
        compiler = QuestionCompiler(curriculum, registry)
        pinned = PinnedSlmManifest.from_model_manifest(
            model_manifest,
            registry_id=registry.registry_id,
            max_response_bytes=16_384,
            max_tokens=INFERENCE_PARAMETERS["max_tokens"],
        )
        verifier = DistractorVerifier(compiler, registry, pinned)
        request = CompileRequest(
            "valuehold",
            "place_value",
            "place_value",
            2,
            9301,
        )
        blueprint = compiler.compile(request)
        distractors = [
            {
                "answer": registry.evaluate(procedure_id, blueprint).display,
                "computation": registry.canonical_computation(
                    procedure_id,
                    blueprint,
                ),
                "misconception": registry.canonical_label(procedure_id),
            }
            for procedure_id in blueprint.allowed_procedure_ids[:3]
        ]
        slm_request = build_slm_request(blueprint)
        generation = RawSlmGeneration(
            text=_canonical_json({"distractors": distractors}),
            model_sha256=pinned.model_sha256,
            adapter_identity_receipt_sha256=(
                pinned.adapter_identity_receipt_sha256
            ),
            gguf_sha256=pinned.gguf_sha256,
            generator_identity_receipt_sha256=(
                pinned.generator_identity_receipt_sha256
            ),
            prompt_sha256=slm_request.prompt_sha256,
            prompt_template_sha256=pinned.prompt_template_sha256,
            registry_id=pinned.registry_id,
            generated_at_utc="2026-07-12T12:00:00Z",
        )
        verification = verifier.verify_generation(blueprint, generation)
        self.assertTrue(verification.accepted, verification.code)
        assert verification.value is not None
        bundle = VerifiedQuestionBundle.from_verified(
            compiler=compiler,
            request=request,
            blueprint=blueprint,
            verified=verification.value,
            generation=generation,
            manifest=pinned,
        )
        unsigned_approval = {
            "approvedCacheContentSha256": bundle.cache_content_sha256,
            "approvedSemanticContentSha256": bundle.semantic_content_sha256,
            "decision": "approved",
            "ownerAlias": "owner-01",
            "reviewedAtUtc": "2026-07-12T12:30:00Z",
            "schemaVersion": BUILD_APPROVAL_SCHEMA_VERSION,
        }
        approval = unsigned_approval | {
            "approvalRecordSha256": hashlib.sha256(
                _canonical_json(unsigned_approval).encode("utf-8")
            ).hexdigest()
        }
        build_input = self.root / "cache-build-input.json"
        build_input.write_text(
            _canonical_json(
                {
                    "items": [
                        {
                            "approval": approval,
                            "bundle": json.loads(bundle.to_private_json()),
                        }
                    ],
                    "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                }
            ),
            encoding="utf-8",
        )
        build_input.chmod(0o600)
        cache_root = (
            self.package_root / "resources/reviewed_cache_release_v1"
        )
        cache_root.mkdir(mode=0o700)
        (cache_root / "generations").mkdir(mode=0o700)
        lock = cache_root / ".publish.lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        publish_reviewed_cache(
            build_input,
            cache_root,
            compiler=compiler,
            model_manifest=pinned,
        )
        lock.unlink()

        descriptor_receipt = DescriptorBindingReleaseReceipt.attest(
            binary_sha256=hashlib.sha256(server.read_bytes()).hexdigest(),
            model_sha256=model_payload["ggufSha256"],
            llama_cpp_revision=model_payload["llamaCppRevision"],
            os_name="Darwin",
            architecture="arm64",
            readiness_protocol_revision="llama.cpp.openai.models.v1",
            spawn_adapter_sha256=PRODUCTION_SPAWN_ADAPTER_SHA256,
        )
        self.write_package_file(
            "resources/descriptor_binding_release_receipt_v1.json",
            descriptor_receipt.to_json().encode("utf-8"),
        )
        self.write_package_file("_internal/runtime.bin", b"pyinstaller-runtime")
        write_package_manifest(self.package_root)

    async def test_invalid_package_fails_before_runtime_mutation(self) -> None:
        production_runtime = self.production_runtime()
        from services.wayline_forge.app.settings import Settings

        with self.assertRaises(production_runtime.ProductionRuntimeError) as caught:
            await production_runtime.build_production_runtime(
                Settings.for_tests(self.runtime_root),
                package_root=self.package_root,
            )

        self.assertEqual(caught.exception.code, "package_invalid")
        self.assertEqual(tuple(self.runtime_root.iterdir()), ())

    async def test_valid_package_composes_without_starting_model_worker(self) -> None:
        self.build_valid_package()
        production_runtime = self.production_runtime()
        from services.wayline_forge.app.contracts import (
            ProfileCreate,
            SessionCreate,
        )
        from services.wayline_forge.app.launcher import RuntimeBundle
        from services.wayline_forge.app.settings import Settings

        bundle = await production_runtime.build_production_runtime(
            Settings.for_tests(self.runtime_root),
            package_root=self.package_root,
        )
        self.addAsyncCleanup(bundle.aclose)

        self.assertIsInstance(bundle, RuntimeBundle)
        self.assertIs(bundle.worker.state, WorkerState.STOPPED)
        self.assertFalse((self.runtime_root / "worker/llama.lock").exists())
        profile = bundle.facade.create_profile(
            ProfileCreate(
                schemaVersion="wayline.v1",
                requestId="create-profile-runtime-test",
            )
        )
        session = bundle.facade.create_session(
            SessionCreate(
                schemaVersion="wayline.v1",
                requestId="create-session-runtime-test",
                profileId=profile.profile_id,
                clientBuild="mac-test-0.1.0",
            )
        )
        self.assertEqual(
            bundle.resolve_profile_id(session.session_id),
            profile.profile_id,
        )
        self.assertTrue((self.runtime_root / "profiles/wayline_profiles_v1.sqlite").is_file())

    async def test_cleanup_shields_async_work_and_preserves_cancellation(self) -> None:
        production_runtime = self.production_runtime()
        started = asyncio.Event()
        release = asyncio.Event()
        completed: list[bool] = []

        async def cleanup() -> None:
            started.set()
            await release.wait()
            completed.append(True)

        task = asyncio.create_task(production_runtime._run_cleanups([cleanup]))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        release.set()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(completed, [True])

    async def test_cleanup_attempts_every_callback_before_control_flow(self) -> None:
        production_runtime = self.production_runtime()
        events: list[str] = []
        interruption = SystemExit(61)

        def first() -> None:
            events.append("first")

        def interrupted() -> None:
            events.append("interrupted")
            raise interruption

        async def last() -> None:
            events.append("last")

        with self.assertRaises(SystemExit) as caught:
            await production_runtime._run_cleanups([first, interrupted, last])

        self.assertIs(caught.exception, interruption)
        self.assertEqual(events, ["last", "interrupted", "first"])

    async def test_driver_backstop_gets_a_fresh_shutdown_deadline(self) -> None:
        production_runtime = self.production_runtime()
        worker = _WorkerShutdownProbe()
        driver = _DriverProbe()

        with (
            patch.object(
                production_runtime.time,
                "monotonic",
                side_effect=(10.0, 20.0),
            ),
            self.assertRaisesRegex(RuntimeError, "worker cleanup failed"),
        ):
            await production_runtime._close_worker(worker, driver)

        self.assertEqual(worker.shutdown_deadlines, [15.0])
        self.assertEqual(driver.shutdown_deadlines, [25.0])

    async def test_later_driver_control_flow_overrides_worker_failure(self) -> None:
        production_runtime = self.production_runtime()

        for driver_error in (
            asyncio.CancelledError("driver cancellation"),
            SystemExit(71),
        ):
            worker = _WorkerShutdownProbe()
            driver = _DriverFailureProbe(driver_error)

            with (
                self.subTest(error=type(driver_error).__name__),
                self.assertRaises(type(driver_error)) as caught,
            ):
                await production_runtime._close_worker(worker, driver)

            self.assertIs(caught.exception, driver_error)
            self.assertEqual(len(worker.shutdown_deadlines), 1)

    async def test_driver_is_owned_before_worker_construction(self) -> None:
        self.build_valid_package()
        production_runtime = self.production_runtime()
        from services.wayline_forge.app.settings import Settings

        driver = _DriverProbe()
        with (
            patch.object(
                production_runtime,
                "build_macos_worker_driver",
                return_value=driver,
            ),
            patch.object(
                production_runtime,
                "ManagedLlamaWorker",
                side_effect=RuntimeError("private constructor failure"),
            ),
            self.assertRaises(production_runtime.ProductionRuntimeError) as caught,
        ):
            await production_runtime.build_production_runtime(
                Settings.for_tests(self.runtime_root),
                package_root=self.package_root,
            )

        self.assertEqual(caught.exception.code, "runtime_composition_failed")
        self.assertEqual(len(driver.shutdown_deadlines), 1)

    async def test_unsupported_descriptor_driver_fails_before_worker(self) -> None:
        self.build_valid_package()
        production_runtime = self.production_runtime()
        from services.wayline_forge.app.settings import Settings

        driver = _DriverProbe(descriptor_binding_supported=False)
        with (
            patch.object(
                production_runtime,
                "build_macos_worker_driver",
                return_value=driver,
            ),
            patch.object(production_runtime, "ManagedLlamaWorker") as worker_factory,
            self.assertRaises(production_runtime.ProductionRuntimeError) as caught,
        ):
            await production_runtime.build_production_runtime(
                Settings.for_tests(self.runtime_root),
                package_root=self.package_root,
            )

        self.assertEqual(caught.exception.code, "runtime_composition_failed")
        worker_factory.assert_not_called()
        self.assertEqual(len(driver.shutdown_deadlines), 1)

    async def test_worker_environment_uses_only_private_runtime_paths(self) -> None:
        self.build_valid_package()
        production_runtime = self.production_runtime()
        from services.wayline_forge.app.settings import Settings

        real_builder = production_runtime.build_macos_worker_driver
        ambient = {
            "GGML_METAL_PATH_RESOURCES": "/tmp/unreviewed-metal",
            "HOME": "/tmp/ambient-home",
            "TMPDIR": "/tmp/ambient-tmp",
        }
        with (
            patch.dict(os.environ, ambient, clear=False),
            patch.object(
                production_runtime,
                "build_macos_worker_driver",
                wraps=real_builder,
            ) as driver_builder,
        ):
            bundle = await production_runtime.build_production_runtime(
                Settings.for_tests(self.runtime_root),
                package_root=self.package_root,
            )
        self.addAsyncCleanup(bundle.aclose)

        self.assertEqual(
            driver_builder.call_args.kwargs["environment"],
            {
                "HOME": str(self.runtime_root / "worker/home"),
                "LANG": "C",
                "LC_ALL": "C",
                "TMPDIR": str(self.runtime_root / "worker/tmp"),
            },
        )

    async def test_post_audit_receipt_swap_fails_before_state_mutation(self) -> None:
        self.build_valid_package()
        production_runtime = self.production_runtime()
        from services.wayline_forge.app.macos_worker_runtime import (
            DescriptorBindingReleaseReceipt,
            parse_descriptor_binding_release_receipt,
        )
        from services.wayline_forge.app.settings import Settings

        receipt_path = (
            self.package_root
            / "resources/descriptor_binding_release_receipt_v1.json"
        )
        receipt = parse_descriptor_binding_release_receipt(receipt_path.read_bytes())
        replacement = DescriptorBindingReleaseReceipt.attest(
            binary_sha256="f" * 64,
            model_sha256=receipt.model_sha256,
            llama_cpp_revision=receipt.llama_cpp_revision,
            os_name=receipt.os_name,
            architecture=receipt.architecture,
            readiness_protocol_revision=receipt.readiness_protocol_revision,
            spawn_adapter_sha256=receipt.spawn_adapter_sha256,
        )
        real_validate = production_runtime.validate_packaged_layout

        def validate_then_swap(path: Path):
            manifest = real_validate(path)
            receipt_path.chmod(0o600)
            receipt_path.write_bytes(replacement.to_json().encode("utf-8"))
            return manifest

        with (
            patch.object(
                production_runtime,
                "validate_packaged_layout",
                side_effect=validate_then_swap,
            ),
            self.assertRaises(production_runtime.ProductionRuntimeError) as caught,
        ):
            await production_runtime.build_production_runtime(
                Settings.for_tests(self.runtime_root),
                package_root=self.package_root,
            )

        self.assertEqual(caught.exception.code, "package_invalid")
        self.assertEqual(tuple(self.runtime_root.iterdir()), ())

    def test_authority_validation_preserves_control_flow(self) -> None:
        production_runtime = self.production_runtime()
        exceptional = (
            KeyboardInterrupt("owner interrupt"),
            SystemExit(67),
            asyncio.CancelledError(),
        )

        for error in exceptional:
            with (
                self.subTest(error=type(error).__name__),
                patch.object(
                    production_runtime,
                    "validate_packaged_layout",
                    side_effect=error,
                ),
                self.assertRaises(type(error)) as caught,
            ):
                production_runtime._immutable_authorities(self.package_root)
            self.assertIs(caught.exception, error)

    async def test_packaged_launcher_injects_only_concrete_factory(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec(
                "services.wayline_forge.app.packaged_launcher"
            ),
            "packaged launcher module is required",
        )
        packaged = importlib.import_module(
            "services.wayline_forge.app.packaged_launcher"
        )

        with patch.object(packaged, "launch_main", return_value=17) as launch:
            result = packaged.main(("--runtime-root", "/private/runtime"))

        self.assertEqual(result, 17)
        launch.assert_called_once_with(
            ("--runtime-root", "/private/runtime"),
            runtime_factory=packaged.packaged_runtime_factory,
        )

    def test_pyinstaller_uses_packaged_launcher_entrypoint(self) -> None:
        spec = (SERVICE_ROOT / "WaylineForge.spec").read_text(encoding="utf-8")

        self.assertIn("app' / 'packaged_launcher.py", spec)
        self.assertNotIn("app' / 'launcher.py", spec)
        self.assertIn("datas=runtime_resources", spec)
        for resource in (
            "campaign_catalog_v1.json",
            "curriculum_v1.json",
            "procedure_registry_v1.json",
            "story_templates_v1.json",
        ):
            self.assertIn(repr(resource), spec)
        self.assertIn("'services/wayline_forge/resources'", spec)


if __name__ == "__main__":
    unittest.main()
