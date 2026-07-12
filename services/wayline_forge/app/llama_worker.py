"""Launcher-owned lifecycle authority for the local ``llama-server`` child.

Closing an HTTP connection does not prove that llama.cpp stopped evaluating a
request.  This module therefore binds each generation to one launched process
epoch and releases that lease only after a complete response or an exact child
reap receipt.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import inspect
import json
import math
import os
import re
import stat
import threading
from typing import Any, Awaitable, Callable, Protocol, Sequence


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_AUTHORITY_FLAGS = (
    "--model",
    "-m",
    "--host",
    "--port",
    "--parallel",
    "--api-key",
    "--api-key-file",
    "--alias",
    "-a",
)
_DEADLINE_EXPIRED = object()


class WorkerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY_IDLE = "ready_idle"
    BUSY = "busy"
    STOPPING = "stopping"
    QUARANTINED = "quarantined"


class WorkerError(RuntimeError):
    """A fail-closed worker lifecycle error with a stable public code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class WorkerLaunchAuthority:
    """Process-wide single-child and lifecycle-identifier authority.

    Production controllers share the module singleton. Tests may inject a
    fresh instance so a deliberately quarantined scenario cannot contaminate
    an unrelated test process.
    """

    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._owner: object | None = None
        self._spawn_boundary_crossed = False
        self._quarantined = False
        self._seen_epoch_ids: set[str] = set()
        self._seen_generation_ids: set[str] = set()

    def reserve_epoch(self, owner: object, epoch_id: str) -> None:
        with self._guard:
            if self._quarantined:
                raise WorkerError("worker_quarantined")
            if self._owner is not None:
                raise WorkerError("worker_unsafe_state")
            if epoch_id in self._seen_epoch_ids:
                raise WorkerError("reused_worker_epoch")
            self._seen_epoch_ids.add(epoch_id)
            self._owner = owner
            self._spawn_boundary_crossed = False

    def mark_spawned(self, owner: object) -> None:
        with self._guard:
            self._require_owner(owner)
            self._spawn_boundary_crossed = True

    def reserve_generation(self, owner: object, generation_id: str) -> None:
        with self._guard:
            self._require_healthy_owner(owner)
            if generation_id in self._seen_generation_ids:
                raise WorkerError("reused_generation_id")
            self._seen_generation_ids.add(generation_id)

    def release_unspawned(self, owner: object) -> None:
        with self._guard:
            self._require_healthy_owner(owner)
            if self._spawn_boundary_crossed:
                self._quarantined = True
                raise WorkerError("worker_quarantined")
            self._owner = None

    def release_reaped(self, owner: object) -> None:
        with self._guard:
            self._require_healthy_owner(owner)
            if not self._spawn_boundary_crossed:
                self._quarantined = True
                raise WorkerError("worker_quarantined")
            self._owner = None
            self._spawn_boundary_crossed = False

    def quarantine(self, owner: object) -> None:
        with self._guard:
            if self._owner is None:
                self._owner = owner
            if self._owner is not owner:
                # Conflicting uncertain ownership is itself process-wide
                # quarantine; never transfer or release it.
                self._quarantined = True
                return
            self._quarantined = True

    def _require_owner(self, owner: object) -> None:
        if self._owner is not owner:
            if self._quarantined:
                raise WorkerError("worker_quarantined")
            raise WorkerError("worker_unsafe_state")

    def _require_healthy_owner(self, owner: object) -> None:
        self._require_owner(owner)
        if self._quarantined:
            raise WorkerError("worker_quarantined")


_PROCESS_LAUNCH_AUTHORITY = WorkerLaunchAuthority()


@dataclass(frozen=True, slots=True)
class WorkerLaunchSpec:
    binary_path: str
    model_path: str
    binary_sha256: str
    model_sha256: str
    extra_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.binary_path, str)
            or not self.binary_path
            or "\x00" in self.binary_path
            or not isinstance(self.model_path, str)
            or not self.model_path
            or "\x00" in self.model_path
        ):
            raise ValueError("worker paths must be non-empty")
        if not isinstance(self.binary_sha256, str) or not _SHA256.fullmatch(
            self.binary_sha256
        ):
            raise ValueError("binary_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.model_sha256, str) or not _SHA256.fullmatch(
            self.model_sha256
        ):
            raise ValueError("model_sha256 must be a lowercase SHA-256 digest")
        object.__setattr__(
            self,
            "binary_path",
            os.path.realpath(os.path.abspath(self.binary_path)),
        )
        object.__setattr__(
            self,
            "model_path",
            os.path.realpath(os.path.abspath(self.model_path)),
        )
        extra_args = tuple(self.extra_args)
        for argument in extra_args:
            if (
                not isinstance(argument, str)
                or not argument
                or "\x00" in argument
            ):
                raise ValueError("worker arguments must be non-empty strings")
            if any(
                argument == flag or argument.startswith(flag + "=")
                for flag in _AUTHORITY_FLAGS
            ) or argument == "--" or argument.startswith(("-m", "-a")):
                raise ValueError("worker authority flags cannot be overridden")
        object.__setattr__(self, "extra_args", extra_args)


