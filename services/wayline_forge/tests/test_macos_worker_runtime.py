import asyncio
import base64
from dataclasses import replace
import hashlib
import io
import os
import platform
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from services.wayline_forge.app.llama_worker import (
    ArtifactVerificationReceipt,
    WorkerError,
)
from services.wayline_forge.app.macos_worker_runtime import (
    AuthenticatedLoopbackReadinessProbe,
    DescriptorArtifactRevalidator,
    DescriptorBindingReleaseReceipt,
    FlockInterprocessWorkerLock,
    MacOSSignalGroup,
    PopenLlamaSpawner,
    PopenProcessAuthority,
    build_macos_worker_driver,
)
from services.wayline_forge.app.macos_worker_driver import (
    BoundedRedactedOutput,
    ReadinessChallenge,
    SignalGroupRequest,
    MacOSWorkerProcessDriver,
    SpawnOwnership,
    SpawnSpecification,
)


class FlockInterprocessWorkerLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = os.path.realpath(self.temporary_directory.name)
        self.path = os.path.join(self.root, "worker.lock")

    def test_lockfile_is_private_and_second_lock_is_nonblocking(self):
        first = FlockInterprocessWorkerLock(self.path)
        second = FlockInterprocessWorkerLock(self.path)
        self.addCleanup(first.close)
        self.addCleanup(second.close)

        first_lease = first.acquire()

        self.assertIsNotNone(first_lease)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertIsNone(second.acquire())

        first.release(first_lease)
        second_lease = second.acquire()
        self.assertIsNotNone(second_lease)
        second.release(second_lease)

    def test_forged_lease_does_not_unlock(self):
        first = FlockInterprocessWorkerLock(self.path)
        second = FlockInterprocessWorkerLock(self.path)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        lease = first.acquire()
        self.assertIsNotNone(lease)

        with self.assertRaises(WorkerError):
            first.release(object())

        self.assertIsNone(second.acquire())
        first.release(lease)

    def test_close_releases_once_and_prevents_reacquisition(self):
        lock = FlockInterprocessWorkerLock(self.path)
        contender = FlockInterprocessWorkerLock(self.path)
        self.addCleanup(contender.close)
        lease = lock.acquire()
        self.assertIsNotNone(lease)

        lock.close()
        lock.close()

        contender_lease = contender.acquire()
        self.assertIsNotNone(contender_lease)
        contender.release(contender_lease)
        with self.assertRaises(WorkerError):
            lock.acquire()

    def test_close_failure_still_closes_descriptor_and_lock_instance(self):
        lock = FlockInterprocessWorkerLock(self.path)
        contender = FlockInterprocessWorkerLock(self.path)
        self.addCleanup(contender.close)
        lease = lock.acquire()
        self.assertIsNotNone(lease)
        real_flock = __import__("fcntl").flock

        def fail_unlock(descriptor, operation):
            if operation == __import__("fcntl").LOCK_UN:
                raise OSError("injected unlock failure")
            return real_flock(descriptor, operation)

        with patch(
            "services.wayline_forge.app.macos_worker_runtime.fcntl.flock",
            side_effect=fail_unlock,
        ):
            with self.assertRaises(WorkerError):
                lock.close()

        with self.assertRaises(WorkerError):
            lock.acquire()
        contender_lease = contender.acquire()
        self.assertIsNotNone(contender_lease)
        contender.release(contender_lease)

    def test_rejects_symlink_writable_parent_non_normal_path_and_hardlink(self):
        real_private = os.path.join(self.root, "real-private")
        os.mkdir(real_private, 0o700)
        linked_private = os.path.join(self.root, "linked-private")
        os.symlink(real_private, linked_private)
        with self.assertRaises((ValueError, WorkerError)):
            FlockInterprocessWorkerLock(
                os.path.join(linked_private, "worker.lock"),
                trusted_root=linked_private,
            )

        writable = os.path.join(self.root, "writable")
        os.mkdir(writable, 0o770)
        os.chmod(writable, 0o770)
        with self.assertRaises(WorkerError):
            FlockInterprocessWorkerLock(
                os.path.join(writable, "worker.lock"),
                trusted_root=writable,
            )

        with self.assertRaises(ValueError):
            FlockInterprocessWorkerLock(
                os.path.join(self.root, "nested", "..", "worker.lock"),
                trusted_root=self.root,
            )

        first = FlockInterprocessWorkerLock(self.path, trusted_root=self.root)
        self.addCleanup(first.close)
        lease = first.acquire()
        first.release(lease)
        hardlink = os.path.join(self.root, "worker-hardlink")
        os.link(self.path, hardlink)
        second = FlockInterprocessWorkerLock(self.path, trusted_root=self.root)
        self.addCleanup(second.close)
        with self.assertRaises(WorkerError):
            second.acquire()

    def test_real_cross_process_contender_is_nonblocking(self):
        lock = FlockInterprocessWorkerLock(self.path, trusted_root=self.root)
        self.addCleanup(lock.close)
        lease = lock.acquire()
        self.assertIsNotNone(lease)
        code = (
            "import sys; "
            "from services.wayline_forge.app.macos_worker_runtime import "
            "FlockInterprocessWorkerLock as L; "
            "lock=L(sys.argv[1], trusted_root=sys.argv[2]); "
            "lease=lock.acquire(); "
            "print('blocked' if lease is None else 'acquired'); "
            "lock.close()"
        )

        completed = subprocess.run(
            [sys.executable, "-c", code, self.path, self.root],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=True,
        )

        self.assertEqual(completed.stdout.strip(), "blocked")
        lock.release(lease)


