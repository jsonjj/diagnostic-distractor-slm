"""Fail-closed macOS process authority for one local ``llama-server``.

This module deliberately keeps process creation, filesystem access, readiness
I/O, and group signalling behind injected seams.  The packaged launcher can
bind those seams to ``subprocess.Popen`` and descriptor-oriented ``openat``
code after the pinned binary/GGUF integration gate has passed; unit tests never
need to launch a child.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
import math
import os
import re
import signal
import subprocess
import threading
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from .llama_worker import (
    ArtifactVerificationReceipt,
    ProcessExit,
    WorkerError,
    canonical_argv_sha256,
)


_EXPIRED = object()
_ALLOWED_ENVIRONMENT = frozenset(
    {
        "GGML_METAL_LOG_LEVEL",
        "GGML_METAL_PATH_RESOURCES",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
    }
)
_PRIMITIVE_IDENTITIES = (bool, int, float, str, bytes, tuple, frozenset)
_AUTHORIZATION = re.compile(
    r"(?i)authorization\s*:\s*bearer\s+[^\r\n]+"
)
_STRUCTURED_SECRET_MARKERS = (
    b'"messages"',
    b'"prompt"',
    b'"content"',
)
_SAFE_DIAGNOSTIC_LINES = frozenset(
    {
        b"server ready",
        b"server starting",
        b"server stopped",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    """Facts recomputed from one still-open artifact descriptor."""

    path: str
    sha256: str
    size: int
    device: int
    inode: int


class RetainedArtifactOwnership:
    """Descriptor ownership retained until readiness or exact child reap."""

    __slots__ = (
        "binary",
        "descriptor_binding_supported",
        "descriptor_identities",
        "model",
        "receipt",
        "_close_callback",
        "_closed",
        "_closing",
        "_condition",
        "_guard",
    )

    def __init__(
        self,
        *,
        receipt: ArtifactVerificationReceipt,
        binary: ArtifactIdentity,
        model: ArtifactIdentity,
        descriptor_binding_supported: bool,
        descriptor_identities: tuple[object, object],
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        self.receipt = receipt
        self.binary = binary
        self.model = model
        self.descriptor_binding_supported = descriptor_binding_supported
        self.descriptor_identities = descriptor_identities
        self._close_callback = close_callback
        self._closed = False
        self._closing = False
        self._guard = threading.Lock()
        self._condition = threading.Condition(self._guard)

    @property
    def closed(self) -> bool:
        with self._guard:
            return self._closed

    def close(self) -> None:
        with self._condition:
            while self._closing:
                self._condition.wait()
            if self._closed:
                return
            self._closing = True
        try:
            if self._close_callback is not None:
                self._close_callback()
        except BaseException:
            with self._condition:
                self._closing = False
                self._condition.notify_all()
            raise
        with self._condition:
            self._closed = True
            self._closing = False
            self._condition.notify_all()


class BoundedRedactedOutput:
    """Thread-safe bounded diagnostics that never retain known sensitive text."""

    def __init__(
        self,
        *,
        max_bytes: int,
        sensitive_values: Sequence[str | bytes] = (),
        max_line_bytes: int = 4_096,
    ) -> None:
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes < 32
        ):
            raise ValueError("max_bytes must be an integer of at least 32")
        if (
            isinstance(max_line_bytes, bool)
            or not isinstance(max_line_bytes, int)
            or max_line_bytes < 16
        ):
            raise ValueError("max_line_bytes must be an integer of at least 16")
        encoded: list[bytes] = []
        for value in sensitive_values:
            if isinstance(value, str):
                value = value.encode("utf-8")
            if not isinstance(value, bytes) or not value:
                raise ValueError("sensitive values must be non-empty text or bytes")
            encoded.append(value)
        self._max_bytes = max_bytes
        self._max_line_bytes = max_line_bytes
        self._sensitive_values = tuple(sorted(encoded, key=len, reverse=True))
        self._pending = bytearray()
        self._retained = bytearray()
        self._scrubbed = False
        self._guard = threading.Lock()

    def feed(self, data: bytes | bytearray | memoryview) -> None:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("diagnostic output must be bytes-like")
        with self._guard:
            if self._scrubbed:
                return
            self._pending.extend(bytes(data))
            while True:
                newline = self._pending.find(b"\n")
                if newline < 0:
                    if len(self._pending) > self._max_line_bytes * 2:
                        self._append_locked(b"[REDACTED OVERSIZED LINE]\n")
                        self._pending.clear()
                    return
                line = bytes(self._pending[: newline + 1])
                del self._pending[: newline + 1]
                self._append_locked(self._sanitize(line))

    def scrub(self) -> None:
        """Irreversibly drop redaction needles and reject future retention."""

        with self._guard:
            if self._scrubbed:
                return
            if self._pending:
                self._append_locked(self._sanitize(bytes(self._pending)))
                self._pending.clear()
            self._sensitive_values = ()
            self._scrubbed = True

    def snapshot(self) -> str:
        with self._guard:
            combined = bytearray(self._retained)
            if self._pending:
                combined.extend(self._sanitize(bytes(self._pending)))
            if len(combined) > self._max_bytes:
                del combined[: len(combined) - self._max_bytes]
            return bytes(combined).decode("utf-8", errors="replace")

    def _sanitize(self, raw: bytes) -> bytes:
        lowered = raw.lower()
        if any(marker in lowered for marker in _STRUCTURED_SECRET_MARKERS):
            return b"[REDACTED STRUCTURED OUTPUT]\n"
        if len(raw.rstrip(b"\r\n")) > self._max_line_bytes:
            return b"[REDACTED OVERSIZED LINE]\n"
        if _AUTHORIZATION.search(raw.decode("utf-8", errors="replace")):
            return b"Authorization: Bearer [REDACTED]\n"
        if any(value in raw for value in self._sensitive_values):
            return b"[REDACTED]\n"
        stripped = raw.rstrip(b"\r\n").lower()
        if stripped in _SAFE_DIAGNOSTIC_LINES:
            return stripped + b"\n"
        return b"[REDACTED UNRECOGNIZED OUTPUT]\n"

    def _append_locked(self, value: bytes) -> None:
        self._retained.extend(value)
        if len(self._retained) > self._max_bytes:
            del self._retained[: len(self._retained) - self._max_bytes]


@dataclass(frozen=True, slots=True, eq=False)
class SpawnChildClaim:
    """The exact child published before any post-creation inspection."""

    child: object = field(repr=False)


@dataclass(frozen=True, slots=True, eq=False)
class SpawnResult:
    """Completed process, argv, and pipe-drain attestation.

    A concrete spawner first calls ``claim_child`` immediately after process
    creation. It may only then read PID/PGID, construct the two required drains,
    and call ``complete``. The driver therefore retains the exact child when
    any inspection or drain construction subsequently raises.
    """

    child_claim: SpawnChildClaim = field(repr=False)
    child: object = field(repr=False)
    pid: object
    pgid: object
    executed_argv_sha256: str | None
    stdout_drain: object = field(repr=False)
    stderr_drain: object = field(repr=False)


class SpawnOwnership:
    """One-shot child and executed-argv ownership handoff.

    ``bind_executed_argv`` must be called with the actual private argv before
    process creation.  Only its hash is retained.  ``claim`` must then be the
    first operation after child creation.  These ordering rules are a required
    obligation of every concrete OS spawner.
    """

    __slots__ = (
        "_child_claim",
        "_child_claim_callback",
        "_complete_callback",
        "_executed_argv_sha256",
        "_guard",
        "_required_argv_flags",
        "_required_argv_pairs",
        "_result",
    )

    def __init__(
        self,
        child_claim_callback: Callable[[SpawnChildClaim], None],
        complete_callback: Callable[[SpawnResult], None],
        *,
        required_argv_pairs: Sequence[tuple[str, str]],
        required_argv_flags: Sequence[str] = (),
    ) -> None:
        self._child_claim_callback = child_claim_callback
        self._complete_callback = complete_callback
        self._child_claim: SpawnChildClaim | None = None
        self._executed_argv_sha256: str | None = None
        self._required_argv_pairs = tuple(required_argv_pairs)
        self._required_argv_flags = tuple(required_argv_flags)
        if any(
            not isinstance(flag, str)
            or not flag
            or "\x00" in flag
            for flag in self._required_argv_flags
        ):
            raise ValueError("required argv flags must be non-empty strings")
        self._result: SpawnResult | None = None
        self._guard = threading.Lock()

    def bind_executed_argv(self, argv: Sequence[str]) -> str:
        try:
            normalized = tuple(argv)
        except TypeError:
            raise WorkerError("invalid_worker_argv") from None
        if (
            not normalized
            or any(
                not isinstance(argument, str)
                or not argument
                or "\x00" in argument
                for argument in normalized
            )
        ):
            raise WorkerError("invalid_worker_argv")
        for flag, value in self._required_argv_pairs:
            matches = sum(
                1
                for index in range(len(normalized) - 1)
                if normalized[index] == flag and normalized[index + 1] == value
            )
            if matches != 1:
                raise WorkerError("invalid_worker_argv")
        for flag in self._required_argv_flags:
            matches = sum(
                1
                for index in range(len(normalized) - 1)
                if normalized[index] == flag
            )
            if matches != 1:
                raise WorkerError("invalid_worker_argv")
        digest = canonical_argv_sha256(normalized)
        with self._guard:
            if (
                self._child_claim is not None
                or self._result is not None
                or self._executed_argv_sha256 is not None
            ):
                raise WorkerError("worker_unsafe_state")
            self._executed_argv_sha256 = digest
        return digest

    def claim_child(self, child: object) -> SpawnChildClaim:
        if child is None:
            raise WorkerError("invalid_worker_process")
        with self._guard:
            if self._child_claim is not None or self._result is not None:
                raise WorkerError("worker_unsafe_state")
            claim = SpawnChildClaim(child=child)
            self._child_claim = claim
        self._child_claim_callback(claim)
        return claim

    def complete(
        self,
        child_claim: SpawnChildClaim,
        *,
        pid: object,
        pgid: object,
        stdout_drain: object,
        stderr_drain: object,
    ) -> SpawnResult:
        with self._guard:
            if (
                self._result is not None
                or child_claim is not self._child_claim
            ):
                raise WorkerError("worker_unsafe_state")
            result = SpawnResult(
                child_claim=child_claim,
                child=child_claim.child,
                pid=pid,
                pgid=pgid,
                executed_argv_sha256=self._executed_argv_sha256,
                stdout_drain=stdout_drain,
                stderr_drain=stderr_drain,
            )
            self._result = result
        self._complete_callback(result)
        return result

    @property
    def claimed_result(self) -> SpawnResult | None:
        with self._guard:
            return self._result


@dataclass(frozen=True, slots=True)
class SpawnSpecification:
    """Complete no-shell spawn contract passed to the packaged Popen seam."""

    argv: tuple[str, ...]
    executable: str
    shell: bool
    start_new_session: bool
    stdin: int
    stdout: int
    stderr: int
    close_fds: bool
    cwd: str
    env: Mapping[str, str]
    artifact_ownership: RetainedArtifactOwnership
    readiness_api_key: str = field(repr=False)
    readiness_nonce: str = field(repr=False)
    readiness_alias: str = field(repr=False)
    stdout_collector: BoundedRedactedOutput
    stderr_collector: BoundedRedactedOutput
    spawn_ownership: SpawnOwnership = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReadinessChallenge:
    port: int
    api_key: str = field(repr=False)
    nonce: str = field(repr=False)
    alias: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReadinessProof:
    authenticated: bool
    nonce: str = field(repr=False)
    alias: str = field(repr=False)
    port: int


class _ProcessIdentity:
    __slots__ = ()


class _TransportAuthority:
    __slots__ = ()


class _ProcessGroupIdentity:
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class _TransportCredentials:
    bearer_token: str = field(repr=False)
    model_alias: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SignalGroupRequest:
    """Attested exact-child signal request for a concrete macOS seam.

    The seam must atomically confirm that ``child`` still denotes ``pid`` and
    its newly-created session group immediately before signalling ``pgid``. It
    must raise instead of signalling when that proof is unavailable.  This is
    the OS-specific guard against the final liveness-check/PID-reuse race.
    """

    child: object = field(repr=False)
    pid: int
    pgid: int
    group_identity: object = field(repr=False)
    signum: int


@dataclass(frozen=True, slots=True, eq=False)
class MacOSProcessHandle:
    """Inert public snapshot; authority remains in the driver's registry."""

    pid: int
    process_identity: object
    transport_authority: object
    launch_artifacts: ArtifactVerificationReceipt
    launch_argv_sha256: str