@dataclass(frozen=True, slots=True)
class ArtifactVerificationReceipt:
    binary_path: str
    model_path: str
    binary_sha256: str
    model_sha256: str
    binary_size: int
    model_size: int
    binary_device: int
    binary_inode: int
    model_device: int
    model_inode: int


class ArtifactVerifier(Protocol):
    async def verify(
        self,
        launch_spec: WorkerLaunchSpec,
        *,
        deadline: float,
    ) -> ArtifactVerificationReceipt: ...


class StdlibArtifactVerifier:
    """Hash regular files and capture stable descriptor facts.

    The descriptors close after verification. The receipt detects corruption
    and binds the launch protocol, but it does not by itself close the path
    replacement window between verification and exec.
    """

    async def verify(
        self,
        launch_spec: WorkerLaunchSpec,
        *,
        deadline: float,
    ) -> ArtifactVerificationReceipt:
        del deadline
        try:
            binary = await asyncio.to_thread(
                self._hash_regular_file,
                launch_spec.binary_path,
            )
            model = await asyncio.to_thread(
                self._hash_regular_file,
                launch_spec.model_path,
            )
        except WorkerError:
            raise
        except BaseException:
            raise WorkerError("artifact_verification_failed") from None
        return ArtifactVerificationReceipt(
            binary_path=launch_spec.binary_path,
            model_path=launch_spec.model_path,
            binary_sha256=binary[0],
            model_sha256=model[0],
            binary_size=binary[1],
            model_size=model[1],
            binary_device=binary[2],
            binary_inode=binary[3],
            model_device=model[2],
            model_inode=model[3],
        )

    @staticmethod
    def _hash_regular_file(path: str) -> tuple[str, int, int, int]:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise WorkerError("artifact_verification_failed")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise WorkerError("artifact_verification_failed")
            return digest.hexdigest(), after.st_size, after.st_dev, after.st_ino
        finally:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class WorkerEpochReceipt:
    epoch_id: str
    pid: int
    port: int
    endpoint: str
    binary_sha256: str
    model_sha256: str
    argv_sha256: str


@dataclass(frozen=True, slots=True, eq=False)
class GenerationLease:
    generation_id: str
    epoch_id: str
    prompt_sha256: str
    endpoint: str
    transport_authority: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class WorkerStopReceipt:
    epoch_id: str
    generation_id: str | None
    pid: int
    reason: str
    signals: tuple[str, ...]
    returncode: int


@dataclass(frozen=True, slots=True)
class ProcessExit:
    pid: int
    returncode: int
    process_identity: object

    @property
    def handle_token(self) -> object:
        """Deprecated name retained for receipt readers."""

        return self.process_identity


class WorkerProcess(Protocol):
    pid: int
    process_identity: object
    transport_authority: object
    launch_artifacts: ArtifactVerificationReceipt
    launch_argv_sha256: str


class WorkerProcessDriver(Protocol):
    """Trusted spawn boundary for an exact child process.

    ``start`` must consume the exact receipt object and argv it receives,
    revalidate or use descriptor-bound artifacts immediately before exec, and
    return driver-issued opaque process and transport authorities plus the same
    receipt and *logical* controller argv hash on its handle. The driver may
    inject bearer/alias arguments into a private effective argv; those secrets
    are intentionally excluded from this public logical hash and every public
    receipt. Signal and reap methods must validate process identity against the
    driver's exact-child registry before acting; PID alone is never authority.
    A driver that can spawn before raising must retain and reap that child
    itself because no handle crossed back to this controller.
    """

    async def start(
        self,
        argv: Sequence[str],
        *,
        start_new_session: bool,
        deadline: float,
        artifacts: ArtifactVerificationReceipt,
    ) -> WorkerProcess: ...

    async def await_ready(
        self,
        process: WorkerProcess,
        *,
        port: int,
        deadline: float,
    ) -> bool: ...

    def resolve_transport_credentials(
        self,
        transport_authority: object,
    ) -> object: ...

    def terminate_group(
        self,
        process: WorkerProcess,
        *,
        process_identity: object,
    ) -> None: ...

    def kill_group(
        self,
        process: WorkerProcess,
        *,
        process_identity: object,
    ) -> None: ...

    async def wait_reaped(
        self,
        process: WorkerProcess,
        *,
        process_identity: object,
        deadline: float,
    ) -> ProcessExit | None: ...