class DescriptorArtifactRevalidatorTests(unittest.IsolatedAsyncioTestCase):
    SPAWN_ADAPTER_SHA256 = "a" * 64
    LLAMA_CPP_REVISION = "b" * 40

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.temporary_root = os.path.realpath(self.temporary_directory.name)
        self.binary_root = os.path.join(self.temporary_root, "bin")
        self.model_root = os.path.join(self.temporary_root, "models")
        os.mkdir(self.binary_root, 0o700)
        os.mkdir(self.model_root, 0o700)
        self.binary_path = os.path.join(self.binary_root, "llama-server")
        self.model_path = os.path.join(self.model_root, "wayline.gguf")
        self._write(self.binary_path, b"trusted-binary", mode=0o700)
        self._write(self.model_path, b"trusted-model", mode=0o600)

    @staticmethod
    def _write(path: str, content: bytes, *, mode: int) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            os.write(descriptor, content)
        finally:
            os.close(descriptor)

    def receipt(self, *, binary_path=None, model_path=None):
        binary_path = binary_path or self.binary_path
        model_path = model_path or self.model_path
        binary = os.stat(binary_path)
        model = os.stat(model_path)
        with open(binary_path, "rb") as source:
            binary_digest = hashlib.file_digest(source, "sha256").hexdigest()
        with open(model_path, "rb") as source:
            model_digest = hashlib.file_digest(source, "sha256").hexdigest()
        return ArtifactVerificationReceipt(
            binary_path=binary_path,
            model_path=model_path,
            binary_sha256=binary_digest,
            model_sha256=model_digest,
            binary_size=binary.st_size,
            model_size=model.st_size,
            binary_device=binary.st_dev,
            binary_inode=binary.st_ino,
            model_device=model.st_dev,
            model_inode=model.st_ino,
        )

    def release_receipt(self, artifacts=None, **changes):
        artifacts = artifacts or self.receipt()
        arguments = {
            "binary_sha256": artifacts.binary_sha256,
            "model_sha256": artifacts.model_sha256,
            "llama_cpp_revision": self.LLAMA_CPP_REVISION,
            "os_name": platform.system(),
            "architecture": platform.machine(),
            "readiness_protocol_revision": "llama.cpp.openai.models.v1",
            "spawn_adapter_sha256": self.SPAWN_ADAPTER_SHA256,
        }
        arguments.update(changes)
        return DescriptorBindingReleaseReceipt.attest(**arguments)

    def revalidator(self, *, artifacts=None, **changes):
        artifacts = artifacts or self.receipt()
        arguments = {
            "binary_root": self.binary_root,
            "model_root": self.model_root,
            "release_receipt": self.release_receipt(artifacts),
            "spawn_adapter_sha256": self.SPAWN_ADAPTER_SHA256,
        }
        arguments.update(changes)
        return DescriptorArtifactRevalidator(**arguments)

    async def validate(self, receipt=None, revalidator=None):
        receipt = receipt or self.receipt()
        return await (revalidator or self.revalidator(artifacts=receipt))(
            receipt,
            deadline=asyncio.get_running_loop().time() + 1.0,
        )

    async def test_retains_hashed_regular_descriptors_until_exact_close(self):
        receipt = self.receipt()

        ownership = await self.validate(receipt)

        self.assertIs(ownership.receipt, receipt)
        self.assertTrue(ownership.descriptor_binding_supported)
        binary, model = ownership.descriptor_identities
        self.assertNotIsInstance(binary, (bool, int, float, str, bytes, tuple))
        self.assertNotIsInstance(model, (bool, int, float, str, bytes, tuple))
        self.assertEqual(os.pread(binary.fileno(), 64, 0), b"trusted-binary")
        self.assertEqual(os.pread(model.fileno(), 64, 0), b"trusted-model")

        ownership.close()
        ownership.close()

        for retained in (binary, model):
            with self.assertRaises(OSError):
                os.fstat(retained.fileno())

    async def test_retained_descriptor_survives_path_swap(self):
        receipt = self.receipt()
        ownership = await self.validate(receipt)
        binary = ownership.descriptor_identities[0]
        replacement = self.binary_path + ".replacement"
        self._write(replacement, b"untrusted-replacement", mode=0o700)

        os.replace(replacement, self.binary_path)

        self.assertEqual(os.pread(binary.fileno(), 64, 0), b"trusted-binary")
        self.assertNotEqual(os.stat(self.binary_path).st_ino, receipt.binary_inode)
        ownership.close()

    async def test_rejects_final_and_intermediate_symlinks(self):
        final_link = os.path.join(self.binary_root, "linked-server")
        os.symlink(self.binary_path, final_link)
        nested = os.path.join(self.binary_root, "nested")
        real_nested = os.path.join(self.temporary_root, "real-nested")
        os.mkdir(real_nested, 0o700)
        nested_binary = os.path.join(real_nested, "llama-server")
        self._write(nested_binary, b"nested", mode=0o700)
        os.symlink(real_nested, nested)

        for unsafe_path in (final_link, os.path.join(nested, "llama-server")):
            with self.subTest(path=unsafe_path):
                unsafe_receipt = self.receipt(binary_path=unsafe_path)
                with self.assertRaises(WorkerError):
                    await self.validate(unsafe_receipt)

    async def test_rejects_traversal_and_paths_outside_trusted_root(self):
        outside = os.path.join(self.temporary_root, "outside")
        self._write(outside, b"outside", mode=0o700)
        unsafe = self.receipt(binary_path=outside)
        traversal = replace(
            unsafe,
            binary_path=os.path.join(self.binary_root, "..", "outside"),
        )

        for receipt in (unsafe, traversal):
            with self.subTest(path=receipt.binary_path):
                with self.assertRaises(WorkerError):
                    await self.validate(receipt)

    async def test_rejects_nonregular_writable_and_unexpected_owner(self):
        directory_artifact = os.path.join(self.binary_root, "directory")
        os.mkdir(directory_artifact, 0o700)
        writable = self.receipt()
        os.chmod(self.binary_path, 0o720)

        with self.assertRaises(WorkerError):
            await self.validate(writable)

        os.chmod(self.binary_path, 0o700)
        with self.assertRaises(WorkerError):
            await self.validate(
                replace(
                    self.receipt(),
                    binary_path=directory_artifact,
                    binary_sha256=hashlib.sha256(b"").hexdigest(),
                    binary_size=0,
                    binary_inode=os.stat(directory_artifact).st_ino,
                )
            )
        with self.assertRaises(WorkerError):
            await self.validate(
                revalidator=self.revalidator(expected_uid=os.getuid() + 1)
            )

    async def test_rejects_digest_inode_and_duplicate_inode_mismatch_without_leak(self):
        receipt = self.receipt()
        hardlinked_model = os.path.join(self.model_root, "same-inode.gguf")
        os.unlink(self.model_path)
        os.link(self.binary_path, hardlinked_model)
        duplicate = self.receipt(model_path=hardlinked_model)
        before = set(os.listdir("/dev/fd"))

        for unsafe in (
            replace(receipt, binary_sha256="0" * 64),
            replace(receipt, binary_inode=receipt.binary_inode + 1),
            duplicate,
        ):
            with self.subTest(receipt=unsafe):
                with self.assertRaises(WorkerError):
                    await self.validate(unsafe)

        self.assertEqual(set(os.listdir("/dev/fd")), before)

    async def test_rejects_bool_receipt_fact_even_when_equal_to_integer(self):
        one_byte = os.path.join(self.binary_root, "one-byte-server")
        self._write(one_byte, b"x", mode=0o700)
        receipt = self.receipt(binary_path=one_byte)
        self.assertEqual(receipt.binary_size, 1)

        with self.assertRaises(WorkerError):
            await self.validate(replace(receipt, binary_size=True))

    def test_release_receipt_is_fail_closed_until_attested(self):
        revalidator = self.revalidator(release_receipt=None)

        self.assertFalse(revalidator.descriptor_binding_supported)
        with self.assertRaises(ValueError):
            DescriptorArtifactRevalidator(
                binary_root="relative/bin",
                model_root=self.model_root,
                release_receipt=None,
                spawn_adapter_sha256=self.SPAWN_ADAPTER_SHA256,
            )

    async def test_release_receipt_rejects_cross_artifact_platform_and_adapter(self):
        artifacts = self.receipt()
        changes = (
            {"binary_sha256": "0" * 64},
            {"model_sha256": "1" * 64},
            {"os_name": "NotCurrentOS"},
            {"architecture": "not-current-arch"},
            {"readiness_protocol_revision": "stale.protocol.v0"},
            {"spawn_adapter_sha256": "2" * 64},
        )

        for change in changes:
            with self.subTest(change=change):
                revalidator = self.revalidator(
                    artifacts=artifacts,
                    release_receipt=self.release_receipt(artifacts, **change),
                )
                with self.assertRaises(WorkerError):
                    await self.validate(artifacts, revalidator=revalidator)

    async def test_cancelled_revalidation_closes_late_retained_descriptors(self):
        revalidator = self.revalidator()
        artifacts = self.receipt()
        entered = threading.Event()
        release = threading.Event()
        original = DescriptorArtifactRevalidator._revalidate
        before = set(os.listdir("/dev/fd"))

        def blocking_revalidate(instance, receipt, deadline):
            entered.set()
            release.wait()
            return original(instance, receipt, deadline)

        with patch.object(
            DescriptorArtifactRevalidator,
            "_revalidate",
            blocking_revalidate,
        ):
            task = asyncio.create_task(
                revalidator(
                    artifacts,
                    deadline=asyncio.get_running_loop().time() + 2.0,
                )
            )
            self.assertTrue(await asyncio.to_thread(entered.wait, 1.0))
            task.cancel()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
            await revalidator.wait_for_background_cleanup()

        self.assertEqual(set(os.listdir("/dev/fd")), before)

    async def test_completed_background_failure_remains_observable(self):
        revalidator = self.revalidator()

        async def fail_cleanup():
            raise RuntimeError("late descriptor cleanup failed")

        background = asyncio.create_task(fail_cleanup())
        revalidator._retain_background(background)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertTrue(background.done())
        self.assertNotIn(background, revalidator._background_tasks)

        with self.assertRaisesRegex(WorkerError, "worker_unsafe_state"):
            await revalidator.wait_for_background_cleanup()