class InterprocessWorkerLock(Protocol):
    """Non-blocking system-wide single-worker lock used by production."""

    def acquire(self) -> object | None: ...

    def release(self, lease: object) -> None: ...

    def close(self) -> None: ...


class MacOSDriverAuthority:
    """Cross-driver reservation with an optional interprocess lock seam."""

    def __init__(
        self,
        *,
        interprocess_lock: InterprocessWorkerLock | None = None,
    ) -> None:
        if interprocess_lock is not None and not (
            callable(getattr(interprocess_lock, "acquire", None))
            and callable(getattr(interprocess_lock, "release", None))
            and callable(getattr(interprocess_lock, "close", None))
        ):
            raise ValueError("interprocess_lock must provide acquire/release/close")
        self._guard = threading.Lock()
        self._owner: object | None = None
        self._interprocess_lock = interprocess_lock
        self._interprocess_lease: object | None = None
        self._closed = False

    def reserve(self, owner: object) -> None:
        with self._guard:
            if self._closed or self._owner is not None:
                raise WorkerError("worker_unsafe_state")
            lease = None
            if self._interprocess_lock is not None:
                try:
                    lease = self._interprocess_lock.acquire()
                except BaseException:
                    raise WorkerError("worker_unsafe_state") from None
                if lease is None:
                    raise WorkerError("worker_unsafe_state")
            self._owner = owner
            self._interprocess_lease = lease

    def release(self, owner: object) -> None:
        with self._guard:
            if self._owner is not owner:
                raise WorkerError("worker_unsafe_state")
            if self._interprocess_lock is not None:
                try:
                    self._interprocess_lock.release(self._interprocess_lease)
                except BaseException:
                    # Keep both local and interprocess authority reserved so a
                    # caller can retry without permitting a second child.
                    raise WorkerError("worker_unsafe_state") from None
            self._owner = None
            self._interprocess_lease = None

    def close(self) -> None:
        with self._guard:
            if self._closed:
                return
            if self._owner is not None:
                raise WorkerError("worker_unsafe_state")
            if self._interprocess_lock is not None:
                try:
                    self._interprocess_lock.close()
                except BaseException:
                    raise WorkerError("worker_unsafe_state") from None
            self._closed = True


