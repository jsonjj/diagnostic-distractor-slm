"""Concrete, fail-closed macOS stdlib seams for the managed worker.

The higher-level lifecycle state machine lives in :mod:`macos_worker_driver`.
This module owns the small amount of operating-system authority needed by a
packaged macOS launcher.  Construction is side-effect free; process launch is
kept behind an explicit release gate.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import errno
import fcntl
import hashlib
import http.client
import json
import math
import os
import platform
import secrets
import signal
import stat
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from .llama_worker import ArtifactVerificationReceipt, WorkerError
from .macos_worker_driver import (
    ArtifactIdentity,
    BoundedRedactedOutput,
    MacOSDriverAuthority,
    MacOSWorkerProcessDriver,
    ReadinessChallenge,
    ReadinessProof,
    RetainedArtifactOwnership,
    SignalGroupRequest,
    SpawnResult,
    SpawnSpecification,
)


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
_READINESS_PROTOCOL_REVISION = "llama.cpp.openai.models.v1"
_DESCRIPTOR_RECEIPT_SCHEMA = "wayline.descriptor-binding-release-receipt.v1"
_HEX_DIGITS = frozenset("0123456789abcdef")


class DescriptorBindingReceiptError(ValueError):
    """A strict descriptor-binding receipt could not be authenticated."""


@dataclass(frozen=True, slots=True)
class DescriptorBindingReleaseReceipt:
    """Structured owner attestation for one exact local-launch integration.

    ``attest`` constructs this value in-process; it is an explicit owner
    decision, not a signature or cryptographic proof.  Production packaging
    must source every field from reviewed immutable build receipts.
    """

    binary_sha256: str
    model_sha256: str
    llama_cpp_revision: str
    os_name: str
    architecture: str
    readiness_protocol_revision: str
    spawn_adapter_sha256: str

    def __post_init__(self) -> None:
        for name in ("binary_sha256", "model_sha256", "spawn_adapter_sha256"):
            self._require_hex(name, getattr(self, name), 64)
        self._require_hex("llama_cpp_revision", self.llama_cpp_revision, 40)
        for name in ("os_name", "architecture", "readiness_protocol_revision"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 128
                or "\x00" in value
                or value != value.strip()
            ):
                raise ValueError(f"{name} must be bounded non-empty text")

    @classmethod
    def attest(
        cls,
        *,
        binary_sha256: str,
        model_sha256: str,
        llama_cpp_revision: str,
        os_name: str,
        architecture: str,
        readiness_protocol_revision: str,
        spawn_adapter_sha256: str,
    ) -> DescriptorBindingReleaseReceipt:
        return cls(
            binary_sha256=binary_sha256,
            model_sha256=model_sha256,
            llama_cpp_revision=llama_cpp_revision,
            os_name=os_name,
            architecture=architecture,
            readiness_protocol_revision=readiness_protocol_revision,
            spawn_adapter_sha256=spawn_adapter_sha256,
        )

    def __repr__(self) -> str:
        return (
            "DescriptorBindingReleaseReceipt("
            f"platform={self.os_name!r}/{self.architecture!r}, "
            f"llama_cpp_revision={self.llama_cpp_revision[:12]!r}, "
            "owner_attested=True)"
        )

    def to_json(self) -> str:
        """Return the sole canonical serialized release receipt."""

        return json.dumps(
            {
                "architecture": self.architecture,
                "binarySha256": self.binary_sha256,
                "llamaCppRevision": self.llama_cpp_revision,
                "modelSha256": self.model_sha256,
                "osName": self.os_name,
                "readinessProtocolRevision": self.readiness_protocol_revision,
                "schemaVersion": _DESCRIPTOR_RECEIPT_SCHEMA,
                "spawnAdapterSha256": self.spawn_adapter_sha256,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def matches_environment(self, spawn_adapter_sha256: object) -> bool:
        return bool(
            isinstance(spawn_adapter_sha256, str)
            and self.spawn_adapter_sha256 == spawn_adapter_sha256
            and self.os_name == platform.system()
            and self.architecture == platform.machine()
            and self.readiness_protocol_revision == _READINESS_PROTOCOL_REVISION
        )

    def matches_artifacts(self, receipt: object) -> bool:
        return bool(
            isinstance(receipt, ArtifactVerificationReceipt)
            and self.binary_sha256 == receipt.binary_sha256
            and self.model_sha256 == receipt.model_sha256
        )

    @staticmethod
    def _require_hex(name: str, value: object, length: int) -> None:
        if (
            not isinstance(value, str)
            or len(value) != length
            or any(character not in _HEX_DIGITS for character in value)
        ):
            raise ValueError(f"{name} must be a lowercase {length}-hex receipt")


def parse_descriptor_binding_release_receipt(
    payload: str | bytes | bytearray,
) -> DescriptorBindingReleaseReceipt:
    """Parse one canonical Mac-arm64 descriptor-binding owner attestation."""

    duplicate = False

    def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal duplicate
        decoded: dict[str, object] = {}
        for key, value in pairs:
            if key in decoded:
                duplicate = True
            decoded[key] = value
        return decoded

    def reject_constant(_value: str) -> None:
        raise DescriptorBindingReceiptError("receipt_json_invalid")

    try:
        raw = (
            bytes(payload).decode("utf-8")
            if isinstance(payload, (bytes, bytearray))
            else payload
        )
        if not isinstance(raw, str):
            raise DescriptorBindingReceiptError("receipt_json_invalid")
        decoded = json.loads(
            raw,
            object_pairs_hook=strict_object,
            parse_constant=reject_constant,
        )
    except DescriptorBindingReceiptError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise DescriptorBindingReceiptError("receipt_json_invalid") from None

    required = {
        "architecture",
        "binarySha256",
        "llamaCppRevision",
        "modelSha256",
        "osName",
        "readinessProtocolRevision",
        "schemaVersion",
        "spawnAdapterSha256",
    }
    if (
        duplicate
        or not isinstance(decoded, dict)
        or set(decoded) != required
        or decoded.get("schemaVersion") != _DESCRIPTOR_RECEIPT_SCHEMA
        or decoded.get("osName") != "Darwin"
        or decoded.get("architecture") != "arm64"
        or decoded.get("readinessProtocolRevision")
        != _READINESS_PROTOCOL_REVISION
    ):
        raise DescriptorBindingReceiptError("receipt_contract_invalid")
    try:
        receipt = DescriptorBindingReleaseReceipt.attest(
            binary_sha256=decoded["binarySha256"],
            model_sha256=decoded["modelSha256"],
            llama_cpp_revision=decoded["llamaCppRevision"],
            os_name=decoded["osName"],
            architecture=decoded["architecture"],
            readiness_protocol_revision=decoded[
                "readinessProtocolRevision"
            ],
            spawn_adapter_sha256=decoded["spawnAdapterSha256"],
        )
    except (TypeError, ValueError):
        raise DescriptorBindingReceiptError("receipt_contract_invalid") from None
    if raw != receipt.to_json():
        raise DescriptorBindingReceiptError("receipt_not_canonical")
    return receipt


class _RetainedDescriptor:
    """Opaque, close-once descriptor identity shared only with the spawner."""

    __slots__ = ("_descriptor", "_guard")

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor
        self._guard = threading.Lock()

    def __repr__(self) -> str:
        return "<RetainedArtifactDescriptor>"

    def fileno(self) -> int:
        with self._guard:
            return self._descriptor

    def close(self) -> None:
        with self._guard:
            if self._descriptor < 0:
                return
            descriptor = self._descriptor
            os.close(descriptor)
            self._descriptor = -1


class DescriptorArtifactRevalidator:
    """Reopen, hash, and retain two artifacts through symlink-free walks."""

    __slots__ = (
        "_background_failure",
        "_background_tasks",
        "_binary_root",
        "_clock",
        "_expected_uid",
        "_guard",
        "_model_root",
        "_release_receipt",
        "_spawn_adapter_sha256",
    )

    def __init__(
        self,
        *,
        binary_root: str,
        model_root: str,
        release_receipt: DescriptorBindingReleaseReceipt | None = None,
        spawn_adapter_sha256: str | None = None,
        expected_uid: int | None = None,
        clock=time.monotonic,
    ) -> None:
        self._binary_root = self._validate_root("binary_root", binary_root)
        self._model_root = self._validate_root("model_root", model_root)
        if release_receipt is not None and not isinstance(
            release_receipt,
            DescriptorBindingReleaseReceipt,
        ):
            raise ValueError("release_receipt must be structured or None")
        if spawn_adapter_sha256 is not None:
            DescriptorBindingReleaseReceipt._require_hex(
                "spawn_adapter_sha256",
                spawn_adapter_sha256,
                64,
            )
        if expected_uid is None:
            expected_uid = os.getuid()
        if (
            isinstance(expected_uid, bool)
            or not isinstance(expected_uid, int)
            or expected_uid < 0
        ):
            raise ValueError("expected_uid must be a non-negative integer")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._release_receipt = release_receipt
        self._spawn_adapter_sha256 = spawn_adapter_sha256
        self._expected_uid = expected_uid
        self._clock = clock
        self._guard = threading.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._background_failure = False

    def __repr__(self) -> str:
        return (
            "DescriptorArtifactRevalidator(roots=<private>, "
            f"descriptor_binding_supported={self.descriptor_binding_supported})"
        )

    @property
    def release_receipt(self) -> DescriptorBindingReleaseReceipt | None:
        return self._release_receipt

    @property
    def descriptor_binding_supported(self) -> bool:
        return bool(
            self._release_receipt is not None
            and self._release_receipt.matches_environment(
                self._spawn_adapter_sha256
            )
        )

    async def __call__(
        self,
        receipt: ArtifactVerificationReceipt,
        *,
        deadline: float,
    ) -> RetainedArtifactOwnership:
        if not isinstance(receipt, ArtifactVerificationReceipt):
            raise WorkerError("artifact_revalidation_failed")
        if (
            not self.descriptor_binding_supported
            or self._release_receipt is None
            or not self._release_receipt.matches_artifacts(receipt)
        ):
            raise WorkerError("descriptor_binding_unavailable")
        self._require_deadline(deadline)
        worker = asyncio.create_task(
            asyncio.to_thread(self._revalidate, receipt, float(deadline))
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(self._close_late_result(worker))
            self._retain_background(cleanup)
            raise
        except WorkerError:
            raise
        except BaseException:
            raise WorkerError("artifact_revalidation_failed") from None

    async def wait_for_background_cleanup(self) -> None:
        while True:
            with self._guard:
                background_failure = self._background_failure
                tasks = tuple(self._background_tasks)
            if background_failure:
                raise WorkerError("worker_unsafe_state")
            if not tasks:
                return
            results = await asyncio.gather(*tasks, return_exceptions=True)
            if any(isinstance(result, BaseException) for result in results):
                with self._guard:
                    self._background_failure = True

    async def _close_late_result(
        self,
        worker: asyncio.Task[RetainedArtifactOwnership],
    ) -> None:
        try:
            ownership = await asyncio.shield(worker)
        except BaseException:
            return
        ownership.close()

    def _retain_background(self, task: asyncio.Task[Any]) -> None:
        with self._guard:
            self._background_tasks.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            failed = False
            try:
                done.result()
            except BaseException:
                failed = True
            with self._guard:
                if failed:
                    self._background_failure = True
                self._background_tasks.discard(done)

        task.add_done_callback(finished)

    @staticmethod
    def _validate_root(name: str, root: str) -> str:
        if (
            not isinstance(root, str)
            or not root
            or "\x00" in root
            or not os.path.isabs(root)
            or os.path.normpath(root) != root
            or root == os.path.sep
        ):
            raise ValueError(f"{name} must be a normalized absolute directory")
        return root

    def _require_deadline(self, deadline: float) -> None:
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
            or float(deadline) <= float(self._clock())
        ):
            raise WorkerError("worker_deadline_elapsed")

    def _revalidate(
        self,
        receipt: ArtifactVerificationReceipt,
        deadline: float,
    ) -> RetainedArtifactOwnership:
        binary_descriptor: int | None = None
        model_descriptor: int | None = None
        try:
            self._validate_receipt_facts(receipt)
            binary_descriptor = self._open_artifact(
                self._binary_root,
                receipt.binary_path,
            )
            binary = self._identity(
                binary_descriptor,
                path=receipt.binary_path,
                deadline=deadline,
            )
            self._match_identity(binary, receipt, prefix="binary")
            model_descriptor = self._open_artifact(
                self._model_root,
                receipt.model_path,
            )
            model = self._identity(
                model_descriptor,
                path=receipt.model_path,
                deadline=deadline,
            )
            self._match_identity(model, receipt, prefix="model")
            if (binary.device, binary.inode) == (model.device, model.inode):
                raise WorkerError("artifact_revalidation_failed")

            retained_binary = _RetainedDescriptor(binary_descriptor)
            retained_model = _RetainedDescriptor(model_descriptor)
            binary_descriptor = None
            model_descriptor = None

            def close_descriptors() -> None:
                first_error: BaseException | None = None
                for retained in (retained_binary, retained_model):
                    try:
                        retained.close()
                    except BaseException as error:
                        if first_error is None:
                            first_error = error
                if first_error is not None:
                    raise WorkerError("artifact_revalidation_failed") from None

            return RetainedArtifactOwnership(
                receipt=receipt,
                binary=binary,
                model=model,
                descriptor_binding_supported=self.descriptor_binding_supported,
                descriptor_identities=(retained_binary, retained_model),
                close_callback=close_descriptors,
            )
        except WorkerError:
            raise
        except BaseException:
            raise WorkerError("artifact_revalidation_failed") from None
        finally:
            for descriptor in (binary_descriptor, model_descriptor):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    @staticmethod
    def _validate_receipt_facts(receipt: ArtifactVerificationReceipt) -> None:
        for name in ("binary_sha256", "model_sha256"):
            value = getattr(receipt, name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise WorkerError("artifact_revalidation_failed")
        for name in (
            "binary_size",
            "model_size",
            "binary_device",
            "binary_inode",
            "model_device",
            "model_inode",
        ):
            value = getattr(receipt, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WorkerError("artifact_revalidation_failed")

    def _open_artifact(self, root: str, path: str) -> int:
        relative = self._relative_components(root, path)
        directory = self._open_absolute_directory(root)
        try:
            self._validate_directory(directory)
            for component in relative[:-1]:
                next_directory = os.open(
                    component,
                    self._directory_flags(),
                    dir_fd=directory,
                )
                os.close(directory)
                directory = next_directory
                self._validate_directory(directory)
            flags = os.O_RDONLY
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            return os.open(relative[-1], flags, dir_fd=directory)
        finally:
            os.close(directory)

    @staticmethod
    def _relative_components(root: str, path: str) -> tuple[str, ...]:
        if (
            not isinstance(path, str)
            or not path
            or "\x00" in path
            or not os.path.isabs(path)
            or os.path.normpath(path) != path
        ):
            raise WorkerError("artifact_revalidation_failed")
        try:
            if os.path.commonpath((root, path)) != root:
                raise WorkerError("artifact_revalidation_failed")
        except ValueError:
            raise WorkerError("artifact_revalidation_failed") from None
        relative = os.path.relpath(path, root)
        components = tuple(relative.split(os.path.sep))
        if (
            relative in ("", ".")
            or not components
            or any(component in ("", ".", "..") for component in components)
        ):
            raise WorkerError("artifact_revalidation_failed")
        return components

    @classmethod
    def _open_absolute_directory(cls, path: str) -> int:
        descriptor = os.open(os.path.sep, cls._directory_flags())
        try:
            for component in path.split(os.path.sep)[1:]:
                if not component:
                    continue
                next_descriptor = os.open(
                    component,
                    cls._directory_flags(),
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = next_descriptor
            result = descriptor
            descriptor = -1
            return result
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _directory_flags() -> int:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        return flags

    def _validate_directory(self, descriptor: int) -> None:
        facts = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(facts.st_mode)
            or facts.st_uid != self._expected_uid
            or facts.st_mode & 0o022
        ):
            raise WorkerError("artifact_revalidation_failed")

    def _identity(
        self,
        descriptor: int,
        *,
        path: str,
        deadline: float,
    ) -> ArtifactIdentity:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != self._expected_uid
            or before.st_mode & 0o022
        ):
            raise WorkerError("artifact_revalidation_failed")
        digest = hashlib.sha256()
        offset = 0
        while True:
            if float(self._clock()) >= deadline:
                raise WorkerError("worker_deadline_elapsed")
            chunk = os.pread(descriptor, 1024 * 1024, offset)
            if not chunk:
                break
            digest.update(chunk)
            offset += len(chunk)
        after = os.fstat(descriptor)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(before, name) != getattr(after, name) for name in stable_fields):
            raise WorkerError("artifact_revalidation_failed")
        return ArtifactIdentity(
            path=path,
            sha256=digest.hexdigest(),
            size=after.st_size,
            device=after.st_dev,
            inode=after.st_ino,
        )

    @staticmethod
    def _match_identity(
        identity: ArtifactIdentity,
        receipt: ArtifactVerificationReceipt,
        *,
        prefix: str,
    ) -> None:
        expected = ArtifactIdentity(
            path=getattr(receipt, f"{prefix}_path"),
            sha256=getattr(receipt, f"{prefix}_sha256"),
            size=getattr(receipt, f"{prefix}_size"),
            device=getattr(receipt, f"{prefix}_device"),
            inode=getattr(receipt, f"{prefix}_inode"),
        )
        if identity != expected:
            raise WorkerError("artifact_revalidation_failed")


class _ExactPopenChild:
    __slots__ = ("_authority_token",)

    def __init__(self, authority_token: object) -> None:
        self._authority_token = authority_token

    def __repr__(self) -> str:
        return "<ExactManagedPopenChild>"


class _PopenRecord:
    __slots__ = (
        "child",
        "group_identity",
        "new_session",
        "pgid",
        "pid",
        "raw_child",
        "reap_task",
        "reaped",
        "returncode",
    )

    def __init__(
        self,
        *,
        child: _ExactPopenChild,
        raw_child: object,
        new_session: bool,
    ) -> None:
        self.child = child
        self.raw_child = raw_child
        self.new_session = new_session
        self.pid: int | None = None
        self.pgid: int | None = None
        self.group_identity: object | None = None
        self.reap_task: asyncio.Task[int] | None = None
        self.reaped = False
        self.returncode: int | None = None


def _stdlib_nonreaping_exit_check(pid: int) -> bool:
    """Return true only when waitid observes exit without consuming status."""

    required = ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "waitid")
    if any(not hasattr(os, name) for name in required):
        # Exact liveness cannot be attested on a platform without WNOWAIT.
        return True
    try:
        result = os.waitid(
            os.P_PID,
            pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except (ChildProcessError, OSError):
        return True
    return result is not None


class PopenProcessAuthority:
    """Registry binding opaque child handles to exact ``Popen`` instances.

    Bound post-create failures signal the re-attested process group so child
    descendants are included.  A pinned macOS llama-server release still
    requires a live descendant/process-group smoke test before enablement.
    """

    __slots__ = (
        "_authority_token",
        "_getpgid",
        "_guard",
        "_killpg",
        "_nonreaping_exit_check",
        "_records",
    )

    def __init__(
        self,
        *,
        getpgid: Callable[[int], int] = os.getpgid,
        nonreaping_exit_check: Callable[[int], bool] = _stdlib_nonreaping_exit_check,
        killpg: Callable[[int, int], Any] = os.killpg,
    ) -> None:
        if (
            not callable(getpgid)
            or not callable(nonreaping_exit_check)
            or not callable(killpg)
        ):
            raise ValueError("process authority callbacks must be callable")
        self._authority_token = object()
        self._getpgid = getpgid
        self._nonreaping_exit_check = nonreaping_exit_check
        self._killpg = killpg
        self._guard = threading.RLock()
        self._records: dict[_ExactPopenChild, _PopenRecord] = {}

    def __repr__(self) -> str:
        return "PopenProcessAuthority(<private registry>)"

    def register(self, raw_child: object, *, new_session: bool) -> _ExactPopenChild:
        if raw_child is None or new_session is not True:
            raise WorkerError("invalid_worker_process")
        child = _ExactPopenChild(self._authority_token)
        record = _PopenRecord(
            child=child,
            raw_child=raw_child,
            new_session=True,
        )
        with self._guard:
            self._records[child] = record
        return child

    def bind(
        self,
        child: object,
        raw_child: object,
        *,
        pid: object,
        pgid: object,
    ) -> None:
        self.bind_pid(child, raw_child, pid=pid)
        self.bind_group(child, raw_child, pgid=pgid)

    def bind_pid(
        self,
        child: object,
        raw_child: object,
        *,
        pid: object,
    ) -> None:
        with self._guard:
            record = self._record_locked(child)
            if (
                record.raw_child is not raw_child
                or record.pid is not None
                or isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 0
                or record.new_session is not True
            ):
                raise WorkerError("invalid_worker_process")
            record.pid = pid

    def bind_group(
        self,
        child: object,
        raw_child: object,
        *,
        pgid: object,
    ) -> None:
        with self._guard:
            record = self._record_locked(child)
            if (
                record.raw_child is not raw_child
                or record.pid is None
                or record.pgid is not None
                or isinstance(pgid, bool)
                or not isinstance(pgid, int)
                or pgid != record.pid
                or record.new_session is not True
            ):
                raise WorkerError("invalid_worker_process")
            record.pgid = pgid

    def has_bound_pid(self, child: object, pid: object) -> bool:
        with self._guard:
            try:
                record = self._record_locked(child)
            except WorkerError:
                return False
            return record.pid == pid and record.raw_child is not None

    def owns_exact_child(self, child: object, raw_child: object) -> bool:
        with self._guard:
            try:
                record = self._record_locked(child)
            except WorkerError:
                return False
            return record.raw_child is raw_child

    def is_live(self, child: object) -> bool:
        with self._guard:
            try:
                record = self._record_locked(child)
                return self._is_live_locked(record)
            except BaseException:
                return False

    async def reap(self, child: object) -> int:
        with self._guard:
            record = self._record_locked(child)
            if record.reaped:
                assert isinstance(record.returncode, int)
                return record.returncode
            task = record.reap_task
            if task is None:
                task = asyncio.create_task(
                    asyncio.to_thread(self._wait_and_publish, record)
                )
                record.reap_task = task
                task.add_done_callback(self._consume_reap_task)
        return await asyncio.shield(task)

    async def cleanup_failed_spawn(
        self,
        child: object,
        *,
        timeout: float,
    ) -> int:
        if not math.isfinite(timeout) or timeout <= 0:
            raise WorkerError("worker_unsafe_state")
        with self._guard:
            record = self._record_locked(child)
            if record.reaped:
                assert isinstance(record.returncode, int)
                return record.returncode
            if record.reap_task is not None:
                raise WorkerError("worker_unsafe_state")
            raw_child = record.raw_child
        if raw_child is None:
            raise WorkerError("worker_unsafe_state")
        try:
            async with asyncio.timeout(timeout * 3.0):
                return await asyncio.to_thread(
                    self._cleanup_failed_sync,
                    record,
                    raw_child,
                    timeout,
                )
        except WorkerError:
            raise
        except BaseException:
            raise WorkerError("worker_unsafe_state") from None

    def signal_exact(
        self,
        request: SignalGroupRequest,
        *,
        getpgid: Callable[[int], int],
        killpg: Callable[[int, int], Any],
    ) -> None:
        if not isinstance(request, SignalGroupRequest):
            raise WorkerError("worker_unsafe_state")
        if request.signum not in (signal.SIGTERM, signal.SIGKILL):
            raise WorkerError("worker_unsafe_state")
        if request.group_identity is None or isinstance(
            request.group_identity,
            _PRIMITIVE_IDENTITIES,
        ):
            raise WorkerError("worker_unsafe_state")
        with self._guard:
            record = self._record_locked(request.child)
            if (
                record.reaped
                or record.new_session is not True
                or record.pid != request.pid
                or record.pgid != request.pgid
                or request.pgid != request.pid
            ):
                raise WorkerError("worker_unsafe_state")
            if record.group_identity is None:
                record.group_identity = request.group_identity
            elif record.group_identity is not request.group_identity:
                raise WorkerError("worker_unsafe_state")
            try:
                raw_pid = record.raw_child.pid
                live_group = getpgid(request.pid)
                exited = self._nonreaping_exit_check(request.pid)
            except BaseException:
                raise WorkerError("worker_unsafe_state") from None
            if (
                isinstance(raw_pid, bool)
                or raw_pid != request.pid
                or live_group != request.pid
                or exited is not False
                or getattr(record.raw_child, "returncode", None) is not None
            ):
                raise WorkerError("worker_unsafe_state")
            try:
                killpg(request.pgid, request.signum)
            except BaseException:
                raise WorkerError("worker_unsafe_state") from None

    def _record_locked(self, child: object) -> _PopenRecord:
        if (
            not isinstance(child, _ExactPopenChild)
            or child._authority_token is not self._authority_token
        ):
            raise WorkerError("stale_process_handle")
        record = self._records.get(child)
        if record is None or record.child is not child:
            raise WorkerError("stale_process_handle")
        return record

    def _is_live_locked(self, record: _PopenRecord) -> bool:
        if (
            record.reaped
            or record.raw_child is None
            or record.pid is None
            or record.pgid != record.pid
            or record.new_session is not True
            or getattr(record.raw_child, "returncode", None) is not None
        ):
            return False
        try:
            if self._nonreaping_exit_check(record.pid) is not False:
                return False
            return self._getpgid(record.pid) == record.pid
        except BaseException:
            return False

    def _wait_and_publish(self, record: _PopenRecord) -> int:
        with self._guard:
            raw_child = record.raw_child
        if raw_child is None:
            raise WorkerError("invalid_process_exit")
        try:
            returncode = raw_child.wait()
        except BaseException:
            with self._guard:
                record.reap_task = None
            raise WorkerError("invalid_process_exit") from None
        if isinstance(returncode, bool) or not isinstance(returncode, int):
            with self._guard:
                record.reap_task = None
            raise WorkerError("invalid_process_exit")
        with self._guard:
            if record.raw_child is not raw_child:
                raise WorkerError("invalid_process_exit")
            record.returncode = returncode
            record.reaped = True
            record.raw_child = None
        return returncode

    def _cleanup_failed_sync(
        self,
        record: _PopenRecord,
        raw_child: object,
        timeout: float,
    ) -> int:
        try:
            self._signal_failed_spawn(record, raw_child, signal.SIGTERM)
        except WorkerError:
            # The child may have exited between binding and signalling.  Only
            # exact wait/reap below can distinguish that safe race from an
            # unsafe reused or unverifiable group.
            pass
        try:
            returncode = raw_child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                self._signal_failed_spawn(record, raw_child, signal.SIGKILL)
            except WorkerError:
                pass
            try:
                returncode = raw_child.wait(timeout=timeout)
            except BaseException:
                raise WorkerError("worker_unsafe_state") from None
        except BaseException:
            try:
                try:
                    self._signal_failed_spawn(record, raw_child, signal.SIGKILL)
                except WorkerError:
                    pass
                returncode = raw_child.wait(timeout=timeout)
            except BaseException:
                raise WorkerError("worker_unsafe_state") from None
        if isinstance(returncode, bool) or not isinstance(returncode, int):
            raise WorkerError("invalid_process_exit")
        with self._guard:
            if record.raw_child is not raw_child:
                raise WorkerError("worker_unsafe_state")
            record.returncode = returncode
            record.reaped = True
            record.raw_child = None
        for stream_name in ("stdout", "stderr"):
            try:
                stream = getattr(raw_child, stream_name)
                if stream is not None:
                    stream.close()
            except BaseException:
                pass
        return returncode

    def _signal_failed_spawn(
        self,
        record: _PopenRecord,
        raw_child: object,
        signum: int,
    ) -> None:
        with self._guard:
            if record.raw_child is not raw_child or record.reaped:
                raise WorkerError("worker_unsafe_state")
            if record.pgid is None:
                callback = (
                    raw_child.terminate
                    if signum == signal.SIGTERM
                    else raw_child.kill
                )
                try:
                    callback()
                except BaseException:
                    pass
                return
            if (
                record.new_session is not True
                or isinstance(record.pid, bool)
                or not isinstance(record.pid, int)
                or record.pid <= 0
                or record.pgid != record.pid
            ):
                raise WorkerError("worker_unsafe_state")
            try:
                raw_pid = raw_child.pid
                live_group = self._getpgid(record.pid)
                exited = self._nonreaping_exit_check(record.pid)
            except BaseException:
                raise WorkerError("worker_unsafe_state") from None
            if (
                isinstance(raw_pid, bool)
                or raw_pid != record.pid
                or live_group != record.pid
                or exited is not False
                or getattr(raw_child, "returncode", None) is not None
            ):
                raise WorkerError("worker_unsafe_state")
            try:
                self._killpg(record.pgid, signum)
            except BaseException:
                raise WorkerError("worker_unsafe_state") from None

    @staticmethod
    def _consume_reap_task(task: asyncio.Task[int]) -> None:
        try:
            task.result()
        except BaseException:
            pass


class PopenLlamaSpawner:
    """Create one descriptor-bound child and immediately publish ownership."""

    __slots__ = (
        "_allowed_cwd",
        "_background_failure",
        "_background_guard",
        "_background_tasks",
        "_cleanup_timeout",
        "_drain_task_factory",
        "_getpgid",
        "_popen_factory",
        "_process_authority",
        "_release_receipt",
        "_spawn_adapter_sha256",
    )

    def __init__(
        self,
        *,
        process_authority: PopenProcessAuthority,
        popen_factory: Callable[..., object] = subprocess.Popen,
        release_receipt: DescriptorBindingReleaseReceipt | None = None,
        allowed_cwd: str,
        getpgid: Callable[[int], int] = os.getpgid,
        drain_task_factory: Callable[[Any], asyncio.Task[Any]] = asyncio.create_task,
        cleanup_timeout: float = 0.25,
    ) -> None:
        if not isinstance(process_authority, PopenProcessAuthority):
            raise ValueError("process_authority must be a PopenProcessAuthority")
        if (
            not callable(popen_factory)
            or not callable(getpgid)
            or not callable(drain_task_factory)
        ):
            raise ValueError("spawner callbacks must be callable")
        if not math.isfinite(cleanup_timeout) or cleanup_timeout <= 0:
            raise ValueError("cleanup_timeout must be positive and finite")
        if (
            not isinstance(allowed_cwd, str)
            or not os.path.isabs(allowed_cwd)
            or "\x00" in allowed_cwd
        ):
            raise ValueError("allowed_cwd must be an absolute path")
        if release_receipt is not None and not isinstance(
            release_receipt,
            DescriptorBindingReleaseReceipt,
        ):
            raise ValueError("release_receipt must be structured or None")
        spawn_adapter_sha256 = getattr(
            popen_factory,
            "wayline_spawn_adapter_sha256",
            None,
        )
        if spawn_adapter_sha256 is not None:
            DescriptorBindingReleaseReceipt._require_hex(
                "wayline_spawn_adapter_sha256",
                spawn_adapter_sha256,
                64,
            )
        self._process_authority = process_authority
        self._popen_factory = popen_factory
        self._release_receipt = release_receipt
        self._spawn_adapter_sha256 = spawn_adapter_sha256
        self._allowed_cwd = allowed_cwd
        self._getpgid = getpgid
        self._drain_task_factory = drain_task_factory
        self._cleanup_timeout = float(cleanup_timeout)
        self._background_guard = threading.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._background_failure = False

    def __repr__(self) -> str:
        state = "passed" if self.descriptor_binding_supported else "blocked"
        return f"PopenLlamaSpawner(release_gate={state})"

    @property
    def release_receipt(self) -> DescriptorBindingReleaseReceipt | None:
        return self._release_receipt

    @property
    def spawn_adapter_sha256(self) -> str | None:
        return self._spawn_adapter_sha256

    @property
    def descriptor_binding_supported(self) -> bool:
        return bool(
            self._release_receipt is not None
            and self._release_receipt.matches_environment(
                self._spawn_adapter_sha256
            )
            and (
                getattr(self._popen_factory, "wayline_no_child_on_raise", False)
                is True
                or getattr(
                    self._popen_factory,
                    "wayline_child_created_callback",
                    False,
                )
                is True
            )
        )

    async def __call__(self, specification: SpawnSpecification) -> SpawnResult:
        if not isinstance(specification, SpawnSpecification):
            raise WorkerError("invalid_worker_process")
        if not self.descriptor_binding_supported:
            raise WorkerError("descriptor_binding_unavailable")
        effective_base, artifact_fds = self._effective_argv(specification)
        key_descriptor = self._api_key_descriptor(
            specification.readiness_api_key
        )
        effective_argv = (
            *effective_base,
            "--api-key-file",
            f"/dev/fd/{key_descriptor}",
            "--alias",
            specification.readiness_alias,
        )
        pass_fds = (*artifact_fds, key_descriptor)
        try:
            specification.spawn_ownership.bind_executed_argv(effective_argv)
        except BaseException:
            os.close(key_descriptor)
            raise
        kwargs = {
            "executable": effective_argv[0],
            "shell": False,
            "start_new_session": True,
            "stdin": specification.stdin,
            "stdout": specification.stdout,
            "stderr": specification.stderr,
            "close_fds": True,
            "cwd": specification.cwd,
            "env": dict(specification.env),
            "pass_fds": pass_fds,
            "bufsize": 0,
        }

        child: _ExactPopenChild | None = None
        child_claim = None
        raw_child: object | None = None
        stdout_drain: asyncio.Task[Any] | None = None
        stderr_drain: asyncio.Task[Any] | None = None
        late_cleanup_scheduled = False

        def created_callback(created: object) -> None:
            nonlocal child, child_claim, raw_child
            if raw_child is not None or child is not None or child_claim is not None:
                raise WorkerError("worker_unsafe_state")
            raw_child = created
            child = self._process_authority.register(
                created,
                new_session=True,
            )
            child_claim = specification.spawn_ownership.claim_child(child)

        callback_contract = (
            getattr(
                self._popen_factory,
                "wayline_child_created_callback",
                False,
            )
            is True
        )

        def create_process() -> object:
            if callback_contract:
                returned_child = self._popen_factory(
                    effective_argv,
                    wayline_child_created=created_callback,
                    **kwargs,
                )
                if raw_child is None or returned_child is not raw_child:
                    raise WorkerError("invalid_worker_process")
                return returned_child
            returned_child = self._popen_factory(effective_argv, **kwargs)
            created_callback(returned_child)
            return returned_child

        try:
            creation_task = asyncio.create_task(asyncio.to_thread(create_process))
            try:
                returned = await asyncio.shield(creation_task)
            except asyncio.CancelledError:
                late_key_descriptor = key_descriptor
                key_descriptor = -1
                cleanup = asyncio.create_task(
                    self._cleanup_cancelled_creation(
                        creation_task,
                        lambda: child,
                        late_key_descriptor,
                    )
                )
                self._retain_background(cleanup)
                late_cleanup_scheduled = True
                raise
            os.close(key_descriptor)
            key_descriptor = -1
            if returned is not raw_child:
                raise WorkerError("invalid_worker_process")
            assert raw_child is not None and child is not None and child_claim is not None
            pid = raw_child.pid
            self._process_authority.bind_pid(
                child,
                raw_child,
                pid=pid,
            )
            pgid = self._getpgid(pid)
            self._process_authority.bind_group(
                child,
                raw_child,
                pgid=pgid,
            )
            stdout = getattr(raw_child, "stdout")
            stderr = getattr(raw_child, "stderr")
            if stdout is None or stderr is None:
                raise WorkerError("invalid_worker_process")
            stdout_coroutine = self._drain(
                stdout,
                specification.stdout_collector,
            )
            try:
                stdout_drain = self._drain_task_factory(stdout_coroutine)
            except BaseException:
                stdout_coroutine.close()
                raise
            stderr_coroutine = self._drain(
                stderr,
                specification.stderr_collector,
            )
            try:
                stderr_drain = self._drain_task_factory(stderr_coroutine)
            except BaseException:
                stderr_coroutine.close()
                raise
            if not isinstance(stdout_drain, asyncio.Task) or not isinstance(
                stderr_drain,
                asyncio.Task,
            ):
                raise WorkerError("invalid_worker_process")
            return specification.spawn_ownership.complete(
                child_claim,
                pid=pid,
                pgid=pgid,
                stdout_drain=stdout_drain,
                stderr_drain=stderr_drain,
            )
        except asyncio.CancelledError:
            if child is not None and not late_cleanup_scheduled:
                await self._cleanup_post_create(
                    child,
                    stdout_drain,
                    stderr_drain,
                )
            raise
        except BaseException as error:
            if child is not None:
                await self._cleanup_post_create(
                    child,
                    stdout_drain,
                    stderr_drain,
                )
            if isinstance(error, WorkerError):
                raise
            raise WorkerError("invalid_worker_process") from None
        finally:
            if key_descriptor >= 0:
                try:
                    os.close(key_descriptor)
                except OSError:
                    pass

    async def wait_for_background_cleanup(self) -> None:
        while True:
            with self._background_guard:
                background_failure = self._background_failure
                tasks = tuple(self._background_tasks)
            if background_failure:
                raise WorkerError("worker_unsafe_state")
            if not tasks:
                return
            results = await asyncio.gather(*tasks, return_exceptions=True)
            if any(isinstance(result, BaseException) for result in results):
                with self._background_guard:
                    self._background_failure = True

    async def _cleanup_cancelled_creation(
        self,
        creation_task: asyncio.Task[object],
        child_factory: Callable[[], object | None],
        key_descriptor: int,
    ) -> None:
        try:
            try:
                await asyncio.shield(creation_task)
            except BaseException:
                pass
        finally:
            try:
                os.close(key_descriptor)
            except OSError:
                pass
        child = child_factory()
        if child is not None:
            await self._cleanup_post_create(child, None, None)

    def _retain_background(self, task: asyncio.Task[Any]) -> None:
        with self._background_guard:
            self._background_tasks.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            failed = False
            try:
                done.result()
            except BaseException:
                failed = True
            with self._background_guard:
                if failed:
                    self._background_failure = True
                self._background_tasks.discard(done)

        task.add_done_callback(finished)

    def _effective_argv(
        self,
        specification: SpawnSpecification,
    ) -> tuple[tuple[str, ...], tuple[int, int]]:
        ownership = specification.artifact_ownership
        if (
            self._release_receipt is None
            or not self._release_receipt.matches_artifacts(ownership.receipt)
            or ownership.closed
            or ownership.descriptor_binding_supported is not True
            or specification.shell is not False
            or specification.start_new_session is not True
            or specification.close_fds is not True
            or specification.stdin != subprocess.DEVNULL
            or specification.stdout != subprocess.PIPE
            or specification.stderr != subprocess.PIPE
            or specification.cwd != self._allowed_cwd
            or specification.executable != ownership.receipt.binary_path
            or not isinstance(specification.env, Mapping)
            or set(specification.env).difference(_ALLOWED_ENVIRONMENT)
            or not isinstance(
                specification.stdout_collector,
                BoundedRedactedOutput,
            )
            or not isinstance(
                specification.stderr_collector,
                BoundedRedactedOutput,
            )
        ):
            raise WorkerError("descriptor_binding_unavailable")
        self._validate_environment(specification.env)
        try:
            logical = tuple(specification.argv)
        except TypeError:
            raise WorkerError("invalid_worker_argv") from None
        if (
            not logical
            or logical[0] != ownership.receipt.binary_path
            or any(
                not isinstance(argument, str)
                or not argument
                or "\x00" in argument
                for argument in logical
            )
        ):
            raise WorkerError("invalid_worker_argv")
        forbidden = ("--api-key", "--api-key-file", "--alias", "-a")
        if any(
            argument in forbidden
            or any(argument.startswith(flag + "=") for flag in forbidden)
            for argument in logical
        ):
            raise WorkerError("invalid_worker_argv")
        model_positions = [
            index
            for index in range(len(logical) - 1)
            if logical[index] == "--model"
            and logical[index + 1] == ownership.receipt.model_path
        ]
        if (
            len(model_positions) != 1
            or logical.count("--model") != 1
            or any(argument == "-m" or argument.startswith("-m=") for argument in logical)
        ):
            raise WorkerError("invalid_worker_argv")
        identities = ownership.descriptor_identities
        if (
            not isinstance(identities, tuple)
            or len(identities) != 2
            or any(not isinstance(item, _RetainedDescriptor) for item in identities)
        ):
            raise WorkerError("descriptor_binding_unavailable")
        binary_descriptor = identities[0].fileno()
        model_descriptor = identities[1].fileno()
        if (
            binary_descriptor < 0
            or model_descriptor < 0
            or binary_descriptor == model_descriptor
        ):
            raise WorkerError("descriptor_binding_unavailable")
        effective = list(logical)
        effective[0] = f"/dev/fd/{binary_descriptor}"
        effective[model_positions[0] + 1] = f"/dev/fd/{model_descriptor}"
        return tuple(effective), (binary_descriptor, model_descriptor)

    @staticmethod
    def _api_key_descriptor(api_key: object) -> int:
        if (
            not isinstance(api_key, str)
            or not api_key
            or "\x00" in api_key
            or "\r" in api_key
            or "\n" in api_key
        ):
            raise WorkerError("invalid_worker_identity")
        flags = getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "pipe2"):
            read_descriptor, write_descriptor = os.pipe2(flags)
        else:
            read_descriptor, write_descriptor = os.pipe()
            os.set_inheritable(read_descriptor, False)
            os.set_inheritable(write_descriptor, False)
        payload = (api_key + "\n").encode("ascii", errors="strict")
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(write_descriptor, payload[offset:])
        except BaseException:
            os.close(read_descriptor)
            raise WorkerError("invalid_worker_identity") from None
        finally:
            os.close(write_descriptor)
        return read_descriptor

    @staticmethod
    def _validate_environment(environment: Mapping[str, str]) -> None:
        for key, value in environment.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or not key
                or "\x00" in key
                or "\x00" in value
            ):
                raise WorkerError("invalid_worker_process")

    @staticmethod
    async def _drain(stream: object, collector: object) -> None:
        try:
            while True:
                chunk = await asyncio.to_thread(stream.read, 65_536)
                if not chunk:
                    return
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise WorkerError("invalid_worker_process")
                collector.feed(chunk)
        finally:
            try:
                await asyncio.to_thread(stream.close)
            except BaseException:
                pass

    async def _cleanup_post_create(
        self,
        child: object,
        stdout_drain: asyncio.Task[Any] | None,
        stderr_drain: asyncio.Task[Any] | None,
    ) -> None:
        cleanup_error: BaseException | None = None
        try:
            await self._process_authority.cleanup_failed_spawn(
                child,
                timeout=self._cleanup_timeout,
            )
        except BaseException as error:
            cleanup_error = error
        for task in (stdout_drain, stderr_drain):
            if task is not None and not task.done():
                task.cancel()
        tasks = tuple(
            task
            for task in (stdout_drain, stderr_drain)
            if task is not None
        )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if cleanup_error is not None:
            raise WorkerError("worker_unsafe_state") from None


class MacOSSignalGroup:
    """Signal only an exact, still-bound newly-created process group."""

    __slots__ = ("_getpgid", "_killpg", "_process_authority")

    def __init__(
        self,
        *,
        process_authority: PopenProcessAuthority,
        getpgid: Callable[[int], int] = os.getpgid,
        killpg: Callable[[int, int], Any] = os.killpg,
    ) -> None:
        if not isinstance(process_authority, PopenProcessAuthority):
            raise ValueError("process_authority must be a PopenProcessAuthority")
        if not callable(getpgid) or not callable(killpg):
            raise ValueError("signal callbacks must be callable")
        self._process_authority = process_authority
        self._getpgid = getpgid
        self._killpg = killpg

    def __repr__(self) -> str:
        return "MacOSSignalGroup(<exact process authority>)"

    def __call__(self, request: SignalGroupRequest) -> None:
        self._process_authority.signal_exact(
            request,
            getpgid=self._getpgid,
            killpg=self._killpg,
        )


def _reject_duplicate_json_pairs(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


class AuthenticatedLoopbackReadinessProbe:
    """Bounded stock llama.cpp ``/v1/models`` authentication proof.

    The one-time nonce never crosses HTTP.  It binds the successful response
    to the exact in-process challenge owned by the driver, while the bearer
    challenge and exact model alias are supported by stock llama.cpp.
    """

    PROTOCOL = "llama.cpp.openai.models.v1"

    __slots__ = ("_clock", "_connection_factory", "_max_response_bytes")

    def __init__(
        self,
        *,
        connection_factory: Callable[..., object] = http.client.HTTPConnection,
        max_response_bytes: int = 65_536,
        clock=time.monotonic,
    ) -> None:
        if not callable(connection_factory) or not callable(clock):
            raise ValueError("readiness callbacks must be callable")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 256 <= max_response_bytes <= 1_048_576
        ):
            raise ValueError("max_response_bytes must be between 256 and 1048576")
        self._connection_factory = connection_factory
        self._max_response_bytes = max_response_bytes
        self._clock = clock

    def __repr__(self) -> str:
        return (
            "AuthenticatedLoopbackReadinessProbe("
            f"protocol={self.PROTOCOL!r}, max_bytes={self._max_response_bytes})"
        )

    async def __call__(
        self,
        child: object,
        challenge: ReadinessChallenge,
        *,
        deadline: float,
    ) -> ReadinessProof:
        if child is None or not self._valid_challenge(challenge):
            return self._failed(getattr(challenge, "port", 0))
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
        ):
            return self._failed(challenge.port)
        remaining = float(deadline) - float(self._clock())
        if remaining <= 0:
            return self._failed(challenge.port)
        try:
            async with asyncio.timeout(remaining):
                return await asyncio.to_thread(
                    self._probe,
                    challenge,
                    remaining,
                )
        except BaseException:
            return self._failed(challenge.port)

    @staticmethod
    def _valid_challenge(challenge: object) -> bool:
        return bool(
            isinstance(challenge, ReadinessChallenge)
            and not isinstance(challenge.port, bool)
            and isinstance(challenge.port, int)
            and 1 <= challenge.port <= 65_535
            and all(
                isinstance(value, str)
                and value
                and "\x00" not in value
                and "\r" not in value
                and "\n" not in value
                for value in (challenge.api_key, challenge.nonce, challenge.alias)
            )
        )

    def _probe(
        self,
        challenge: ReadinessChallenge,
        timeout: float,
    ) -> ReadinessProof:
        connection = None
        try:
            connection = self._connection_factory(
                "127.0.0.1",
                challenge.port,
                timeout=timeout,
            )
            connection.connect()
            socket = getattr(connection, "sock", None)
            if socket is None:
                return self._failed(challenge.port)
            peer = socket.getpeername()
            if (
                not isinstance(peer, tuple)
                or len(peer) < 2
                or peer[0] != "127.0.0.1"
                or peer[1] != challenge.port
            ):
                return self._failed(challenge.port)
            connection.request(
                "GET",
                "/v1/models",
                body=None,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {challenge.api_key}",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if getattr(response, "status", None) != 200:
                return self._failed(challenge.port)
            if response.getheader("Content-Encoding") not in (None, "", "identity"):
                return self._failed(challenge.port)
            content_type = response.getheader("Content-Type")
            if not isinstance(content_type, str):
                return self._failed(challenge.port)
            normalized_type = "".join(content_type.lower().split())
            if normalized_type not in (
                "application/json",
                "application/json;charset=utf-8",
            ):
                return self._failed(challenge.port)
            content_length = response.getheader("Content-Length")
            if (
                not isinstance(content_length, str)
                or not content_length.isascii()
                or not content_length.isdigit()
                or len(content_length) > 10
            ):
                return self._failed(challenge.port)
            declared = int(content_length)
            if declared < 2 or declared > self._max_response_bytes:
                return self._failed(challenge.port)
            body = response.read(self._max_response_bytes + 1)
            if (
                not isinstance(body, bytes)
                or len(body) != declared
                or len(body) > self._max_response_bytes
            ):
                return self._failed(challenge.port)
            payload = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_json_pairs,
                parse_constant=_reject_json_constant,
            )
            if not self._valid_payload(payload, challenge.alias):
                return self._failed(challenge.port)
            return ReadinessProof(
                authenticated=True,
                nonce=challenge.nonce,
                alias=challenge.alias,
                port=challenge.port,
            )
        except BaseException:
            return self._failed(challenge.port)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except BaseException:
                    pass

    @staticmethod
    def _valid_payload(payload: object, alias: str) -> bool:
        if (
            not isinstance(payload, dict)
            or set(payload) != {"object", "data"}
            or payload.get("object") != "list"
        ):
            return False
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != 1:
            return False
        model = data[0]
        return bool(
            isinstance(model, dict)
            and model.get("id") == alias
            and model.get("object") == "model"
            and {"id", "object"}.issubset(model)
            and set(model).issubset({"id", "object", "created", "owned_by", "meta"})
        )

    @staticmethod
    def _failed(port: object) -> ReadinessProof:
        safe_port = port if isinstance(port, int) and not isinstance(port, bool) else 0
        return ReadinessProof(
            authenticated=False,
            nonce="",
            alias="",
            port=safe_port,
        )


def build_macos_worker_driver(
    *,
    binary_root: str,
    model_root: str,
    lock_path: str,
    cwd: str,
    environment: Mapping[str, str],
    release_receipt: DescriptorBindingReleaseReceipt | None = None,
    popen_factory: Callable[..., object] = subprocess.Popen,
    connection_factory: Callable[..., object] = http.client.HTTPConnection,
    getpgid: Callable[[int], int] = os.getpgid,
    nonreaping_exit_check: Callable[[int], bool] = _stdlib_nonreaping_exit_check,
    killpg: Callable[[int, int], Any] = os.killpg,
    token_factory: Callable[[], str] | None = None,
    clock=time.monotonic,
    expected_uid: int | None = None,
    term_grace_seconds: float = 0.25,
    late_cleanup_seconds: float = 1.0,
    max_log_bytes: int = 32_768,
    max_readiness_bytes: int = 65_536,
) -> MacOSWorkerProcessDriver:
    """Compose the production driver around one shared exact-child registry.

    With default arguments this function is intentionally launch-blocked: raw
    ``subprocess.Popen`` has no audited no-orphan-on-raise contract, and no
    pinned llama-server ``/dev/fd`` probe receipt has been supplied.  Supplying
    an exact structured owner receipt is necessary but still insufficient
    unless the injected factory advertises one of the supported ownership
    contracts.
    """

    if release_receipt is not None and not isinstance(
        release_receipt,
        DescriptorBindingReleaseReceipt,
    ):
        raise ValueError("release_receipt must be structured or None")
    if token_factory is None:
        token_factory = lambda: secrets.token_urlsafe(32)

    process_authority = PopenProcessAuthority(
        getpgid=getpgid,
        nonreaping_exit_check=nonreaping_exit_check,
        killpg=killpg,
    )
    spawner = PopenLlamaSpawner(
        process_authority=process_authority,
        popen_factory=popen_factory,
        release_receipt=release_receipt,
        allowed_cwd=cwd,
        getpgid=getpgid,
    )
    effective_receipt = (
        release_receipt if spawner.descriptor_binding_supported else None
    )
    revalidator = DescriptorArtifactRevalidator(
        binary_root=binary_root,
        model_root=model_root,
        release_receipt=effective_receipt,
        spawn_adapter_sha256=spawner.spawn_adapter_sha256,
        expected_uid=expected_uid,
        clock=clock,
    )
    signal_group = MacOSSignalGroup(
        process_authority=process_authority,
        getpgid=getpgid,
        killpg=killpg,
    )
    readiness_probe = AuthenticatedLoopbackReadinessProbe(
        connection_factory=connection_factory,
        max_response_bytes=max_readiness_bytes,
        clock=clock,
    )
    interprocess_lock = FlockInterprocessWorkerLock(lock_path)
    driver_authority = MacOSDriverAuthority(interprocess_lock=interprocess_lock)
    driver = MacOSWorkerProcessDriver(
        spawn=spawner,
        revalidate_artifacts=revalidator,
        signal_group=signal_group,
        reap_process=process_authority.reap,
        readiness_probe=readiness_probe,
        process_is_live=process_authority.is_live,
        clock=clock,
        token_factory=token_factory,
        authority=driver_authority,
        close_authority_on_shutdown=True,
        environment=environment,
        cwd=cwd,
        require_descriptor_binding=True,
        term_grace_seconds=term_grace_seconds,
        late_cleanup_seconds=late_cleanup_seconds,
        max_log_bytes=max_log_bytes,
    )
    # Public status is inert evidence for release tooling; changing it cannot
    # mutate the closed-over revalidator/spawner gates.
    driver.descriptor_binding_release_receipt = effective_receipt
    return driver


class _FlockLease:
    __slots__ = ("descriptor",)

    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor

    def __repr__(self) -> str:
        return "<FlockWorkerLease>"


class FlockInterprocessWorkerLock:
    """A non-blocking, exact-lease advisory lock over a private file."""

    __slots__ = (
        "_closed",
        "_directory_descriptor",
        "_expected_uid",
        "_filename",
        "_guard",
        "_lease",
    )

    def __init__(
        self,
        path: str,
        *,
        trusted_root: str | None = None,
        expected_uid: int | None = None,
    ) -> None:
        if (
            not isinstance(path, str)
            or not os.path.isabs(path)
            or not path
            or "\x00" in path
            or os.path.normpath(path) != path
        ):
            raise ValueError("lock path must be normalized and absolute")
        if trusted_root is None:
            trusted_root = os.path.dirname(path)
        trusted_root = DescriptorArtifactRevalidator._validate_root(
            "trusted_root",
            trusted_root,
        )
        if expected_uid is None:
            expected_uid = os.getuid()
        if (
            isinstance(expected_uid, bool)
            or not isinstance(expected_uid, int)
            or expected_uid < 0
        ):
            raise ValueError("expected_uid must be a non-negative integer")
        components = DescriptorArtifactRevalidator._relative_components(
            trusted_root,
            path,
        )
        directory = -1
        try:
            directory = DescriptorArtifactRevalidator._open_absolute_directory(
                trusted_root
            )
            self._validate_private_directory(directory, expected_uid)
            for component in components[:-1]:
                next_directory = os.open(
                    component,
                    DescriptorArtifactRevalidator._directory_flags(),
                    dir_fd=directory,
                )
                if directory >= 0:
                    os.close(directory)
                directory = next_directory
                self._validate_private_directory(directory, expected_uid)
        except BaseException:
            try:
                os.close(directory)
            except OSError:
                pass
            raise WorkerError("worker_unsafe_state") from None
        self._directory_descriptor = directory
        self._filename = components[-1]
        self._expected_uid = expected_uid
        self._guard = threading.Lock()
        self._lease: _FlockLease | None = None
        self._closed = False

    def __repr__(self) -> str:
        return "FlockInterprocessWorkerLock(<private path>)"

    def acquire(self) -> object | None:
        with self._guard:
            if self._closed or self._lease is not None:
                raise WorkerError("worker_unsafe_state")
            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(
                    self._filename,
                    flags,
                    0o600,
                    dir_fd=self._directory_descriptor,
                )
            except OSError:
                raise WorkerError("worker_unsafe_state") from None
            try:
                facts = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(facts.st_mode)
                    or facts.st_uid != self._expected_uid
                    or stat.S_IMODE(facts.st_mode) != 0o600
                    or facts.st_nlink != 1
                ):
                    raise WorkerError("worker_unsafe_state")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    if error.errno in (errno.EACCES, errno.EAGAIN):
                        os.close(descriptor)
                        return None
                    raise WorkerError("worker_unsafe_state") from None
            except BaseException:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            lease = _FlockLease(descriptor)
            self._lease = lease
            return lease

    def release(self, lease: object) -> None:
        with self._guard:
            if self._closed or lease is not self._lease:
                raise WorkerError("worker_unsafe_state")
            assert isinstance(lease, _FlockLease)
            try:
                fcntl.flock(lease.descriptor, fcntl.LOCK_UN)
                os.close(lease.descriptor)
            except OSError:
                raise WorkerError("worker_unsafe_state") from None
            self._lease = None

    def close(self) -> None:
        with self._guard:
            if self._closed:
                return
            lease = self._lease
            first_error: BaseException | None = None
            if lease is not None:
                try:
                    fcntl.flock(lease.descriptor, fcntl.LOCK_UN)
                except BaseException as error:
                    first_error = error
                try:
                    os.close(lease.descriptor)
                except BaseException as error:
                    if first_error is None:
                        first_error = error
                self._lease = None
            if self._directory_descriptor >= 0:
                try:
                    os.close(self._directory_descriptor)
                except BaseException as error:
                    if first_error is None:
                        first_error = error
                self._directory_descriptor = -1
            self._closed = True
            if first_error is not None:
                raise WorkerError("worker_unsafe_state") from None

    @staticmethod
    def _validate_private_directory(descriptor: int, expected_uid: int) -> None:
        facts = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(facts.st_mode)
            or facts.st_uid != expected_uid
            or facts.st_mode & 0o022
        ):
            raise WorkerError("worker_unsafe_state")


__all__ = [
    "AuthenticatedLoopbackReadinessProbe",
    "DescriptorArtifactRevalidator",
    "DescriptorBindingReceiptError",
    "DescriptorBindingReleaseReceipt",
    "FlockInterprocessWorkerLock",
    "MacOSSignalGroup",
    "PopenLlamaSpawner",
    "PopenProcessAuthority",
    "build_macos_worker_driver",
    "parse_descriptor_binding_release_receipt",
]