class FakePopen:
    def __init__(
        self,
        pid: int,
        *,
        stdout=None,
        stderr=None,
        order=None,
        pid_error: BaseException | None = None,
        returncode: int | None = None,
        wait_timeouts: int = 0,
    ) -> None:
        self._pid = pid
        self.stdout = stdout if stdout is not None else io.BytesIO(b"server ready\n")
        self.stderr = stderr if stderr is not None else io.BytesIO(b"server stopped\n")
        self.order = order if order is not None else []
        self.pid_error = pid_error
        self.returncode = returncode
        self.wait_calls = 0
        self.wait_timeouts = wait_timeouts
        self.terminate_calls = 0
        self.kill_calls = 0

    @property
    def pid(self):
        self.order.append("pid")
        if self.pid_error is not None:
            raise self.pid_error
        return self._pid

    def poll(self):
        raise AssertionError("poll() must never be used by liveness authority")

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_timeouts:
            self.wait_timeouts -= 1
            raise subprocess.TimeoutExpired("fake-popen", timeout)
        return 0 if self.returncode is None else self.returncode


class SafeFakePopenFactory:
    wayline_no_child_on_raise = True
    wayline_spawn_adapter_sha256 = DescriptorArtifactRevalidatorTests.SPAWN_ADAPTER_SHA256

    def __init__(self, child: FakePopen, order=None) -> None:
        self.child = child
        self.order = order if order is not None else []
        self.calls = []
        self.api_key_file_bytes = None

    def __call__(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        self.order.append("popen")
        self.child.args = tuple(argv)
        if "--api-key-file" in argv:
            path = argv[argv.index("--api-key-file") + 1]
            descriptor = int(path.removeprefix("/dev/fd/"))
            self.api_key_file_bytes = os.read(descriptor, 4096)
        return self.child


class UnsafePopenFactory:
    def __init__(self) -> None:
        self.called = False

    def __call__(self, argv, **kwargs):
        self.called = True
        raise RuntimeError("could have created an unowned child")


class CallbackRaisingPopenFactory:
    wayline_child_created_callback = True
    wayline_spawn_adapter_sha256 = DescriptorArtifactRevalidatorTests.SPAWN_ADAPTER_SHA256

    def __init__(self, child: FakePopen) -> None:
        self.child = child
        self.calls = 0

    def __call__(self, argv, *, wayline_child_created, **kwargs):
        self.calls += 1
        wayline_child_created(self.child)
        raise RuntimeError("failure after internal child creation")


class BlockingSafePopenFactory(SafeFakePopenFactory):
    def __init__(self, child: FakePopen, release: threading.Event) -> None:
        super().__init__(child)
        self.release = release
        self.entered = threading.Event()

    def __call__(self, argv, **kwargs):
        self.entered.set()
        self.release.wait()
        return super().__call__(argv, **kwargs)


class PopenRuntimeTests(DescriptorArtifactRevalidatorTests):
    async def asyncSetUp(self) -> None:
        asyncio.get_running_loop().slow_callback_duration = 5.0
        self.ownership = await self.validate()
        self.addAsyncCleanup(self._close_ownership)
        self.cleanup_group_signals = []
        self.process_authority = PopenProcessAuthority(
            getpgid=lambda pid: pid,
            nonreaping_exit_check=lambda pid: False,
            killpg=lambda pgid, signum: self.cleanup_group_signals.append(
                (pgid, signum)
            ),
        )

    async def _close_ownership(self) -> None:
        self.ownership.close()

    def specification(self, child_claims, completions, *, order=None):
        api_key = "private-api-key"
        alias = "wayline-private-alias"

        def claim_child(claim):
            if order is not None:
                order.append("claim")
            child_claims.append(claim)

        spawn_ownership = SpawnOwnership(
            claim_child,
            lambda result: completions.append(result),
            required_argv_pairs=(("--alias", alias),),
            required_argv_flags=("--api-key-file",),
        )
        return SpawnSpecification(
            argv=(
                self.binary_path,
                "--model",
                self.model_path,
                "--host",
                "127.0.0.1",
                "--port",
                "18081",
            ),
            executable=self.binary_path,
            shell=False,
            start_new_session=True,
            stdin=-3,
            stdout=-1,
            stderr=-1,
            close_fds=True,
            cwd=self.temporary_root,
            env={"HOME": self.temporary_root, "LC_ALL": "C"},
            artifact_ownership=self.ownership,
            readiness_api_key=api_key,
            readiness_nonce="private-nonce",
            readiness_alias=alias,
            stdout_collector=BoundedRedactedOutput(
                max_bytes=4096,
                sensitive_values=(api_key, alias),
            ),
            stderr_collector=BoundedRedactedOutput(
                max_bytes=4096,
                sensitive_values=(api_key, alias),
            ),
            spawn_ownership=spawn_ownership,
        )

    def spawner(self, factory, *, getpgid=None, drain_task_factory=None):
        arguments = {}
        if drain_task_factory is not None:
            arguments["drain_task_factory"] = drain_task_factory
        return PopenLlamaSpawner(
            process_authority=self.process_authority,
            popen_factory=factory,
            release_receipt=self.release_receipt(),
            allowed_cwd=self.temporary_root,
            getpgid=getpgid or (lambda pid: pid),
            **arguments,
        )

    async def test_effective_argv_uses_retained_fds_and_claim_precedes_pid(self):
        order = []
        child = FakePopen(7311, order=order)
        factory = SafeFakePopenFactory(child, order=order)
        claims = []
        completions = []
        specification = self.specification(claims, completions, order=order)

        result = await self.spawner(factory)(specification)
        await asyncio.gather(result.stdout_drain, result.stderr_drain)

        self.assertEqual(len(factory.calls), 1)
        effective_argv, kwargs = factory.calls[0]
        binary, model = self.ownership.descriptor_identities
        self.assertEqual(effective_argv[0], f"/dev/fd/{binary.fileno()}")
        model_index = effective_argv.index("--model") + 1
        self.assertEqual(effective_argv[model_index], f"/dev/fd/{model.fileno()}")
        self.assertEqual(effective_argv.count("--api-key-file"), 1)
        self.assertNotIn("--api-key", effective_argv)
        self.assertNotIn("private-api-key", effective_argv)
        self.assertEqual(effective_argv.count("--alias"), 1)
        self.assertEqual(effective_argv.count("wayline-private-alias"), 1)
        self.assertNotIn("private-nonce", effective_argv)
        self.assertEqual(kwargs["pass_fds"][:2], (binary.fileno(), model.fileno()))
        self.assertEqual(len(kwargs["pass_fds"]), 3)
        key_descriptor = kwargs["pass_fds"][2]
        self.assertIn(
            f"/dev/fd/{key_descriptor}",
            effective_argv,
        )
        with self.assertRaises(OSError):
            os.fstat(key_descriptor)
        self.assertEqual(factory.api_key_file_bytes, b"private-api-key\n")
        self.assertNotIn("private-api-key", child.args)
        self.assertNotIn("private-api-key", repr(child.args))
        self.assertEqual(kwargs["executable"], effective_argv[0])
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(kwargs["cwd"], self.temporary_root)
        self.assertEqual(kwargs["env"], {"HOME": self.temporary_root, "LC_ALL": "C"})
        self.assertEqual(order[:3], ["popen", "claim", "pid"])
        self.assertEqual(claims, [result.child_claim])
        self.assertEqual(completions, [result])
        self.assertTrue(self.process_authority.owns_exact_child(result.child, child))
        self.assertIn("server ready", specification.stdout_collector.snapshot())

    async def test_pid_failure_occurs_after_exact_child_claim(self):
        order = []
        child = FakePopen(7312, order=order, pid_error=RuntimeError("pid unavailable"))
        factory = SafeFakePopenFactory(child, order=order)
        claims = []
        specification = self.specification(claims, [], order=order)

        with self.assertRaises(WorkerError):
            await self.spawner(factory)(specification)

        self.assertEqual(order, ["popen", "claim", "pid"])
        self.assertEqual(len(claims), 1)
        self.assertEqual(await self.process_authority.reap(claims[0].child), 0)
        self.assertEqual(child.terminate_calls, 1)
        self.assertEqual(child.kill_calls, 0)
        self.assertTrue(child.stdout.closed)
        self.assertTrue(child.stderr.closed)
        self.assertFalse(
            self.process_authority.owns_exact_child(claims[0].child, child)
        )

    async def test_factory_without_orphan_safe_contract_fails_before_call(self):
        factory = UnsafePopenFactory()
        specification = self.specification([], [])

        with self.assertRaises(WorkerError):
            await self.spawner(factory)(specification)

        self.assertFalse(factory.called)

    async def test_callback_factory_raise_leaves_exact_child_claimed_for_reap(self):
        child = FakePopen(7317, wait_timeouts=1)
        factory = CallbackRaisingPopenFactory(child)
        claims = []

        with self.assertRaises(WorkerError):
            await self.spawner(factory)(self.specification(claims, []))

        self.assertEqual(factory.calls, 1)
        self.assertEqual(len(claims), 1)
        self.assertEqual(await self.process_authority.reap(claims[0].child), 0)
        self.assertEqual(child.terminate_calls, 1)
        self.assertEqual(child.kill_calls, 1)
        self.assertEqual(child.wait_calls, 2)
        self.assertFalse(
            self.process_authority.owns_exact_child(claims[0].child, child)
        )

    async def test_pid_is_published_before_hostile_getpgid_then_child_is_cleaned(self):
        child = FakePopen(7320)
        claims = []

        def hostile_getpgid(pid):
            self.assertTrue(
                self.process_authority.has_bound_pid(claims[0].child, pid)
            )
            raise OSError("hostile getpgid")

        with self.assertRaises(WorkerError):
            await self.spawner(
                SafeFakePopenFactory(child),
                getpgid=hostile_getpgid,
            )(self.specification(claims, []))

        self.assertEqual(child.terminate_calls, 1)
        self.assertEqual(await self.process_authority.reap(claims[0].child), 0)

    async def test_pipe_or_drain_setup_failure_cleans_exact_child(self):
        class HostilePipeChild(FakePopen):
            @property
            def stdout(self):
                raise RuntimeError("hostile stdout accessor")

            @stdout.setter
            def stdout(self, value):
                self._stdout = value

        pipe_child = HostilePipeChild(7321)
        with self.assertRaises(WorkerError):
            await self.spawner(SafeFakePopenFactory(pipe_child))(
                self.specification([], [])
            )
        self.assertEqual(pipe_child.terminate_calls, 0)

        drain_child = FakePopen(7322)

        def hostile_drain_factory(coroutine):
            coroutine.close()
            raise RuntimeError("hostile drain factory")

        with self.assertRaises(WorkerError):
            await self.spawner(
                SafeFakePopenFactory(drain_child),
                drain_task_factory=hostile_drain_factory,
            )(self.specification([], []))
        self.assertEqual(drain_child.terminate_calls, 0)
        self.assertEqual(
            self.cleanup_group_signals,
            [(7321, signal.SIGTERM), (7322, signal.SIGTERM)],
        )

    async def test_post_bind_failure_kills_exact_group_and_fake_descendant(self):
        class PostBindFailureChild(FakePopen):
            @property
            def stdout(self):
                raise RuntimeError("failure after PID and PGID binding")

            @stdout.setter
            def stdout(self, value):
                self._stdout = value

        child = PostBindFailureChild(7326, wait_timeouts=1)
        descendant_alive = True
        group_signals = []

        def killpg(pgid, signum):
            nonlocal descendant_alive
            group_signals.append((pgid, signum))
            if signum == signal.SIGKILL:
                descendant_alive = False

        process_authority = PopenProcessAuthority(
            getpgid=lambda pid: pid,
            nonreaping_exit_check=lambda _pid: False,
            killpg=killpg,
        )
        spawner = PopenLlamaSpawner(
            process_authority=process_authority,
            popen_factory=SafeFakePopenFactory(child),
            release_receipt=self.release_receipt(),
            allowed_cwd=self.temporary_root,
            getpgid=lambda pid: pid,
        )
        claims = []

        with self.assertRaises(WorkerError):
            await spawner(self.specification(claims, []))

        self.assertEqual(
            group_signals,
            [(7326, signal.SIGTERM), (7326, signal.SIGKILL)],
        )
        self.assertFalse(descendant_alive)
        self.assertEqual(child.terminate_calls, 0)
        self.assertEqual(child.kill_calls, 0)
        self.assertEqual(child.wait_calls, 2)
        self.assertEqual(await process_authority.reap(claims[0].child), 0)

    async def test_post_bind_cleanup_refuses_reused_group_without_raw_fallback(self):
        live_group = {7327: 7327}

        class ReusedGroupChild(FakePopen):
            @property
            def stdout(self):
                live_group[7327] = 9000
                raise RuntimeError("group changed before cleanup signal")

            @stdout.setter
            def stdout(self, value):
                self._stdout = value

        child = ReusedGroupChild(7327, wait_timeouts=2)
        group_signals = []
        process_authority = PopenProcessAuthority(
            getpgid=lambda pid: live_group[pid],
            nonreaping_exit_check=lambda _pid: False,
            killpg=lambda pgid, signum: group_signals.append((pgid, signum)),
        )
        spawner = PopenLlamaSpawner(
            process_authority=process_authority,
            popen_factory=SafeFakePopenFactory(child),
            release_receipt=self.release_receipt(),
            allowed_cwd=self.temporary_root,
            getpgid=lambda pid: pid,
        )
        claims = []

        with self.assertRaises(WorkerError):
            await spawner(self.specification(claims, []))

        self.assertEqual(group_signals, [])
        self.assertEqual(child.terminate_calls, 0)
        self.assertEqual(child.kill_calls, 0)
        self.assertTrue(
            process_authority.owns_exact_child(claims[0].child, child)
        )

        live_group[7327] = 7327
        self.assertEqual(
            await process_authority.cleanup_failed_spawn(
                claims[0].child,
                timeout=0.1,
            ),
            0,
        )
        self.assertEqual(group_signals, [(7327, signal.SIGTERM)])

    async def test_bound_child_natural_exit_is_reaped_when_group_signal_is_stale(self):
        class PostBindFailureChild(FakePopen):
            @property
            def stdout(self):
                raise RuntimeError("failure after natural child exit")

            @stdout.setter
            def stdout(self, value):
                self._stdout = value

        child = PostBindFailureChild(7328)
        group_signals = []
        process_authority = PopenProcessAuthority(
            getpgid=lambda pid: pid,
            nonreaping_exit_check=lambda _pid: True,
            killpg=lambda pgid, signum: group_signals.append((pgid, signum)),
        )
        spawner = PopenLlamaSpawner(
            process_authority=process_authority,
            popen_factory=SafeFakePopenFactory(child),
            release_receipt=self.release_receipt(),
            allowed_cwd=self.temporary_root,
            getpgid=lambda pid: pid,
        )
        claims = []

        with self.assertRaises(WorkerError):
            await spawner(self.specification(claims, []))

        self.assertEqual(group_signals, [])
        self.assertEqual(child.terminate_calls, 0)
        self.assertEqual(child.kill_calls, 0)
        self.assertEqual(child.wait_calls, 1)
        self.assertFalse(
            process_authority.owns_exact_child(claims[0].child, child)
        )
        self.assertEqual(await process_authority.reap(claims[0].child), 0)

    async def test_failed_spawn_reaps_before_closing_blocking_streams(self):
        close_entered = threading.Event()
        release_close = threading.Event()

        class BlockingCloseStream:
            def close(self):
                close_entered.set()
                release_close.wait()

        stream = BlockingCloseStream()
        child = FakePopen(
            7325,
            stdout=stream,
            stderr=stream,
            wait_timeouts=1,
        )
        claims = []

        def hostile_getpgid(_pid):
            raise OSError("getpgid failed after child creation")

        async def failed_launch():
            with self.assertRaises(WorkerError):
                await self.spawner(
                    SafeFakePopenFactory(child),
                    getpgid=hostile_getpgid,
                )(self.specification(claims, []))

        launch = asyncio.create_task(failed_launch())
        try:
            self.assertTrue(await asyncio.to_thread(close_entered.wait, 1.0))
            self.assertEqual(child.terminate_calls, 1)
            self.assertEqual(child.kill_calls, 1)
            self.assertEqual(child.wait_calls, 2)
            self.assertEqual(len(claims), 1)
            self.assertFalse(
                self.process_authority.owns_exact_child(claims[0].child, child)
            )
        finally:
            release_close.set()
            await launch

    async def test_blocking_popen_factory_does_not_block_deadline_loop(self):
        release = threading.Event()
        factory = BlockingSafePopenFactory(FakePopen(7319), release)
        timer = threading.Timer(0.5, release.set)
        timer.start()
        self.addCleanup(timer.cancel)

        task = asyncio.create_task(
            self.spawner(factory)(self.specification([], []))
        )
        await asyncio.sleep(0)

        self.assertFalse(release.is_set())
        release.set()
        result = await task
        await asyncio.gather(result.stdout_drain, result.stderr_drain)

    async def test_cancelled_spawn_keeps_secret_fd_until_late_child_cleanup(self):
        release = threading.Event()
        child = FakePopen(7323)
        factory = BlockingSafePopenFactory(child, release)
        claims = []
        spawner = self.spawner(factory)
        before = set(os.listdir("/dev/fd"))
        task = asyncio.create_task(
            spawner(self.specification(claims, []))
        )
        self.assertTrue(await asyncio.to_thread(factory.entered.wait, 1.0))

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        release.set()
        await spawner.wait_for_background_cleanup()

        self.assertEqual(len(claims), 1)
        self.assertEqual(factory.api_key_file_bytes, b"private-api-key\n")
        self.assertEqual(child.terminate_calls, 1)
        self.assertEqual(await self.process_authority.reap(claims[0].child), 0)
        self.assertEqual(set(os.listdir("/dev/fd")), before)

    async def test_completed_spawn_cleanup_failure_remains_observable(self):
        spawner = self.spawner(SafeFakePopenFactory(FakePopen(7324)))

        async def fail_cleanup():
            raise RuntimeError("late child cleanup failed")

        background = asyncio.create_task(fail_cleanup())
        spawner._retain_background(background)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertTrue(background.done())
        self.assertNotIn(background, spawner._background_tasks)

        with self.assertRaisesRegex(WorkerError, "worker_unsafe_state"):
            await spawner.wait_for_background_cleanup()

    async def test_pipe_drains_remove_backpressure_and_terminate_at_eof(self):
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        stdout = os.fdopen(stdout_read, "rb", buffering=0)
        stderr = os.fdopen(stderr_read, "rb", buffering=0)
        child = FakePopen(7313, stdout=stdout, stderr=stderr)
        factory = SafeFakePopenFactory(child)
        payload = b"x" * (256 * 1024)
        finished = threading.Event()
        writer_errors = []

        def write_pipes():
            try:
                for descriptor in (stdout_write, stderr_write):
                    remaining = memoryview(payload)
                    while remaining:
                        written = os.write(descriptor, remaining)
                        remaining = remaining[written:]
                    os.close(descriptor)
            except BaseException as error:
                writer_errors.append(error)
            finally:
                finished.set()

        writer = threading.Thread(target=write_pipes, daemon=True)
        writer.start()
        result = await self.spawner(factory)(self.specification([], []))

        await asyncio.wait_for(
            asyncio.gather(result.stdout_drain, result.stderr_drain),
            timeout=2.0,
        )
        await asyncio.to_thread(writer.join, 2.0)

        self.assertTrue(finished.is_set())
        self.assertEqual(writer_errors, [])
        self.assertTrue(stdout.closed)
        self.assertTrue(stderr.closed)

    async def test_rejects_logical_authority_injection_before_popen(self):
        child = FakePopen(7314)
        factory = SafeFakePopenFactory(child)
        claims = []
        specification = self.specification(claims, [])
        object.__setattr__(
            specification,
            "argv",
            (*specification.argv, "--api-key", "attacker"),
        )

        with self.assertRaises(WorkerError):
            await self.spawner(factory)(specification)

        self.assertEqual(factory.calls, [])
        self.assertEqual(claims, [])

    async def test_requires_devnull_pipes_and_default_deny_collectors(self):
        for field_name, value in (
            ("stdin", 0),
            ("stdout", 1),
            ("stderr", 2),
            ("stdout_collector", object()),
            ("stderr_collector", object()),
        ):
            with self.subTest(field=field_name):
                factory = SafeFakePopenFactory(FakePopen(7318))
                specification = self.specification([], [])
                object.__setattr__(specification, field_name, value)

                with self.assertRaises(WorkerError):
                    await self.spawner(factory)(specification)

                self.assertEqual(factory.calls, [])

    async def test_exact_reap_is_once_and_liveness_never_polls(self):
        child = FakePopen(7315, returncode=None)
        factory = SafeFakePopenFactory(child)
        result = await self.spawner(factory)(self.specification([], []))

        self.assertTrue(self.process_authority.is_live(result.child))
        first, second = await asyncio.gather(
            self.process_authority.reap(result.child),
            self.process_authority.reap(result.child),
        )

        self.assertEqual((first, second), (0, 0))
        self.assertEqual(child.wait_calls, 1)
        self.assertFalse(self.process_authority.is_live(result.child))
        self.assertFalse(self.process_authority.owns_exact_child(result.child, child))

    async def test_signal_group_requires_exact_bound_identity_and_live_group(self):
        child = FakePopen(7316)
        result = await self.spawner(SafeFakePopenFactory(child))(
            self.specification([], [])
        )
        calls = []
        current_pgid = {7316: 7316}
        signal_group = MacOSSignalGroup(
            process_authority=self.process_authority,
            getpgid=lambda pid: current_pgid[pid],
            killpg=lambda pgid, signum: calls.append((pgid, signum)),
        )
        group_identity = object()
        valid = SignalGroupRequest(
            child=result.child,
            pid=7316,
            pgid=7316,
            group_identity=group_identity,
            signum=signal.SIGTERM,
        )

        for forged in (
            replace(valid, child=object()),
            replace(valid, pid=7317),
            replace(valid, pgid=7317),
            replace(valid, signum=signal.SIGUSR1),
        ):
            with self.subTest(request=forged):
                with self.assertRaises(WorkerError):
                    signal_group(forged)
        current_pgid[7316] = 9000
        with self.assertRaises(WorkerError):
            signal_group(valid)
        current_pgid[7316] = 7316

        signal_group(valid)

        self.assertEqual(calls, [(7316, signal.SIGTERM)])


class FakeSocket:
    def __init__(self, peer) -> None:
        self.peer = peer

    def getpeername(self):
        return self.peer


class FakeHTTPResponse:
    def __init__(self, *, status=200, body=b"", headers=None) -> None:
        self.status = status
        self.body = body
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.read_calls = []

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read(self, amount):
        self.read_calls.append(amount)
        return self.body[:amount]


class FakeHTTPConnection:
    def __init__(self, host, port, *, timeout, response, peer) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.response = response
        self.sock = FakeSocket(peer)
        self.requests = []
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, dict(headers or {})))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class FakeHTTPConnectionFactory:
    def __init__(self, response, *, peer=("127.0.0.1", 18081)) -> None:
        self.response = response
        self.peer = peer
        self.connections = []

    def __call__(self, host, port, *, timeout):
        connection = FakeHTTPConnection(
            host,
            port,
            timeout=timeout,
            response=self.response,
            peer=self.peer,
        )
        self.connections.append(connection)
        return connection


class AuthenticatedLoopbackReadinessProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        asyncio.get_running_loop().slow_callback_duration = 5.0

    def challenge(self, **changes):
        values = {
            "port": 18081,
            "api_key": "secret-bearer",
            "nonce": "exact-nonce",
            "alias": "exact-alias",
        }
        values.update(changes)
        return ReadinessChallenge(**values)

    @staticmethod
    def response(*, alias="exact-alias", status=200, body=None):
        if body is None:
            body = (
                '{"object":"list","data":[{"id":"'
                + alias
                + '","object":"model"}]}'
            ).encode("utf-8")
        return FakeHTTPResponse(
            status=status,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )

    async def probe(self, factory, challenge=None, *, max_response_bytes=4096):
        probe = AuthenticatedLoopbackReadinessProbe(
            connection_factory=factory,
            max_response_bytes=max_response_bytes,
        )
        return await probe(
            object(),
            challenge or self.challenge(),
            deadline=asyncio.get_running_loop().time() + 1.0,
        )

    async def test_direct_authenticated_get_returns_exact_proof(self):
        factory = FakeHTTPConnectionFactory(self.response())

        proof = await self.probe(factory)

        self.assertTrue(proof.authenticated)
        self.assertEqual(proof.alias, "exact-alias")
        self.assertEqual(proof.nonce, "exact-nonce")
        self.assertEqual(proof.port, 18081)
        connection = factory.connections[0]
        self.assertEqual(connection.host, "127.0.0.1")
        self.assertEqual(connection.port, 18081)
        self.assertTrue(connection.connected)
        self.assertTrue(connection.closed)
        method, path, body, headers = connection.requests[0]
        self.assertEqual((method, path, body), ("GET", "/v1/models", None))
        self.assertEqual(headers["Authorization"], "Bearer secret-bearer")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertNotIn("X-Wayline-Readiness-Nonce", headers)
        self.assertNotIn("X-Wayline-Readiness-Protocol", headers)
        self.assertNotIn("exact-nonce", repr(proof))
        self.assertNotIn("exact-alias", repr(proof))

    async def test_wrong_peer_key_or_alias_never_authenticates(self):
        cases = (
            (
                "peer",
                FakeHTTPConnectionFactory(
                    self.response(),
                    peer=("127.0.0.2", 18081),
                ),
                self.challenge(),
            ),
            (
                "key",
                FakeHTTPConnectionFactory(self.response(status=401)),
                self.challenge(api_key="wrong-key"),
            ),
            (
                "alias",
                FakeHTTPConnectionFactory(self.response(alias="wrong-alias")),
                self.challenge(),
            ),
        )

        for label, factory, challenge in cases:
            with self.subTest(label=label):
                proof = await self.probe(factory, challenge)
                self.assertFalse(proof.authenticated)
                self.assertEqual(proof.alias, "")
                self.assertEqual(proof.nonce, "")

    async def test_nonce_is_bound_only_to_the_in_process_challenge(self):
        first = await self.probe(
            FakeHTTPConnectionFactory(self.response()),
            self.challenge(nonce="first-private-nonce"),
        )
        second = await self.probe(
            FakeHTTPConnectionFactory(self.response()),
            self.challenge(nonce="second-private-nonce"),
        )

        self.assertTrue(first.authenticated)
        self.assertTrue(second.authenticated)
        self.assertEqual(first.nonce, "first-private-nonce")
        self.assertEqual(second.nonce, "second-private-nonce")

    async def test_redirect_oversize_duplicate_json_and_extra_alias_are_rejected(self):
        duplicate = b'{"object":"list","object":"list","data":[]}'
        extra_alias = (
            b'{"object":"list","data":['
            b'{"id":"exact-alias"},{"id":"other"}]}'
        )
        cases = (
            self.response(status=302),
            self.response(body=b"x" * 4097),
            self.response(body=duplicate),
            self.response(body=extra_alias),
        )

        for response in cases:
            with self.subTest(status=response.status, size=len(response.body)):
                proof = await self.probe(
                    FakeHTTPConnectionFactory(response),
                    max_response_bytes=4096,
                )
                self.assertFalse(proof.authenticated)

    async def test_models_schema_rejects_extra_root_and_invalid_model_object(self):
        cases = (
            b'{"object":"list","data":[{"id":"exact-alias","object":"model"}],"extra":true}',
            b'{"object":"list","data":[{"id":"exact-alias"}]}',
            b'{"object":"list","data":[{"id":"exact-alias","object":"list"}]}',
        )

        for body in cases:
            with self.subTest(body=body):
                proof = await self.probe(
                    FakeHTTPConnectionFactory(self.response(body=body))
                )
                self.assertFalse(proof.authenticated)

    def test_probe_repr_does_not_contain_connection_or_secret_state(self):
        probe = AuthenticatedLoopbackReadinessProbe(
            connection_factory=FakeHTTPConnectionFactory(self.response()),
        )

        rendered = repr(probe)

        self.assertNotIn("secret-bearer", rendered)
        self.assertNotIn("exact-nonce", rendered)