_PRODUCTION_AUTHORITY = MacOSDriverAuthority()


class ArtifactRevalidator(Protocol):
    descriptor_binding_supported: bool

    async def __call__(
        self,
        receipt: ArtifactVerificationReceipt,
        *,
        deadline: float,
    ) -> RetainedArtifactOwnership: ...


class SpawnCallable(Protocol):
    async def __call__(self, specification: SpawnSpecification) -> SpawnResult: ...


class ReadinessCallable(Protocol):
    async def __call__(
        self,
        child: object,
        challenge: ReadinessChallenge,
        *,
        deadline: float,
    ) -> ReadinessProof: ...


@dataclass(slots=True)
class _ProcessRecord:
    ticket: object
    handle: MacOSProcessHandle | None
    child: object | None
    pid: object
    pgid: object
    identity: object
    transport_authority: object
    transport_credentials: _TransportCredentials | None = field(repr=False)
    group_identity: object | None
    artifacts: ArtifactVerificationReceipt
    argv_sha256: str
    executed_argv_sha256: str | None
    artifact_ownership: RetainedArtifactOwnership
    port: int | None
    api_key: str | None = field(repr=False)
    nonce: str | None = field(repr=False)
    alias: str | None = field(repr=False)
    stdout_collector: BoundedRedactedOutput
    stderr_collector: BoundedRedactedOutput
    stdout_drain: asyncio.Task[Any] | None = None
    stderr_drain: asyncio.Task[Any] | None = None
    child_claim: SpawnChildClaim | None = None
    spawn_result: SpawnResult | None = None
    state: str = "pre_spawn"
    term_sent: bool = False
    kill_sent: bool = False
    reap_task: asyncio.Task[ProcessExit | None] | None = None
    process_exit: ProcessExit | None = None
    reaped: bool = False
    authority_released: bool = False
    superseded: bool = False


