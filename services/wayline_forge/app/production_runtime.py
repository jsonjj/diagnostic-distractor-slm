"""Validated composition root for the packaged local Wayline runtime."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
from pathlib import Path
import secrets
import socket
import stat
import time
from typing import Awaitable, Callable

from services.wayline_forge.app.application import WaylineApplication
from services.wayline_forge.app.assisted_route_store import AssistedRouteStore
from services.wayline_forge.app.campaign_catalog import CampaignCatalog
from services.wayline_forge.app.current_session import CurrentSessionResolver
from services.wayline_forge.app.curriculum import Curriculum
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.launcher import RuntimeBundle
from services.wayline_forge.app.llama_worker import (
    ManagedLlamaWorker,
    StdlibArtifactVerifier,
    WorkerLaunchSpec,
    WorkerState,
)
from services.wayline_forge.app.macos_worker_runtime import (
    DescriptorBindingReleaseReceipt,
    build_macos_worker_driver,
    parse_descriptor_binding_release_receipt,
)
from services.wayline_forge.app.model_manifest import (
    ModelManifest,
    parse_model_manifest,
)
from services.wayline_forge.app.orchestrator import (
    BatchPreparationOrchestrator,
)
from services.wayline_forge.app.procedure_registry import ProcedureRegistry
from services.wayline_forge.app.production_spawn import ProductionPopenFactory
from services.wayline_forge.app.profile_store import ProfileStore
from services.wayline_forge.app.providers.distractor import PinnedSlmManifest
from services.wayline_forge.app.providers.llama_cpp import (
    LlamaCppProvider,
    StdlibAsyncJsonTransport,
)
from services.wayline_forge.app.providers.template_narrative import (
    StoryTemplateCatalog,
)
from services.wayline_forge.app.question_kernel import QuestionCompiler
from services.wayline_forge.app.quiz_store import QuizStore
from services.wayline_forge.app.reviewed_cache_release import (
    ReviewedCacheRelease,
)
from services.wayline_forge.app.settings import Settings
from services.wayline_forge.app.slm_prompt import INFERENCE_PARAMETERS
from services.wayline_forge.scripts.build_mac_sidecar import (
    DESCRIPTOR_BINDING_RECEIPT_PATH,
    LLAMA_SERVER_PATH,
    MODEL_MANIFEST_PATH,
    PackageLayoutError,
    PackageManifest,
    REVIEWED_CACHE_ROOT,
    validate_packaged_layout,
)


_MAX_RESPONSE_BYTES = 16_384
_WORKER_SHUTDOWN_SECONDS = 5.0
_CONTROL_FLOW_EXCEPTIONS = (
    KeyboardInterrupt,
    SystemExit,
    GeneratorExit,
    asyncio.CancelledError,
)


class ProductionRuntimeError(RuntimeError):
    """Stable, non-sensitive production composition failure."""

    _CODES = frozenset(
        {
            "package_invalid",
            "runtime_composition_failed",
            "runtime_root_invalid",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown production runtime error code")
        self.code = code
        super().__init__(code)


class ProductionRuntimeBundle(RuntimeBundle):
    """Runtime bundle retaining only inert worker state for lifecycle audits."""

    __slots__ = ("_worker",)

    def __init__(self, *, worker: ManagedLlamaWorker, **kwargs: object) -> None:
        if type(worker) is not ManagedLlamaWorker:
            raise TypeError("worker must be a ManagedLlamaWorker")
        super().__init__(**kwargs)
        self._worker = worker

    @property
    def worker(self) -> ManagedLlamaWorker:
        return self._worker


def _runtime_root(path: Path, package_root: Path) -> Path:
    try:
        raw = os.fspath(path)
        if (
            not isinstance(raw, str)
            or not raw
            or not os.path.isabs(raw)
            or os.path.normpath(raw) != raw
            or "\x00" in raw
        ):
            raise ValueError
        root = Path(raw)
        details = root.lstat()
        common = Path(os.path.commonpath((root, package_root)))
    except (OSError, TypeError, ValueError):
        raise ProductionRuntimeError("runtime_root_invalid") from None
    if (
        root.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
        or common in {root, package_root}
    ):
        raise ProductionRuntimeError("runtime_root_invalid")
    return root


def _private_directory(parent: Path, name: str) -> Path:
    path = parent / name
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        details = path.lstat()
    except OSError:
        raise ProductionRuntimeError("runtime_root_invalid") from None
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise ProductionRuntimeError("runtime_root_invalid")
    return path


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _run_cleanups(
    cleanups: list[Callable[[], object | Awaitable[object]]],
) -> bool:
    failed = False
    control_flow: BaseException | None = None
    for cleanup in reversed(cleanups):
        try:
            result = cleanup()
            if inspect.isawaitable(result):
                cleanup_task = asyncio.ensure_future(result)
                cancellation: asyncio.CancelledError | None = None
                while not cleanup_task.done():
                    try:
                        await asyncio.shield(cleanup_task)
                    except asyncio.CancelledError as error:
                        if cleanup_task.done():
                            if (
                                not cleanup_task.cancelled()
                                and cancellation is None
                            ):
                                cancellation = error
                            break
                        if cancellation is None:
                            cancellation = error
                    except BaseException:
                        break
                task_error: BaseException | None = None
                try:
                    cleanup_task.result()
                except BaseException as error:
                    task_error = error
                if cancellation is not None:
                    if task_error is not None and not isinstance(
                        task_error,
                        asyncio.CancelledError,
                    ):
                        raise cancellation from task_error
                    raise cancellation
                if task_error is not None:
                    raise task_error
        except _CONTROL_FLOW_EXCEPTIONS as error:
            if control_flow is None:
                control_flow = error
        except BaseException:
            failed = True
    if control_flow is not None:
        raise control_flow
    return failed


async def _close_worker(
    worker: ManagedLlamaWorker,
    driver: object,
) -> None:
    deadline = time.monotonic() + _WORKER_SHUTDOWN_SECONDS
    primary: BaseException | None = None
    if worker.state is not WorkerState.STOPPED:
        try:
            await worker.shutdown(deadline=deadline)
        except BaseException as error:
            primary = error
    try:
        await driver.shutdown_all(
            deadline=time.monotonic() + _WORKER_SHUTDOWN_SECONDS
        )
    except BaseException as error:
        if primary is None or isinstance(error, _CONTROL_FLOW_EXCEPTIONS):
            primary = error
    if primary is not None:
        raise primary


def _manifest_bound_bytes(
    package_root: Path,
    manifest: PackageManifest,
    relative_path: str,
) -> bytes:
    entries = {
        entry.relative_path: entry
        for entry in manifest.entries
    }
    entry = entries.get(relative_path)
    if entry is None:
        raise PackageLayoutError("package_file_missing")
    raw = (package_root / relative_path).read_bytes()
    if (
        len(raw) != entry.size_bytes
        or hashlib.sha256(raw).hexdigest() != entry.sha256
    ):
        raise PackageLayoutError("package_digest_mismatch")
    return raw


def _immutable_authorities(
    package_root: Path,
) -> tuple[
    ModelManifest,
    DescriptorBindingReleaseReceipt,
    QuestionCompiler,
    PinnedSlmManifest,
    DistractorVerifier,
    ReviewedCacheRelease,
]:
    try:
        package_manifest = validate_packaged_layout(package_root)
        resources = package_root / "resources"
        model_manifest = parse_model_manifest(
            _manifest_bound_bytes(
                package_root,
                package_manifest,
                MODEL_MANIFEST_PATH,
            )
        )
        receipt = parse_descriptor_binding_release_receipt(
            _manifest_bound_bytes(
                package_root,
                package_manifest,
                DESCRIPTOR_BINDING_RECEIPT_PATH,
            )
        )
        CampaignCatalog.packaged_v1(
            resource_path=resources / "campaign_catalog_v1.json"
        )
        StoryTemplateCatalog.packaged_v1(
            resource_path=resources / "story_templates_v1.json"
        )
        curriculum = Curriculum.packaged_v1(
            resource_path=resources / "curriculum_v1.json"
        )
        registry = ProcedureRegistry.packaged_v1(
            resource_path=resources / "procedure_registry_v1.json"
        )
        compiler = QuestionCompiler(curriculum, registry)
        pinned = PinnedSlmManifest.from_model_manifest(
            model_manifest,
            registry_id=registry.registry_id,
            max_response_bytes=_MAX_RESPONSE_BYTES,
            max_tokens=INFERENCE_PARAMETERS["max_tokens"],
        )
        verifier = DistractorVerifier(compiler, registry, pinned)
        reviewed_release = ReviewedCacheRelease.open_current(
            package_root / REVIEWED_CACHE_ROOT,
            compiler=compiler,
            model_manifest=pinned,
        )
    except _CONTROL_FLOW_EXCEPTIONS:
        raise
    except BaseException:
        raise ProductionRuntimeError("package_invalid") from None
    return (
        model_manifest,
        receipt,
        compiler,
        pinned,
        verifier,
        reviewed_release,
    )


async def build_production_runtime(
    settings: Settings,
    *,
    package_root: str | Path,
) -> ProductionRuntimeBundle:
    """Validate every immutable authority before opening writable learner state."""

    if not isinstance(settings, Settings):
        raise ProductionRuntimeError("runtime_composition_failed")
    try:
        package = Path(package_root)
        if not package.is_absolute():
            raise ValueError
    except (TypeError, ValueError):
        raise ProductionRuntimeError("package_invalid") from None

    (
        model_manifest,
        descriptor_receipt,
        compiler,
        pinned,
        verifier,
        reviewed_release,
    ) = _immutable_authorities(package)
    cleanups: list[Callable[[], object | Awaitable[object]]] = [
        reviewed_release.close
    ]
    try:
        state_root = _runtime_root(settings.runtime_root, package)
        _private_directory(state_root, "profiles")
        worker_root = _private_directory(state_root, "worker")
        worker_home = _private_directory(worker_root, "home")
        worker_tmp = _private_directory(worker_root, "tmp")

        profile_store = ProfileStore(settings.profile_db)
        cleanups.append(profile_store.close)
        quiz_store = QuizStore(
            settings.profile_db,
            compiler=compiler,
            manifest=pinned,
        )
        cleanups.append(quiz_store.close)
        assisted_store = AssistedRouteStore(
            settings.profile_db,
            compiler=compiler,
            manifest=pinned,
        )
        cleanups.append(assisted_store.close)

        binary_path = package / LLAMA_SERVER_PATH
        model_path = package / "models" / model_manifest.gguf_file_name
        environment = {
            "HOME": str(worker_home),
            "LANG": "C",
            "LC_ALL": "C",
            "TMPDIR": str(worker_tmp),
        }
        driver = build_macos_worker_driver(
            binary_root=str(binary_path.parent),
            model_root=str(model_path.parent),
            lock_path=str(worker_root / "llama.lock"),
            cwd=str(package),
            environment=environment,
            release_receipt=descriptor_receipt,
            popen_factory=ProductionPopenFactory(),
        )
        cleanups.append(
            lambda driver=driver: driver.shutdown_all(
                deadline=time.monotonic() + _WORKER_SHUTDOWN_SECONDS
            )
        )
        if driver.descriptor_binding_supported is not True:
            raise ProductionRuntimeError("runtime_composition_failed")
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=StdlibArtifactVerifier(),
            launch_spec=WorkerLaunchSpec(
                binary_path=str(binary_path),
                model_path=str(model_path),
                binary_sha256=descriptor_receipt.binary_sha256,
                model_sha256=descriptor_receipt.model_sha256,
                extra_args=(
                    "--ctx-size",
                    str(model_manifest.context_size),
                    "--threads",
                    str(model_manifest.thread_count),
                ),
            ),
            clock=time.monotonic,
            epoch_id_factory=lambda: secrets.token_hex(16),
            generation_id_factory=lambda: secrets.token_hex(16),
            port_factory=_unused_loopback_port,
        )
        cleanups.append(
            lambda worker=worker, driver=driver: _close_worker(worker, driver)
        )
        transport = StdlibAsyncJsonTransport(
            credential_resolver=driver.resolve_transport_credentials
        )
        provider = LlamaCppProvider(
            pinned,
            transport,
            worker=worker,
        )
        orchestrator = BatchPreparationOrchestrator(
            store=quiz_store,
            compiler=compiler,
            verifier=verifier,
            manifest=pinned,
            provider=provider,
            reviewed_cache=reviewed_release.cache,
        )
        application = WaylineApplication(
            profile_store=profile_store,
            quiz_store=quiz_store,
            orchestrator=orchestrator,
            assisted_route_store=assisted_store,
        )
        resolver = CurrentSessionResolver(profile_store)
        return ProductionRuntimeBundle(
            facade=application,
            resolve_profile_id=lambda session_id: resolver.resolve(
                session_id
            ).profile_id,
            cleanup=tuple(cleanups),
            worker=worker,
        )
    except _CONTROL_FLOW_EXCEPTIONS:
        await _run_cleanups(cleanups)
        raise
    except ProductionRuntimeError:
        await _run_cleanups(cleanups)
        raise
    except BaseException:
        await _run_cleanups(cleanups)
        raise ProductionRuntimeError("runtime_composition_failed") from None


__all__ = [
    "ProductionRuntimeBundle",
    "ProductionRuntimeError",
    "build_production_runtime",
]