class MacOSRuntimeCompositionTests(DescriptorArtifactRevalidatorTests):
    def build_driver(
        self,
        *,
        factory,
        release_receipt=None,
        connection_factory=None,
    ):
        return build_macos_worker_driver(
            binary_root=self.binary_root,
            model_root=self.model_root,
            lock_path=os.path.join(self.temporary_root, "managed-worker.lock"),
            cwd=self.temporary_root,
            environment={"HOME": self.temporary_root, "LC_ALL": "C"},
            release_receipt=release_receipt,
            popen_factory=factory,
            connection_factory=(
                connection_factory
                or FakeHTTPConnectionFactory(
                    AuthenticatedLoopbackReadinessProbeTests.response()
                )
            ),
            getpgid=lambda pid: pid,
            nonreaping_exit_check=lambda pid: False,
            killpg=lambda pgid, signum: None,
            token_factory=lambda: "token",
        )

    def test_composition_shares_exact_process_authority_and_private_flock(self):
        receipt = self.release_receipt()
        child = FakePopen(7411)

        driver = self.build_driver(
            factory=SafeFakePopenFactory(child),
            release_receipt=receipt,
        )

        self.assertIsInstance(driver, MacOSWorkerProcessDriver)
        self.assertTrue(driver.descriptor_binding_supported)
        self.assertIs(driver.descriptor_binding_release_receipt, receipt)
        self.assertIs(
            driver._spawn._process_authority,
            driver._signal_group._process_authority,
        )
        self.assertIs(
            driver._spawn._process_authority,
            driver._reap_process.__self__,
        )
        self.assertIsInstance(
            driver._authority._interprocess_lock,
            FlockInterprocessWorkerLock,
        )
        self.assertNotIn(self.binary_root, repr(driver._spawn))

    async def test_never_started_shutdown_returns_lock_fd_to_prebuild_baseline(self):
        baseline = set(os.listdir("/dev/fd"))
        driver = self.build_driver(factory=UnsafePopenFactory())
        lock = driver._authority._interprocess_lock
        descriptor = lock._directory_descriptor
        os.fstat(descriptor)

        self.assertEqual(
            await driver.shutdown_all(
                deadline=asyncio.get_running_loop().time() + 1.0
            ),
            (),
        )

        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assertEqual(set(os.listdir("/dev/fd")), baseline)

    async def test_repeated_composition_shutdowns_do_not_accumulate_lock_fds(self):
        baseline = set(os.listdir("/dev/fd"))

        for _ in range(3):
            driver = self.build_driver(factory=UnsafePopenFactory())
            await driver.shutdown_all(
                deadline=asyncio.get_running_loop().time() + 1.0
            )
            self.assertEqual(set(os.listdir("/dev/fd")), baseline)

    async def test_default_release_gate_fails_before_popen(self):
        factory = UnsafePopenFactory()
        driver = self.build_driver(factory=factory)
        receipt = self.receipt()

        with self.assertRaises(WorkerError):
            await driver.start(
                (
                    self.binary_path,
                    "--model",
                    self.model_path,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18081",
                ),
                start_new_session=True,
                deadline=asyncio.get_running_loop().time() + 1.0,
                artifacts=receipt,
            )

        self.assertFalse(factory.called)
        self.assertFalse(driver.descriptor_binding_supported)
        self.assertIsNone(driver.descriptor_binding_release_receipt)

    async def test_stale_or_cross_artifact_release_rejects_before_popen(self):
        artifacts = self.receipt()
        receipts = (
            self.release_receipt(artifacts, binary_sha256="0" * 64),
            self.release_receipt(
                artifacts,
                readiness_protocol_revision="stale.protocol.v0",
            ),
        )

        for release_receipt in receipts:
            with self.subTest(release_receipt=release_receipt):
                factory = SafeFakePopenFactory(FakePopen(7410))
                driver = self.build_driver(
                    factory=factory,
                    release_receipt=release_receipt,
                )

                with self.assertRaises(WorkerError):
                    await driver.start(
                        (
                            self.binary_path,
                            "--model",
                            self.model_path,
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "18081",
                        ),
                        start_new_session=True,
                        deadline=asyncio.get_running_loop().time() + 1.0,
                        artifacts=artifacts,
                    )

                self.assertEqual(factory.calls, [])

    async def test_verified_composition_starts_readies_and_exactly_reaps_fake(self):
        release_receipt = self.release_receipt()
        child = FakePopen(7412)
        bearer = base64.urlsafe_b64encode(
            hashlib.sha256(b"wayline/bearer/v1\0token").digest()
        ).decode("ascii").rstrip("=")
        alias = "wayline-" + hashlib.sha256(
            b"wayline/model-alias/v1\0" + bearer.encode("ascii")
        ).hexdigest()[:32]
        response = AuthenticatedLoopbackReadinessProbeTests.response(
            alias=alias,
        )
        factory = SafeFakePopenFactory(child)
        driver = self.build_driver(
            factory=factory,
            release_receipt=release_receipt,
            connection_factory=FakeHTTPConnectionFactory(response),
        )
        receipt = self.receipt()
        deadline = asyncio.get_running_loop().time() + 1.0

        handle = await driver.start(
            (
                self.binary_path,
                "--model",
                self.model_path,
                "--host",
                "127.0.0.1",
                "--port",
                "18081",
            ),
            start_new_session=True,
            deadline=deadline,
            artifacts=receipt,
        )
        ready = await driver.await_ready(
            handle,
            port=18081,
            deadline=deadline,
        )
        exit_receipt = await driver.wait_reaped(
            handle,
            process_identity=handle.process_identity,
            deadline=deadline,
        )

        self.assertTrue(ready)
        self.assertEqual(exit_receipt.pid, 7412)
        self.assertEqual(exit_receipt.returncode, 0)
        self.assertEqual(child.wait_calls, 1)
        self.assertEqual(factory.api_key_file_bytes, (bearer + "\n").encode())
        self.assertNotIn(bearer, child.args)
        self.assertNotIn(bearer, repr(child.args))

    async def test_restart_cycles_release_fds_and_scrub_raw_child_and_secrets(self):
        release_receipt = self.release_receipt()
        bearer = base64.urlsafe_b64encode(
            hashlib.sha256(b"wayline/bearer/v1\0token").digest()
        ).decode("ascii").rstrip("=")
        alias = "wayline-" + hashlib.sha256(
            b"wayline/model-alias/v1\0" + bearer.encode("ascii")
        ).hexdigest()[:32]
        factory = SafeFakePopenFactory(FakePopen(7421))
        driver = self.build_driver(
            factory=factory,
            release_receipt=release_receipt,
            connection_factory=FakeHTTPConnectionFactory(
                AuthenticatedLoopbackReadinessProbeTests.response(alias=alias)
            ),
        )
        baseline_fds = set(os.listdir("/dev/fd"))

        for pid in (7421, 7422):
            if pid == 7422:
                factory.child = FakePopen(pid)
            deadline = asyncio.get_running_loop().time() + 1.0
            handle = await driver.start(
                (
                    self.binary_path,
                    "--model",
                    self.model_path,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18081",
                ),
                start_new_session=True,
                deadline=deadline,
                artifacts=self.receipt(),
            )
            self.assertTrue(
                await driver.await_ready(handle, port=18081, deadline=deadline)
            )
            self.assertEqual(
                (
                    await driver.wait_reaped(
                        handle,
                        process_identity=handle.process_identity,
                        deadline=deadline,
                    )
                ).pid,
                pid,
            )

        for record in driver._records.values():
            self.assertIs(record.api_key, None)
            self.assertIs(record.nonce, None)
            self.assertIs(record.alias, None)
            self.assertIs(record.transport_credentials, None)
        for record in driver._spawn._process_authority._records.values():
            self.assertIs(record.raw_child, None)
        for argv, _kwargs in factory.calls:
            self.assertNotIn(bearer, argv)
            self.assertNotIn(bearer, repr(argv))
        self.assertEqual(set(os.listdir("/dev/fd")), baseline_fds)


if __name__ == "__main__":
    unittest.main()