def canonical_argv_sha256(argv: Sequence[str]) -> str:
    """Hash the public logical argv without shell-string ambiguity.

    Driver-private authentication flags belong only to the effective spawn
    argv. They are neither inputs to this digest nor fields in public receipts.
    """

    encoded = json.dumps(
        list(argv),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ManagedLlamaWorker:
    """Own one explicit llama.cpp process epoch and at most one generation."""

    def __init__(
        self,
        *,
        driver: WorkerProcessDriver,
        artifact_verifier: ArtifactVerifier,
        launch_spec: WorkerLaunchSpec,
        clock: Callable[[], float],
        epoch_id_factory: Callable[[], str],
        generation_id_factory: Callable[[], str],
        port_factory: Callable[[], int],
        term_grace_seconds: float = 0.25,
        launch_authority: WorkerLaunchAuthority | None = None,
    ) -> None:
        if not math.isfinite(term_grace_seconds) or term_grace_seconds < 0:
            raise ValueError("term_grace_seconds must be finite and non-negative")
        if artifact_verifier is None or not callable(
            getattr(artifact_verifier, "verify", None)
        ):
            raise ValueError("artifact_verifier is required")
        if not inspect.iscoroutinefunction(artifact_verifier.verify):
            raise ValueError("artifact verifier must be asynchronous")
        for method_name in ("start", "await_ready", "wait_reaped"):
            method = getattr(driver, method_name, None)
            if not inspect.iscoroutinefunction(method):
                raise ValueError(f"driver {method_name} must be asynchronous")
        for method_name in ("terminate_group", "kill_group"):
            if not callable(getattr(driver, method_name, None)):
                raise ValueError(f"driver {method_name} is required")
        credential_resolver = getattr(
            driver,
            "resolve_transport_credentials",
            None,
        )
        if not callable(credential_resolver) or inspect.iscoroutinefunction(
            credential_resolver
        ):
            raise ValueError(
                "driver resolve_transport_credentials must be synchronous"
            )
        for method_name, keyword in (
            ("start", "artifacts"),
            ("resolve_transport_credentials", "transport_authority"),
            ("terminate_group", "process_identity"),
            ("kill_group", "process_identity"),
            ("wait_reaped", "process_identity"),
        ):
            method = getattr(driver, method_name)
            try:
                parameters = inspect.signature(method).parameters.values()
            except (TypeError, ValueError):
                raise ValueError(
                    f"driver {method_name} must expose {keyword} authority"
                ) from None
            if not any(
                parameter.name == keyword
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            ):
                raise ValueError(
                    f"driver {method_name} must expose {keyword} authority"
                )
        self._driver = driver
        self._transport_credential_resolver = credential_resolver
        self._artifact_verifier = artifact_verifier
        self._launch_spec = launch_spec
        self._clock = clock
        self._epoch_id_factory = epoch_id_factory
        self._generation_id_factory = generation_id_factory
        self._port_factory = port_factory
        self._term_grace_seconds = float(term_grace_seconds)
        self._launch_authority = (
            _PROCESS_LAUNCH_AUTHORITY
            if launch_authority is None
            else launch_authority
        )
        if not isinstance(self._launch_authority, WorkerLaunchAuthority):
            raise ValueError("launch_authority must be a WorkerLaunchAuthority")
        self._authority_owner = object()
        self._guard = threading.RLock()
        self._state = WorkerState.STOPPED
        self._process: WorkerProcess | None = None
        self._process_identity: object | None = None
        self._transport_authority: tuple[object, ...] = ()
        self._epoch: WorkerEpochReceipt | None = None
        self._active_lease: GenerationLease | None = None
        self._last_stop_receipt: WorkerStopReceipt | None = None
        self._retained_driver_tasks: set[asyncio.Task[object]] = set()
        self._retained_cleanup_handles: dict[int, object] = {}

    @property
    def state(self) -> WorkerState:
        with self._guard:
            return self._state

    @property
    def active_lease(self) -> GenerationLease | None:
        with self._guard:
            return self._active_lease

    @property
    def epoch(self) -> WorkerEpochReceipt | None:
        with self._guard:
            return self._epoch

    @property
    def cleanup_authority_retained(self) -> bool:
        """Whether an exact returned handle remains under fail-closed authority."""

        with self._guard:
            return bool(self._retained_cleanup_handles)

    async def begin_preparation(self, *, deadline: float) -> WorkerEpochReceipt:
        self._require_future_deadline(deadline)
        with self._guard:
            if self._state is WorkerState.READY_IDLE and self._epoch is not None:
                return self._epoch
            if self._state is WorkerState.QUARANTINED:
                raise WorkerError("worker_quarantined")
            if self._state is not WorkerState.STOPPED:
                raise WorkerError("worker_unsafe_state")
            self._state = WorkerState.STARTING

        try:
            port = self._port_factory()
        except BaseException:
            with self._guard:
                self._state = WorkerState.STOPPED
            raise WorkerError("invalid_worker_port") from None
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            with self._guard:
                self._state = WorkerState.STOPPED
            raise WorkerError("invalid_worker_port")
        try:
            epoch_id = self._epoch_id_factory()
        except BaseException:
            with self._guard:
                self._state = WorkerState.STOPPED
            raise WorkerError("invalid_worker_epoch") from None
        if (
            not isinstance(epoch_id, str)
            or not epoch_id
            or "\x00" in epoch_id
        ):
            with self._guard:
                self._state = WorkerState.STOPPED
            raise WorkerError("invalid_worker_epoch")
        try:
            self._launch_authority.reserve_epoch(
                self._authority_owner,
                epoch_id,
            )
        except WorkerError as error:
            with self._guard:
                self._state = (
                    WorkerState.QUARANTINED
                    if error.code == "worker_quarantined"
                    else WorkerState.STOPPED
                )
            raise
        endpoint = f"http://127.0.0.1:{port}/v1/chat/completions"
        argv = self._launch_argv(port)

        try:
            artifacts = await self._await_until(
                self._artifact_verifier.verify(
                    self._launch_spec,
                    deadline=deadline,
                ),
                deadline=deadline,
            )
        except WorkerError:
            self._release_unspawned_authority()
            with self._guard:
                self._state = WorkerState.STOPPED
            raise
        except BaseException:
            self._release_unspawned_authority()
            with self._guard:
                self._state = WorkerState.STOPPED
            raise WorkerError("artifact_verification_failed") from None
        if artifacts is _DEADLINE_EXPIRED:
            self._release_unspawned_authority()
            with self._guard:
                self._state = WorkerState.STOPPED
            raise WorkerError("artifact_verification_failed")
        if not self._artifacts_match_spec(artifacts):
            self._release_unspawned_authority()
            with self._guard:
                self._state = WorkerState.STOPPED
            raise WorkerError("artifact_digest_mismatch")
        try:
            process = await self._await_until(
                self._driver.start(
                    argv,
                    start_new_session=True,
                    deadline=deadline,
                    artifacts=artifacts,
                ),
                deadline=deadline,
                on_late_result=self._cleanup_late_started_process,
            )
        except BaseException:
            # A launcher exception does not prove that no child crossed the
            # spawn boundary, and there is no trustworthy PID to reap.
            with self._guard:
                self._state = WorkerState.QUARANTINED
            self._launch_authority.quarantine(self._authority_owner)
            raise WorkerError("worker_quarantined") from None
        if process is _DEADLINE_EXPIRED:
            with self._guard:
                self._state = WorkerState.QUARANTINED
            self._launch_authority.quarantine(self._authority_owner)
            raise WorkerError("worker_quarantined")
        with self._guard:
            # Retain the exact spawn-boundary object before touching any
            # hostile or malformed metadata exposed by it.
            self._retained_cleanup_handles[id(process)] = process
        try:
            self._launch_authority.mark_spawned(self._authority_owner)
        except BaseException:
            self._enter_quarantine()
            raise WorkerError("worker_quarantined") from None
        try:
            process_identity = process.process_identity
            transport_authority = process.transport_authority
            launch_artifacts = process.launch_artifacts
            launch_argv_sha256 = process.launch_argv_sha256
        except BaseException:
            with self._guard:
                self._state = WorkerState.QUARANTINED
            self._launch_authority.quarantine(self._authority_owner)
            raise WorkerError("worker_quarantined") from None
        if (
            process_identity is None
            or isinstance(
                process_identity,
                (bool, int, float, str, bytes, tuple, frozenset),
            )
            or not self._is_opaque_authority(transport_authority)
            or launch_artifacts is not artifacts
            or launch_argv_sha256 != canonical_argv_sha256(argv)
        ):
            with self._guard:
                self._state = WorkerState.QUARANTINED
            self._launch_authority.quarantine(self._authority_owner)
            raise WorkerError("worker_quarantined")
        try:
            pid = process.pid
        except BaseException:
            with self._guard:
                self._state = WorkerState.QUARANTINED
            self._launch_authority.quarantine(self._authority_owner)
            raise WorkerError("worker_quarantined") from None
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            with self._guard:
                self._state = WorkerState.QUARANTINED
            self._launch_authority.quarantine(self._authority_owner)
            raise WorkerError("invalid_worker_pid")

        epoch = WorkerEpochReceipt(
            epoch_id=epoch_id,
            pid=pid,
            port=port,
            endpoint=endpoint,
            binary_sha256=self._launch_spec.binary_sha256,
            model_sha256=self._launch_spec.model_sha256,
            argv_sha256=canonical_argv_sha256(argv),
        )
        with self._guard:
            self._process = process
            self._process_identity = process_identity
            self._transport_authority = (transport_authority,)
            self._epoch = epoch
        readiness_error: BaseException | None = None
        try:
            ready = await self._await_until(
                self._driver.await_ready(
                    process,
                    port=port,
                    deadline=deadline,
                ),
                deadline=deadline,
            )
        except BaseException as error:
            readiness_error = error
            ready = False
        if ready is _DEADLINE_EXPIRED:
            ready = False
        if not ready:
            with self._guard:
                self._state = WorkerState.STOPPING
            try:
                await self._await_stop(
                    reason="readiness_failed",
                    deadline=deadline,
                )
            except WorkerError:
                raise
            if isinstance(readiness_error, asyncio.CancelledError):
                raise asyncio.CancelledError
            raise WorkerError("worker_not_ready") from None
        try:
            self._resolve_transport_authority(transport_authority)
        except WorkerError as authority_error:
            with self._guard:
                self._state = WorkerState.STOPPING
            await self._await_stop(
                reason="transport_authority_failed",
                deadline=deadline,
            )
            raise WorkerError(authority_error.code) from None
        with self._guard:
            if self._state is not WorkerState.STARTING:
                self._enter_quarantine()
                raise WorkerError("worker_quarantined")
            self._state = WorkerState.READY_IDLE
        return epoch

    async def acquire(
        self,
        prompt_sha256: str,
        *,
        ready_deadline: float,
    ) -> GenerationLease:
        self._require_future_deadline(ready_deadline)
        with self._guard:
            if self._state is WorkerState.QUARANTINED:
                raise WorkerError("worker_quarantined")
            if self._state is WorkerState.STOPPED:
                raise WorkerError("worker_not_ready")
            if self._state is not WorkerState.READY_IDLE or self._epoch is None:
                raise WorkerError("worker_unsafe_state")
            transport_authority = self._current_transport_authority()
            try:
                self._resolve_transport_authority(transport_authority)
            except WorkerError:
                self._enter_quarantine()
                raise
            if not isinstance(prompt_sha256, str) or not _SHA256.fullmatch(
                prompt_sha256
            ):
                raise WorkerError("invalid_prompt_receipt")
            try:
                generation_id = self._generation_id_factory()
            except BaseException:
                raise WorkerError("invalid_generation_id") from None
            if (
                not isinstance(generation_id, str)
                or not generation_id
                or "\x00" in generation_id
            ):
                raise WorkerError("invalid_generation_id")
            self._launch_authority.reserve_generation(
                self._authority_owner,
                generation_id,
            )
            lease = GenerationLease(
                generation_id=generation_id,
                epoch_id=self._epoch.epoch_id,
                prompt_sha256=prompt_sha256,
                endpoint=self._epoch.endpoint,
                transport_authority=transport_authority,
            )
            self._active_lease = lease
            self._state = WorkerState.BUSY
            return lease

    async def confirm_complete(self, lease: GenerationLease) -> None:
        with self._guard:
            self._require_active_lease(lease)
            self._require_transport_authority_binding(lease)
            if self._state is not WorkerState.BUSY:
                raise WorkerError("worker_not_busy")
            self._active_lease = None
            self._state = WorkerState.READY_IDLE

    def transport_authority_for(self, lease: GenerationLease) -> object:
        """Return only the exact active lease's opaque HTTP authority."""

        with self._guard:
            self._require_active_lease(lease)
            if self._state is not WorkerState.BUSY:
                raise WorkerError("worker_not_busy")
            authority = self._require_transport_authority_binding(lease)
            self._resolve_transport_authority(authority)
            return authority

    async def abort(
        self,
        lease: GenerationLease,
        *,
        reason: str,
        deadline: float,
    ) -> WorkerStopReceipt:
        self._require_future_deadline(deadline)
        with self._guard:
            self._require_active_lease(lease)
            if self._state is not WorkerState.BUSY:
                raise WorkerError("worker_not_busy")
            self._state = WorkerState.STOPPING
        return await self._await_stop(reason=reason, deadline=deadline)

    async def shutdown(self, *, deadline: float) -> WorkerStopReceipt | None:
        self._require_future_deadline(deadline)
        with self._guard:
            if self._state is WorkerState.STOPPED:
                return None
            if self._state is WorkerState.QUARANTINED:
                raise WorkerError("worker_quarantined")
            if self._state in {WorkerState.STARTING, WorkerState.STOPPING}:
                raise WorkerError("worker_unsafe_state")
            if (
                self._process is None
                or self._process_identity is None
                or self._epoch is None
            ):
                self._enter_quarantine()
                raise WorkerError("worker_quarantined")
            self._state = WorkerState.STOPPING
        return await self._await_stop(reason="shutdown", deadline=deadline)

    async def _await_stop(
        self,
        *,
        reason: str,
        deadline: float,
    ) -> WorkerStopReceipt:
        """Shield the exact-child stop through repeated caller cancellation."""

        stop_task = asyncio.create_task(
            self._stop_current(reason=reason, deadline=deadline)
        )
        cancellation_requested = False
        while not stop_task.done():
            try:
                await asyncio.shield(stop_task)
            except asyncio.CancelledError:
                cancellation_requested = True
            except BaseException:
                # Read the completed task below so every driver failure is
                # normalized at this lifecycle boundary.
                pass
        try:
            receipt = stop_task.result()
        except WorkerError:
            raise
        except BaseException:
            self._enter_quarantine()
            raise WorkerError("worker_quarantined") from None
        if cancellation_requested:
            raise asyncio.CancelledError
        return receipt

    async def _stop_current(
        self,
        *,
        reason: str,
        deadline: float,
    ) -> WorkerStopReceipt:
        with self._guard:
            process = self._process
            process_identity = self._process_identity
            epoch = self._epoch
            lease = self._active_lease
            if process is None or process_identity is None or epoch is None:
                self._enter_quarantine()
                raise WorkerError("worker_quarantined")
            if self._state is not WorkerState.STOPPING:
                self._enter_quarantine()
                raise WorkerError("worker_quarantined")

        signals: list[str] = []
        try:
            self._driver.terminate_group(
                process,
                process_identity=process_identity,
            )
            signals.append("SIGTERM")
            term_deadline = min(
                float(deadline),
                float(self._clock()) + self._term_grace_seconds,
            )
            process_exit = await self._await_until(
                self._driver.wait_reaped(
                    process,
                    process_identity=process_identity,
                    deadline=term_deadline,
                ),
                deadline=term_deadline,
            )
            if process_exit is _DEADLINE_EXPIRED:
                process_exit = None
            if not self._matching_exit(
                process_exit,
                epoch.pid,
                process_identity,
            ):
                self._driver.kill_group(
                    process,
                    process_identity=process_identity,
                )
                signals.append("SIGKILL")
                process_exit = await self._await_until(
                    self._driver.wait_reaped(
                        process,
                        process_identity=process_identity,
                        deadline=deadline,
                    ),
                    deadline=deadline,
                )
                if process_exit is _DEADLINE_EXPIRED:
                    process_exit = None
        except BaseException:
            self._enter_quarantine()
            raise WorkerError("worker_quarantined") from None
        if not self._matching_exit(process_exit, epoch.pid, process_identity):
            self._enter_quarantine()
            raise WorkerError("worker_quarantined")

        assert process_exit is not None
        receipt = WorkerStopReceipt(
            epoch_id=epoch.epoch_id,
            generation_id=None if lease is None else lease.generation_id,
            pid=process_exit.pid,
            reason=reason,
            signals=tuple(signals),
            returncode=process_exit.returncode,
        )
        self._accept_stop_receipt(receipt)
        return receipt

    def _accept_stop_receipt(self, receipt: WorkerStopReceipt) -> None:
        with self._guard:
            epoch = self._epoch
            process = self._process
            if (
                epoch is None
                or process is None
                or receipt.epoch_id != epoch.epoch_id
                or receipt.pid != epoch.pid
            ):
                raise WorkerError("stale_worker_epoch")
            expected_generation_id = (
                None
                if self._active_lease is None
                else self._active_lease.generation_id
            )
            if receipt.generation_id != expected_generation_id:
                raise WorkerError("stale_generation_lease")
            if self._state is not WorkerState.STOPPING:
                raise WorkerError("worker_unsafe_state")
            try:
                self._launch_authority.release_reaped(self._authority_owner)
            except WorkerError:
                self._state = WorkerState.QUARANTINED
                raise
            self._last_stop_receipt = receipt
            self._process = None
            self._process_identity = None
            self._transport_authority = ()
            self._epoch = None
            self._active_lease = None
            self._retained_cleanup_handles.pop(id(process), None)
            self._state = WorkerState.STOPPED

    def _require_active_lease(self, lease: GenerationLease) -> None:
        with self._guard:
            epoch = self._epoch
            active = self._active_lease
            if epoch is None or lease.epoch_id != epoch.epoch_id:
                raise WorkerError("stale_worker_epoch")
            if active is None or lease is not active:
                raise WorkerError("stale_generation_lease")

    def _current_transport_authority(self) -> object:
        authorities = self._transport_authority
        if type(authorities) is not tuple or len(authorities) != 1:
            raise WorkerError("stale_transport_authority")
        authority = authorities[0]
        if not self._is_opaque_authority(authority):
            raise WorkerError("stale_transport_authority")
        return authority

    def _require_transport_authority_binding(
        self,
        lease: GenerationLease,
    ) -> object:
        authority = self._current_transport_authority()
        try:
            lease_authority = lease.transport_authority
        except BaseException:
            raise WorkerError("stale_transport_authority") from None
        if lease_authority is not authority:
            raise WorkerError("stale_transport_authority")
        return authority

    def _resolve_transport_authority(self, authority: object) -> None:
        try:
            credentials = self._transport_credential_resolver(authority)
        except WorkerError:
            raise
        except BaseException:
            raise WorkerError("stale_transport_authority") from None
        if inspect.isawaitable(credentials):
            close = getattr(credentials, "close", None)
            if callable(close):
                close()
            raise WorkerError("stale_transport_authority")

    @staticmethod
    def _is_opaque_authority(authority: object) -> bool:
        return authority is not None and not isinstance(
            authority,
            (
                bool,
                int,
                float,
                complex,
                str,
                bytes,
                bytearray,
                tuple,
                frozenset,
                list,
                dict,
                set,
            ),
        )

    def _launch_argv(self, port: int) -> tuple[str, ...]:
        return (
            self._launch_spec.binary_path,
            "--model",
            self._launch_spec.model_path,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--parallel",
            "1",
            *self._launch_spec.extra_args,
        )

    def _release_unspawned_authority(self) -> None:
        try:
            self._launch_authority.release_unspawned(self._authority_owner)
        except WorkerError:
            with self._guard:
                self._state = WorkerState.QUARANTINED
            raise

    def _enter_quarantine(self) -> None:
        with self._guard:
            self._state = WorkerState.QUARANTINED
        self._launch_authority.quarantine(self._authority_owner)

    def _require_future_deadline(self, deadline: float) -> None:
        if not math.isfinite(deadline) or float(deadline) <= float(self._clock()):
            raise WorkerError("worker_deadline_elapsed")

    @staticmethod
    def _matching_exit(
        process_exit: ProcessExit | None,
        pid: int,
        process_identity: object,
    ) -> bool:
        return (
            isinstance(process_exit, ProcessExit)
            and isinstance(process_exit.pid, int)
            and not isinstance(process_exit.pid, bool)
            and process_exit.pid > 0
            and process_exit.pid == pid
            and isinstance(process_exit.returncode, int)
            and not isinstance(process_exit.returncode, bool)
            and process_exit.process_identity is process_identity
        )

    def _artifacts_match_spec(self, receipt: object) -> bool:
        return (
            isinstance(receipt, ArtifactVerificationReceipt)
            and receipt.binary_path == self._launch_spec.binary_path
            and receipt.model_path == self._launch_spec.model_path
            and receipt.binary_sha256 == self._launch_spec.binary_sha256
            and receipt.model_sha256 == self._launch_spec.model_sha256
            and isinstance(receipt.binary_size, int)
            and not isinstance(receipt.binary_size, bool)
            and receipt.binary_size >= 0
            and isinstance(receipt.model_size, int)
            and not isinstance(receipt.model_size, bool)
            and receipt.model_size >= 0
            and isinstance(receipt.binary_device, int)
            and not isinstance(receipt.binary_device, bool)
            and receipt.binary_device >= 0
            and isinstance(receipt.binary_inode, int)
            and not isinstance(receipt.binary_inode, bool)
            and receipt.binary_inode >= 0
            and isinstance(receipt.model_device, int)
            and not isinstance(receipt.model_device, bool)
            and receipt.model_device >= 0
            and isinstance(receipt.model_inode, int)
            and not isinstance(receipt.model_inode, bool)
            and receipt.model_inode >= 0
        )

    async def _await_until(
        self,
        awaitable: Awaitable[Any],
        *,
        deadline: float,
        on_late_result: Callable[[object], Awaitable[None]] | None = None,
    ) -> object:
        operation = asyncio.create_task(awaitable)
        remaining = max(0.0, float(deadline) - float(self._clock()))
        timeout_task = asyncio.create_task(asyncio.sleep(remaining))
        try:
            done, _pending = await asyncio.wait(
                {operation, timeout_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            if on_late_result is None:
                operation.cancel()
            timeout_task.cancel()
            self._retain_driver_task(operation, on_late_result=on_late_result)
            self._retain_driver_task(timeout_task)
            raise
        completed_in_time = (
            operation in done
            and timeout_task not in done
            and float(self._clock()) < float(deadline)
        )
        if completed_in_time:
            timeout_task.cancel()
            self._retain_driver_task(timeout_task)
            return operation.result()
        if on_late_result is None:
            operation.cancel()
        self._retain_driver_task(operation, on_late_result=on_late_result)
        if timeout_task.done():
            self._consume_task_result(timeout_task)
        else:
            timeout_task.cancel()
            self._retain_driver_task(timeout_task)
        return _DEADLINE_EXPIRED

    def _retain_driver_task(
        self,
        task: asyncio.Task[object],
        *,
        on_late_result: Callable[[object], Awaitable[None]] | None = None,
    ) -> None:
        with self._guard:
            self._retained_driver_tasks.add(task)

        def finished(done: asyncio.Task[object]) -> None:
            with self._guard:
                self._retained_driver_tasks.discard(done)
            try:
                result = done.result()
            except BaseException:
                return
            if on_late_result is None:
                return
            # Retain the exact spawn result before asking an event loop to
            # schedule cleanup. A loop may already be closing here.
            with self._guard:
                self._retained_cleanup_handles[id(result)] = result
            cleanup_awaitable = on_late_result(result)
            try:
                cleanup = done.get_loop().create_task(cleanup_awaitable)
            except BaseException:
                close = getattr(cleanup_awaitable, "close", None)
                if callable(close):
                    close()
                return
            self._retain_driver_task(cleanup)

        task.add_done_callback(finished)

    @staticmethod
    def _consume_task_result(task: asyncio.Task[object]) -> None:
        try:
            task.result()
        except BaseException:
            pass

    async def _cleanup_late_started_process(self, value: object) -> None:
        process = value
        with self._guard:
            self._retained_cleanup_handles[id(process)] = process
        try:
            process_identity = process.process_identity
            if process_identity is None:
                return
            pid = process.pid
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                return
            self._driver.terminate_group(
                process,
                process_identity=process_identity,
            )
            cleanup_deadline = float(self._clock()) + self._term_grace_seconds
            process_exit = await self._await_until(
                self._driver.wait_reaped(
                    process,
                    process_identity=process_identity,
                    deadline=cleanup_deadline,
                ),
                deadline=cleanup_deadline,
            )
            if not self._matching_exit(process_exit, pid, process_identity):
                self._driver.kill_group(
                    process,
                    process_identity=process_identity,
                )
                kill_deadline = float(self._clock()) + self._term_grace_seconds
                process_exit = await self._await_until(
                    self._driver.wait_reaped(
                        process,
                        process_identity=process_identity,
                        deadline=kill_deadline,
                    ),
                    deadline=kill_deadline,
                )
            if self._matching_exit(process_exit, pid, process_identity):
                with self._guard:
                    self._retained_cleanup_handles.pop(id(process), None)
        except BaseException:
            return


__all__ = [
    "ArtifactVerificationReceipt",
    "ArtifactVerifier",
    "GenerationLease",
    "ManagedLlamaWorker",
    "ProcessExit",
    "StdlibArtifactVerifier",
    "WorkerEpochReceipt",
    "WorkerError",
    "WorkerLaunchSpec",
    "WorkerLaunchAuthority",
    "WorkerState",
    "WorkerStopReceipt",
    "canonical_argv_sha256",
]