class MacOSWorkerProcessDriver:
    """One-child driver implementing the stable ``WorkerProcessDriver`` protocol."""

    def __init__(
        self,
        *,
        spawn: SpawnCallable,
        revalidate_artifacts: ArtifactRevalidator,
        signal_group: Callable[[SignalGroupRequest], None],
        reap_process: Callable[[object], Awaitable[int]],
        readiness_probe: ReadinessCallable,
        process_is_live: Callable[[object], bool],
        clock: Callable[[], float],
        token_factory: Callable[[], str],
        authority: MacOSDriverAuthority | None = None,
        close_authority_on_shutdown: bool = False,
        environment: Mapping[str, str],
        cwd: str,
        require_descriptor_binding: bool = True,
        term_grace_seconds: float = 0.25,
        late_cleanup_seconds: float = 1.0,
        max_log_bytes: int = 32_768,
    ) -> None:
        for name, callback in (
            ("spawn", spawn),
            ("revalidate_artifacts", revalidate_artifacts),
            ("signal_group", signal_group),
            ("reap_process", reap_process),
            ("readiness_probe", readiness_probe),
            ("process_is_live", process_is_live),
            ("clock", clock),
            ("token_factory", token_factory),
        ):
            if not callable(callback):
                raise ValueError(f"{name} must be callable")
        if authority is not None and not isinstance(authority, MacOSDriverAuthority):
            raise ValueError("authority must be a MacOSDriverAuthority")
        if not isinstance(close_authority_on_shutdown, bool):
            raise ValueError("close_authority_on_shutdown must be boolean")
        if close_authority_on_shutdown and authority is None:
            raise ValueError("an owned authority must be explicit")
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or "\x00" in cwd:
            raise ValueError("cwd must be an absolute path")
        if (
            not math.isfinite(term_grace_seconds)
            or term_grace_seconds < 0
            or not math.isfinite(late_cleanup_seconds)
            or late_cleanup_seconds <= 0
        ):
            raise ValueError("cleanup deadlines must be finite and non-negative")
        unknown = set(environment).difference(_ALLOWED_ENVIRONMENT)
        if unknown:
            raise ValueError("environment contains non-allowlisted keys")
        clean_environment: dict[str, str] = {}
        for key, value in environment.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or not key
                or "\x00" in key
                or "\x00" in value
            ):
                raise ValueError("environment must contain NUL-free strings")
            clean_environment[key] = value

        self._spawn = spawn
        self._revalidate_artifacts = revalidate_artifacts
        self._signal_group = signal_group
        self._reap_process = reap_process
        self._readiness_probe = readiness_probe
        self._process_is_live = process_is_live
        self._clock = clock
        self._token_factory = token_factory
        self._authority = authority if authority is not None else _PRODUCTION_AUTHORITY
        self._close_authority_on_shutdown = close_authority_on_shutdown
        self._environment = MappingProxyType(dict(sorted(clean_environment.items())))
        self._cwd = cwd
        self._require_descriptor_binding = bool(require_descriptor_binding)
        self._term_grace_seconds = float(term_grace_seconds)
        self._late_cleanup_seconds = float(late_cleanup_seconds)
        self._max_log_bytes = max_log_bytes
        self._guard = threading.RLock()
        self._records: dict[MacOSProcessHandle, _ProcessRecord] = {}
        self._ticket_records: dict[object, _ProcessRecord] = {}
        self._retained_ownerships: set[RetainedArtifactOwnership] = set()
        self._spawn_tasks: set[asyncio.Task[MacOSProcessHandle]] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._shutdown_task: asyncio.Task[tuple[ProcessExit, ...]] | None = None
        self._shutdown_result: tuple[ProcessExit, ...] | None = None
        self._closed = False

    @property
    def descriptor_binding_supported(self) -> bool:
        return bool(
            getattr(
                self._revalidate_artifacts,
                "descriptor_binding_supported",
                False,
            )
        )

    async def start(
        self,
        argv: Sequence[str],
        *,
        start_new_session: bool,
        deadline: float,
        artifacts: ArtifactVerificationReceipt,
    ) -> MacOSProcessHandle:
        self._bind_loop()
        self._require_future_deadline(deadline)
        normalized_argv = self._validate_argv(argv, start_new_session)
        if not isinstance(artifacts, ArtifactVerificationReceipt):
            raise WorkerError("artifact_revalidation_failed")
        with self._guard:
            if self._closed:
                raise WorkerError("worker_driver_closed")
        ticket = object()
        self._authority.reserve(ticket)
        pipeline = asyncio.create_task(
            self._prepare_and_spawn(
                ticket=ticket,
                argv=normalized_argv,
                deadline=deadline,
                artifacts=artifacts,
            )
        )
        self._retain_spawn_task(pipeline)
        try:
            result = await self._await_task(pipeline, deadline=deadline)
        except asyncio.CancelledError:
            self._schedule_late_cleanup(pipeline)
            raise
        if result is _EXPIRED:
            self._schedule_late_cleanup(pipeline)
            raise WorkerError("worker_spawn_deadline")
        assert isinstance(result, MacOSProcessHandle)
        return result

    async def await_ready(
        self,
        process: MacOSProcessHandle,
        *,
        port: int,
        deadline: float,
    ) -> bool:
        self._bind_loop()
        self._require_future_deadline(deadline)
        record = self._record_for_process(process)
        if port != record.port or record.reaped:
            return False
        if not self._live(record):
            return False
        if not all(
            isinstance(value, str) and value
            for value in (record.api_key, record.nonce, record.alias)
        ):
            return False
        challenge = ReadinessChallenge(
            port=port,
            api_key=record.api_key,
            nonce=record.nonce,
            alias=record.alias,
        )
        probe: asyncio.Task[ReadinessProof] | None = None
        try:
            probe = asyncio.create_task(
                self._readiness_probe(
                    record.child,
                    challenge,
                    deadline=deadline,
                )
            )
            self._retain_background_task(probe)
            proof = await self._await_task(probe, deadline=deadline)
        except asyncio.CancelledError:
            if probe is not None:
                probe.cancel()
            raise
        except BaseException:
            return False
        if proof is _EXPIRED:
            assert probe is not None
            probe.cancel()
            return False
        if not isinstance(proof, ReadinessProof):
            return False
        verified = (
            proof.authenticated is True
            and proof.nonce == record.nonce
            and proof.alias == record.alias
            and proof.port == record.port
            and self._live(record)
        )
        if verified:
            with self._guard:
                if record.reaped:
                    return False
            if not self._close_ownership(record.artifact_ownership):
                return False
            with self._guard:
                if record.reaped:
                    return False
                record.state = "ready"
        return verified

    def terminate_group(
        self,
        process: MacOSProcessHandle,
        *,
        process_identity: object,
    ) -> None:
        self._require_bound_thread()
        record = self._record_for_control(process, process_identity)
        self._signal_record(record, signal.SIGTERM)

    def kill_group(
        self,
        process: MacOSProcessHandle,
        *,
        process_identity: object,
    ) -> None:
        self._require_bound_thread()
        record = self._record_for_control(process, process_identity)
        self._signal_record(record, signal.SIGKILL)

    async def wait_reaped(
        self,
        process: MacOSProcessHandle,
        *,
        process_identity: object,
        deadline: float,
    ) -> ProcessExit | None:
        self._bind_loop()
        self._require_future_deadline(deadline)
        record = self._record_for_control(
            process,
            process_identity,
            allow_exited=True,
        )
        return await self._wait_record_reaped(record, deadline=deadline)

    def resolve_transport_credentials(
        self,
        transport_authority: object,
    ) -> _TransportCredentials:
        """Resolve credentials only for the exact ready live driver record."""

        self._require_bound_thread()
        if transport_authority is None or isinstance(
            transport_authority, _PRIMITIVE_IDENTITIES
        ):
            raise WorkerError("stale_transport_authority")
        with self._guard:
            for record in self._ticket_records.values():
                if record.transport_authority is not transport_authority:
                    continue
                if record.state != "ready" or record.reaped:
                    break
                if not self._live(record):
                    break
                if record.transport_credentials is not None:
                    return record.transport_credentials
                break
        raise WorkerError("stale_transport_authority")

    def executed_argv_matches(
        self,
        process: MacOSProcessHandle,
        *,
        process_identity: object,
        executed_argv_sha256: str,
    ) -> bool:
        """Compare a trusted spawner receipt with the registry-bound digest."""

        self._require_bound_thread()
        record = self._record_for_control(
            process,
            process_identity,
            allow_exited=True,
        )
        return (
            isinstance(executed_argv_sha256, str)
            and record.executed_argv_sha256 == executed_argv_sha256
        )

    async def shutdown_all(self, *, deadline: float) -> tuple[ProcessExit, ...]:
        self._bind_loop()
        self._require_future_deadline(deadline)
        with self._guard:
            self._closed = True
            if self._shutdown_result is not None:
                return self._shutdown_result
            task = self._shutdown_task
            if task is None or task.done():
                task = asyncio.create_task(self._shutdown_pipeline(deadline))
                self._shutdown_task = task
                task.add_done_callback(self._consume_task)
        # Caller cancellation never cancels the sole cleanup pipeline. A later
        # call awaits the same task, or retries after a bounded failure.
        return await asyncio.shield(task)

    async def _shutdown_pipeline(
        self,
        deadline: float,
    ) -> tuple[ProcessExit, ...]:
        with self._guard:
            spawn_tasks = tuple(self._spawn_tasks)
        for task in spawn_tasks:
            try:
                result = await self._await_task(task, deadline=deadline)
            except BaseException:
                # The pipeline itself retains claimed children in the ticket
                # registry before propagating. Continue into exact cleanup.
                continue
            if result is _EXPIRED:
                raise WorkerError("worker_shutdown_incomplete")

        with self._guard:
            records = tuple(self._ticket_records.values())
        for record in records:
            if record.child is not None and not record.reaped:
                self._signal_record(record, signal.SIGTERM)

        for record in records:
            if record.child is None or record.reaped:
                continue
            term_deadline = min(
                deadline,
                float(self._clock()) + self._term_grace_seconds,
            )
            await self._wait_record_reaped(record, deadline=term_deadline)
            if not record.reaped:
                self._signal_record(record, signal.SIGKILL)
                await self._wait_record_reaped(record, deadline=deadline)
            if not record.reaped:
                raise WorkerError("worker_shutdown_incomplete")

        # Exact reap is published before descriptor cleanup. Retry any close
        # callback that failed transiently without hiding the exit receipt.
        with self._guard:
            ownerships = tuple(self._retained_ownerships)
        for ownership in ownerships:
            self._close_ownership(ownership)
        with self._guard:
            if self._retained_ownerships:
                raise WorkerError("worker_shutdown_incomplete")

        for record in records:
            if record.reaped and not record.authority_released:
                self._release_record_authority(record)
            for drain in (record.stdout_drain, record.stderr_drain):
                if drain is None:
                    continue
                result = await self._await_task(drain, deadline=deadline)
                if result is _EXPIRED:
                    raise WorkerError("worker_shutdown_incomplete")

        # Concrete stdlib seams may own shielded to_thread work after caller
        # cancellation. Their cleanup is part of production shutdown, not a
        # test-only observation hook.
        for seam in (self._revalidate_artifacts, self._spawn):
            cleanup = getattr(seam, "wait_for_background_cleanup", None)
            if not callable(cleanup):
                continue
            try:
                task = asyncio.create_task(cleanup())
                result = await self._await_task(task, deadline=deadline)
            except BaseException:
                raise WorkerError("worker_shutdown_incomplete") from None
            if result is _EXPIRED:
                raise WorkerError("worker_shutdown_incomplete")

        current = asyncio.current_task()
        with self._guard:
            background = tuple(
                task
                for task in self._background_tasks
                if task is not current
            )
        for task in background:
            try:
                result = await self._await_task(task, deadline=deadline)
            except BaseException:
                # Reap/close state above is authoritative; background task
                # exceptions have already been consumed and must not mask it.
                continue
            if result is _EXPIRED:
                raise WorkerError("worker_shutdown_incomplete")

        if self._close_authority_on_shutdown:
            try:
                self._authority.close()
            except BaseException:
                raise WorkerError("worker_shutdown_incomplete") from None

        with self._guard:
            if any(record.reaped and not record.authority_released for record in records):
                raise WorkerError("worker_shutdown_incomplete")
            exits = tuple(
                record.process_exit
                for record in records
                if record.process_exit is not None
            )
            self._shutdown_result = exits
        return exits

    def redacted_output(
        self,
        process: MacOSProcessHandle,
        *,
        process_identity: object,
    ) -> tuple[str, str]:
        self._require_bound_thread()
        record = self._record_for_control(
            process,
            process_identity,
            allow_exited=True,
        )
        return (
            record.stdout_collector.snapshot(),
            record.stderr_collector.snapshot(),
        )

    async def _prepare_and_spawn(
        self,
        *,
        ticket: object,
        argv: tuple[str, ...],
        deadline: float,
        artifacts: ArtifactVerificationReceipt,
    ) -> MacOSProcessHandle:
        ownership: RetainedArtifactOwnership | None = None
        record: _ProcessRecord | None = None
        try:
            ownership = await self._revalidate_artifacts(
                artifacts,
                deadline=deadline,
            )
            if isinstance(ownership, RetainedArtifactOwnership):
                with self._guard:
                    self._retained_ownerships.add(ownership)
            self._validate_artifact_ownership(ownership, artifacts)
            token = self._token_factory()
            if not isinstance(token, str) or not token or "\x00" in token:
                raise WorkerError("invalid_worker_identity")
            seed = token.encode("utf-8", errors="strict")
            api_key = base64.urlsafe_b64encode(
                hashlib.sha256(b"wayline/bearer/v1\0" + seed).digest()
            ).decode("ascii").rstrip("=")
            nonce = hashlib.sha256(
                b"wayline/readiness-nonce/v1\0" + api_key.encode("ascii")
            ).hexdigest()
            alias = "wayline-" + hashlib.sha256(
                b"wayline/model-alias/v1\0" + api_key.encode("ascii")
            ).hexdigest()[:32]
            sensitive = (
                api_key,
                nonce,
                alias,
                artifacts.binary_path,
                artifacts.model_path,
            )
            stdout_collector = BoundedRedactedOutput(
                max_bytes=self._max_log_bytes,
                sensitive_values=sensitive,
            )
            stderr_collector = BoundedRedactedOutput(
                max_bytes=self._max_log_bytes,
                sensitive_values=sensitive,
            )
            identity = _ProcessIdentity()
            transport_authority = _TransportAuthority()
            argv_sha256 = canonical_argv_sha256(argv)
            record = _ProcessRecord(
                ticket=ticket,
                handle=None,
                child=None,
                pid=None,
                pgid=None,
                identity=identity,
                transport_authority=transport_authority,
                transport_credentials=_TransportCredentials(
                    bearer_token=api_key,
                    model_alias=alias,
                ),
                group_identity=None,
                artifacts=artifacts,
                argv_sha256=argv_sha256,
                executed_argv_sha256=None,
                artifact_ownership=ownership,
                port=None,
                api_key=api_key,
                nonce=nonce,
                alias=alias,
                stdout_collector=stdout_collector,
                stderr_collector=stderr_collector,
            )
            with self._guard:
                self._ticket_records[ticket] = record
            spawn_ownership = SpawnOwnership(
                lambda claim: self._claim_child(record, claim),
                lambda result: self._complete_spawn_claim(record, result),
                required_argv_pairs=(("--alias", alias),),
                required_argv_flags=("--api-key-file",),
            )
            specification = SpawnSpecification(
                argv=argv,
                executable=argv[0],
                shell=False,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                cwd=self._cwd,
                env=self._environment,
                artifact_ownership=ownership,
                readiness_api_key=api_key,
                readiness_nonce=nonce,
                readiness_alias=alias,
                stdout_collector=stdout_collector,
                stderr_collector=stderr_collector,
                spawn_ownership=spawn_ownership,
            )
            result = await self._spawn(specification)
            self._validate_spawn_result(record, result)
            port = self._port_from_argv(argv)
            assert isinstance(record.pid, int)
            handle = MacOSProcessHandle(
                pid=record.pid,
                process_identity=identity,
                transport_authority=transport_authority,
                launch_artifacts=artifacts,
                launch_argv_sha256=argv_sha256,
            )
            with self._guard:
                for previous in self._records.values():
                    if previous.reaped:
                        previous.superseded = True
                record.handle = handle
                record.port = port
                record.state = "spawned"
                self._records[handle] = record
            return handle
        except BaseException:
            if record is not None and record.child is not None:
                with self._guard:
                    record.state = "spawn_failed"
                # Exact child, artifact descriptors, and authority stay owned;
                # shutdown_all can signal (when attested) and exact-reap it.
            else:
                if ownership is not None:
                    self._close_ownership(ownership)
                self._release_unspawned_ticket(ticket, record)
            raise

    def _claim_child(
        self,
        record: _ProcessRecord,
        claim: SpawnChildClaim,
    ) -> None:
        """Publish exact child ownership before any fallible inspection."""

        with self._guard:
            if record.child_claim is not None or record.child is not None:
                raise WorkerError("worker_unsafe_state")
            record.child_claim = claim
            record.child = claim.child
            record.state = "child_claimed"

    def _complete_spawn_claim(
        self,
        record: _ProcessRecord,
        result: SpawnResult,
    ) -> None:
        """Publish fallible PID/group/drain attestations after child claim."""

        with self._guard:
            if (
                record.spawn_result is not None
                or result.child_claim is not record.child_claim
                or result.child is not record.child
            ):
                raise WorkerError("worker_unsafe_state")
            record.spawn_result = result
            record.pid = result.pid
            record.pgid = result.pgid
            record.executed_argv_sha256 = result.executed_argv_sha256
            if (
                not isinstance(result.pid, bool)
                and isinstance(result.pid, int)
                and result.pid > 0
                and not isinstance(result.pgid, bool)
                and isinstance(result.pgid, int)
                and result.pgid == result.pid
            ):
                # New-session groups are derived as PGID == child PID. This
                # provisional attestation permits cleanup even if the spawn
                # seam raises immediately after the synchronous claim.
                record.group_identity = _ProcessGroupIdentity()
            if isinstance(result.stdout_drain, asyncio.Task):
                record.stdout_drain = result.stdout_drain
                self._retain_background_task(result.stdout_drain)
            if isinstance(result.stderr_drain, asyncio.Task):
                record.stderr_drain = result.stderr_drain
                self._retain_background_task(result.stderr_drain)
            record.state = "claimed"

    def _validate_spawn_result(
        self,
        record: _ProcessRecord,
        returned: object,
    ) -> None:
        result = record.spawn_result
        if not isinstance(returned, SpawnResult) or returned is not result:
            raise WorkerError("invalid_worker_process")
        if result.child is None:
            raise WorkerError("invalid_worker_process")
        for value in (result.pid, result.pgid):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise WorkerError("invalid_worker_process")
        if result.pgid != result.pid:
            raise WorkerError("invalid_worker_process_group")
        if (
            not isinstance(result.executed_argv_sha256, str)
            or _SHA256.fullmatch(result.executed_argv_sha256) is None
        ):
            raise WorkerError("invalid_worker_process")
        for drain in (result.stdout_drain, result.stderr_drain):
            if (
                not isinstance(drain, asyncio.Task)
                or drain.get_loop() is not self._loop
            ):
                raise WorkerError("invalid_worker_process")
        with self._guard:
            record.group_identity = _ProcessGroupIdentity()

    def _validate_artifact_ownership(
        self,
        ownership: object,
        receipt: ArtifactVerificationReceipt,
    ) -> None:
        if not isinstance(ownership, RetainedArtifactOwnership):
            raise WorkerError("artifact_revalidation_failed")
        if ownership.receipt is not receipt:
            raise WorkerError("artifact_revalidation_failed")
        expected_binary = ArtifactIdentity(
            path=receipt.binary_path,
            sha256=receipt.binary_sha256,
            size=receipt.binary_size,
            device=receipt.binary_device,
            inode=receipt.binary_inode,
        )
        expected_model = ArtifactIdentity(
            path=receipt.model_path,
            sha256=receipt.model_sha256,
            size=receipt.model_size,
            device=receipt.model_device,
            inode=receipt.model_inode,
        )
        if ownership.binary != expected_binary or ownership.model != expected_model:
            raise WorkerError("artifact_revalidation_failed")
        if self._require_descriptor_binding and not (
            self.descriptor_binding_supported
            and ownership.descriptor_binding_supported is True
        ):
            raise WorkerError("descriptor_binding_unavailable")
        if ownership.descriptor_binding_supported:
            identities = ownership.descriptor_identities
            if (
                not isinstance(identities, tuple)
                or len(identities) != 2
                or any(
                    identity is None
                    or isinstance(identity, _PRIMITIVE_IDENTITIES)
                    for identity in identities
                )
            ):
                raise WorkerError("descriptor_binding_unavailable")

    def _record_for_process(self, process: object) -> _ProcessRecord:
        if not isinstance(process, MacOSProcessHandle):
            raise WorkerError("stale_process_handle")
        with self._guard:
            record = self._records.get(process)
        if record is None or record.handle is not process:
            raise WorkerError("stale_process_handle")
        self._validate_public_handle(record, process)
        return record

    def _record_for_control(
        self,
        process: object,
        process_identity: object,
        *,
        allow_exited: bool = False,
    ) -> _ProcessRecord:
        record = self._record_for_process(process)
        if record.identity is not process_identity:
            raise WorkerError("stale_process_identity")
        if (
            record.reaped
            and record.superseded
            and not allow_exited
        ):
            raise WorkerError("stale_process_handle")
        return record

    @staticmethod
    def _validate_public_handle(
        record: _ProcessRecord,
        process: MacOSProcessHandle,
    ) -> None:
        if (
            process.process_identity is not record.identity
            or process.transport_authority is not record.transport_authority
            or process.launch_artifacts is not record.artifacts
            or process.launch_argv_sha256 != record.argv_sha256
        ):
            raise WorkerError("stale_process_handle")

    def _signal_record(self, record: _ProcessRecord, signum: int) -> None:
        with self._guard:
            if record.reaped:
                # Internal shutdown may observe a deadline immediately before
                # the retained reap task publishes its exact exit. Public
                # stale handles are rejected by _record_for_control first;
                # this branch only makes that lifecycle race an idempotent
                # no-op instead of risking a signal after reap.
                return
            if signum == signal.SIGTERM and record.term_sent:
                return
            if signum == signal.SIGKILL and record.kill_sent:
                return
            if (
                record.child is None
                or isinstance(record.pid, bool)
                or not isinstance(record.pid, int)
                or isinstance(record.pgid, bool)
                or not isinstance(record.pgid, int)
                or record.pid <= 0
                or record.pgid != record.pid
                or record.group_identity is None
            ):
                return
            if not self._live(record):
                return
            request = SignalGroupRequest(
                child=record.child,
                pid=record.pid,
                pgid=record.pgid,
                group_identity=record.group_identity,
                signum=signum,
            )
            # The concrete seam raises if its final exact-child attestation
            # fails. Flags change only after it confirms signal delivery.
            self._signal_group(request)
            if signum == signal.SIGTERM:
                record.term_sent = True
            else:
                record.kill_sent = True
            record.state = "stopping"

    async def _wait_record_reaped(
        self,
        record: _ProcessRecord,
        *,
        deadline: float,
    ) -> ProcessExit | None:
        with self._guard:
            if record.reaped:
                return record.process_exit
            if record.reap_task is None:
                record.reap_task = asyncio.create_task(self._reap_record(record))
                self._retain_background_task(record.reap_task)
            task = record.reap_task
        result = await self._await_task(task, deadline=deadline)
        if result is _EXPIRED:
            return None
        if result is not None and not isinstance(result, ProcessExit):
            raise WorkerError("invalid_process_exit")
        return result

    async def _reap_record(self, record: _ProcessRecord) -> ProcessExit | None:
        if record.child is None:
            raise WorkerError("invalid_worker_process")
        returncode = await self._reap_process(record.child)
        if isinstance(returncode, bool) or not isinstance(returncode, int):
            raise WorkerError("invalid_process_exit")
        receipt = None
        if (
            not isinstance(record.pid, bool)
            and isinstance(record.pid, int)
            and record.pid > 0
        ):
            receipt = ProcessExit(
                pid=record.pid,
                returncode=returncode,
                process_identity=record.identity,
            )
        record.stdout_collector.scrub()
        record.stderr_collector.scrub()
        with self._guard:
            if not record.reaped:
                record.process_exit = receipt
                record.reaped = True
                record.state = "exited"
                record.transport_credentials = None
                record.api_key = None
                record.nonce = None
                record.alias = None
        self._close_ownership(record.artifact_ownership)
        self._release_record_authority(record)
        return receipt

    def _close_ownership(self, ownership: RetainedArtifactOwnership) -> bool:
        try:
            ownership.close()
        except BaseException:
            with self._guard:
                self._retained_ownerships.add(ownership)
            return False
        with self._guard:
            self._retained_ownerships.discard(ownership)
        return True

    def _release_record_authority(self, record: _ProcessRecord) -> bool:
        with self._guard:
            if record.authority_released:
                return True
        try:
            self._authority.release(record.ticket)
        except BaseException:
            return False
        with self._guard:
            record.authority_released = True
        return True

    def _release_unspawned_ticket(
        self,
        ticket: object,
        record: _ProcessRecord | None,
    ) -> None:
        released = False
        try:
            self._authority.release(ticket)
            released = True
        except BaseException:
            pass
        with self._guard:
            if released:
                if record is not None:
                    record.authority_released = True
                self._ticket_records.pop(ticket, None)

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        thread_id = threading.get_ident()
        with self._guard:
            if self._loop is None:
                self._loop = loop
                self._loop_thread = thread_id
                return
            if self._loop is not loop or self._loop_thread != thread_id:
                raise WorkerError("worker_loop_mismatch")

    def _require_bound_thread(self) -> None:
        with self._guard:
            thread_id = self._loop_thread
        if thread_id is None or thread_id != threading.get_ident():
            raise WorkerError("worker_loop_mismatch")

    def _schedule_late_cleanup(
        self,
        pipeline: asyncio.Task[MacOSProcessHandle],
    ) -> None:
        cleanup = asyncio.create_task(self._cleanup_late_spawn(pipeline))
        self._retain_background_task(cleanup)

    async def _cleanup_late_spawn(
        self,
        pipeline: asyncio.Task[MacOSProcessHandle],
    ) -> ProcessExit | None:
        try:
            handle = await asyncio.shield(pipeline)
        except BaseException:
            return None
        record = self._record_for_control(
            handle,
            handle.process_identity,
            allow_exited=True,
        )
        if record.process_exit is not None:
            return record.process_exit
        try:
            self._signal_record(record, signal.SIGTERM)
        except WorkerError:
            return record.process_exit
        cleanup_deadline = float(self._clock()) + self._late_cleanup_seconds
        term_deadline = min(
            cleanup_deadline,
            float(self._clock()) + self._term_grace_seconds,
        )
        receipt = await self._wait_record_reaped(
            record,
            deadline=term_deadline,
        )
        if receipt is None:
            try:
                self._signal_record(record, signal.SIGKILL)
            except WorkerError:
                return record.process_exit
            receipt = await self._wait_record_reaped(
                record,
                deadline=cleanup_deadline,
            )
        return receipt

    async def _await_task(
        self,
        task: asyncio.Task[Any],
        *,
        deadline: float,
    ) -> object:
        remaining = max(0.0, float(deadline) - float(self._clock()))
        try:
            async with asyncio.timeout(remaining):
                return await asyncio.shield(task)
        except TimeoutError:
            return _EXPIRED

    def _retain_spawn_task(
        self,
        task: asyncio.Task[MacOSProcessHandle],
    ) -> None:
        with self._guard:
            self._spawn_tasks.add(task)

        def finished(done: asyncio.Task[MacOSProcessHandle]) -> None:
            with self._guard:
                self._spawn_tasks.discard(done)
            self._consume_task(done)

        task.add_done_callback(finished)

    def _retain_background_task(self, task: asyncio.Task[Any]) -> None:
        with self._guard:
            self._background_tasks.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            with self._guard:
                self._background_tasks.discard(done)
            self._consume_task(done)

        task.add_done_callback(finished)

    @staticmethod
    def _consume_task(task: asyncio.Task[Any]) -> None:
        try:
            task.result()
        except BaseException:
            pass

    def _live(self, record: _ProcessRecord) -> bool:
        try:
            return (
                not record.reaped
                and record.child is not None
                and self._process_is_live(record.child) is True
            )
        except BaseException:
            return False

    def _require_future_deadline(self, deadline: float) -> None:
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
            or float(deadline) <= float(self._clock())
        ):
            raise WorkerError("worker_deadline_elapsed")

    @staticmethod
    def _validate_argv(
        argv: Sequence[str],
        start_new_session: bool,
    ) -> tuple[str, ...]:
        try:
            normalized = tuple(argv)
        except TypeError:
            raise WorkerError("invalid_worker_argv") from None
        if (
            start_new_session is not True
            or not normalized
            or not isinstance(normalized[0], str)
            or not os.path.isabs(normalized[0])
            or any(
                not isinstance(argument, str)
                or not argument
                or "\x00" in argument
                for argument in normalized
            )
        ):
            raise WorkerError("invalid_worker_argv")
        return normalized

    @staticmethod
    def _port_from_argv(argv: tuple[str, ...]) -> int:
        try:
            index = argv.index("--port")
            value = int(argv[index + 1])
        except (ValueError, IndexError):
            raise WorkerError("invalid_worker_argv") from None
        if not 1 <= value <= 65_535:
            raise WorkerError("invalid_worker_argv")
        return value


__all__ = [
    "ArtifactIdentity",
    "BoundedRedactedOutput",
    "InterprocessWorkerLock",
    "MacOSDriverAuthority",
    "MacOSProcessHandle",
    "MacOSWorkerProcessDriver",
    "ReadinessChallenge",
    "ReadinessProof",
    "RetainedArtifactOwnership",
    "SignalGroupRequest",
    "SpawnChildClaim",
    "SpawnOwnership",
    "SpawnResult",
    "SpawnSpecification",
]
