import asyncio
from dataclasses import FrozenInstanceError, replace
import hashlib
import inspect
import signal
import subprocess
import threading
import unittest

from services.wayline_forge.app.llama_worker import (
    ArtifactVerificationReceipt,
    ProcessExit,
    WorkerError,
    canonical_argv_sha256,
)
from services.wayline_forge.app.macos_worker_driver import (
    ArtifactIdentity,
    BoundedRedactedOutput,
    MacOSDriverAuthority,
    MacOSProcessHandle,
    MacOSWorkerProcessDriver,
    ReadinessProof,
    RetainedArtifactOwnership,
    SpawnOwnership,
    SpawnSpecification,
)


class FakeChild:
    def __init__(self, pid: int, pgid: int | None = None) -> None:
        self.pid = pid
        self.pgid = pid if pgid is None else pgid
        self.alive = True


class FakeArtifactRevalidator:
    def __init__(
        self,
        *,
        descriptor_binding_supported: bool = True,
        changes: dict[str, object] | None = None,
        close_callback=None,
    ) -> None:
        self.descriptor_binding_supported = descriptor_binding_supported
        self.changes = changes or {}
        self.close_callback = close_callback
        self.calls: list[tuple[ArtifactVerificationReceipt, float]] = []
        self.ownerships: list[RetainedArtifactOwnership] = []

    async def __call__(
        self,
        receipt: ArtifactVerificationReceipt,
        *,
        deadline: float,
    ) -> RetainedArtifactOwnership:
        self.calls.append((receipt, deadline))
        binary = ArtifactIdentity(
            path=receipt.binary_path,
            sha256=receipt.binary_sha256,
            size=receipt.binary_size,
            device=receipt.binary_device,
            inode=receipt.binary_inode,
        )
        model = ArtifactIdentity(
            path=receipt.model_path,
            sha256=receipt.model_sha256,
            size=receipt.model_size,
            device=receipt.model_device,
            inode=receipt.model_inode,
        )
        for name, value in self.changes.items():
            target_name, field_name = name.split("_", 1)
            if target_name == "binary":
                binary = replace(binary, **{field_name: value})
            else:
                model = replace(model, **{field_name: value})
        ownership = RetainedArtifactOwnership(
            receipt=receipt,
            binary=binary,
            model=model,
            descriptor_binding_supported=self.descriptor_binding_supported,
            descriptor_identities=(object(), object()),
            close_callback=self.close_callback,
        )
        self.ownerships.append(ownership)
        return ownership


class FakeSpawner:
    def __init__(self, children: tuple[FakeChild, ...]) -> None:
        self.children = list(children)
        self.specifications: list[SpawnSpecification] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def __call__(self, specification: SpawnSpecification) -> FakeChild:
        self.specifications.append(specification)
        self.entered.set()
        if self.block:
            await self.release.wait()
        child = self.children.pop(0)
        specification.spawn_ownership.bind_executed_argv(
            (
                *specification.argv,
                "--api-key-file",
                "/dev/fd/99",
                "--alias",
                specification.readiness_alias,
            )
        )
        child_claim = specification.spawn_ownership.claim_child(child)
        return specification.spawn_ownership.complete(
            child_claim,
            pid=child.pid,
            pgid=child.pgid,
            stdout_drain=asyncio.create_task(asyncio.sleep(0)),
            stderr_drain=asyncio.create_task(asyncio.sleep(0)),
        )


class FakeSignals:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []
        self.on_signal = None

    def __call__(self, request) -> None:
        self.calls.append((request.pgid, request.signum))
        if self.on_signal is not None:
            self.on_signal(request.pgid, request.signum)


class FakeReaper:
    def __init__(self) -> None:
        self.events: dict[int, asyncio.Event] = {}
        self.returncodes: dict[int, int] = {}
        self.calls: list[FakeChild] = []

    def configure(self, child: FakeChild, returncode: int) -> None:
        self.events[child.pid] = asyncio.Event()
        self.returncodes[child.pid] = returncode

    def release(self, child: FakeChild) -> None:
        child.alive = False
        self.events[child.pid].set()

    async def __call__(self, child: FakeChild) -> int:
        self.calls.append(child)
        await self.events[child.pid].wait()
        return self.returncodes[child.pid]


class FakeReadinessProbe:
    def __init__(self) -> None:
        self.calls = []
        self.proof_factory = lambda challenge: ReadinessProof(
            authenticated=True,
            nonce=challenge.nonce,
            alias=challenge.alias,
            port=challenge.port,
        )

    async def __call__(self, child, challenge, *, deadline):
        self.calls.append((child, challenge, deadline))
        return self.proof_factory(challenge)


class ExplicitContractSpawner:
    """Fake concrete seam that claims a child before later work can fail."""

    def __init__(
        self,
        child: FakeChild,
        *,
        claimed_pid: object | None = None,
        claimed_pgid: object | None = None,
        raise_after_claim: bool = False,
        include_drains: bool = True,
    ) -> None:
        self.child = child
        self.claimed_pid = child.pid if claimed_pid is None else claimed_pid
        self.claimed_pgid = child.pgid if claimed_pgid is None else claimed_pgid
        self.raise_after_claim = raise_after_claim
        self.include_drains = include_drains
        self.specifications: list[SpawnSpecification] = []
        self.result = None

    async def __call__(self, specification: SpawnSpecification):
        self.specifications.append(specification)
        ownership = specification.spawn_ownership
        ownership.bind_executed_argv(
            (
                *specification.argv,
                "--api-key-file",
                "/dev/fd/99",
                "--alias",
                specification.readiness_alias,
            )
        )
        stdout_drain = asyncio.create_task(asyncio.sleep(0)) if self.include_drains else None
        stderr_drain = asyncio.create_task(asyncio.sleep(0)) if self.include_drains else None
        child_claim = ownership.claim_child(self.child)
        self.result = ownership.complete(
            child_claim,
            pid=self.claimed_pid,
            pgid=self.claimed_pgid,
            stdout_drain=stdout_drain,
            stderr_drain=stderr_drain,
        )
        if self.raise_after_claim:
            raise RuntimeError("post-spawn seam failure")
        return self.result


class FakeInterprocessLock:
    def __init__(self, *, close_failures: int = 0) -> None:
        self.lease = object()
        self.held = False
        self.acquire_calls = 0
        self.release_calls: list[object] = []
        self.close_calls = 0
        self.close_failures = close_failures
        self.closed = False

    def acquire(self) -> object | None:
        self.acquire_calls += 1
        if self.held:
            return None
        self.held = True
        return self.lease

    def release(self, lease: object) -> None:
        if not self.held or lease is not self.lease:
            raise RuntimeError("wrong interprocess lease")
        self.held = False
        self.release_calls.append(lease)

    def close(self) -> None:
        self.close_calls += 1
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError("transient interprocess close failure")
        self.closed = True


class MacOSWorkerProcessDriverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.loop = None
        self.receipt = ArtifactVerificationReceipt(
            binary_path="/Applications/Wayline/bin/llama-server",
            model_path="/Applications/Wayline/models/wayline.gguf",
            binary_sha256=hashlib.sha256(b"binary").hexdigest(),
            model_sha256=hashlib.sha256(b"model").hexdigest(),
            binary_size=6,
            model_size=5,
            binary_device=1,
            binary_inode=10,
            model_device=1,
            model_inode=11,
        )
        self.argv = (
            self.receipt.binary_path,
            "--model",
            self.receipt.model_path,
            "--host",
            "127.0.0.1",
            "--port",
            "18081",
            "--parallel",
            "1",
        )

    def make_driver(
        self,
        *,
        children=(FakeChild(4101),),
        authority=None,
        revalidator=None,
        spawner=None,
        signals=None,
        reaper=None,
        readiness=None,
        require_descriptor_binding=True,
        close_authority_on_shutdown=None,
    ):
        child_tuple = tuple(children)
        spawner = spawner or FakeSpawner(child_tuple)
        revalidator = revalidator or FakeArtifactRevalidator()
        signals = signals or FakeSignals()
        reaper = reaper or FakeReaper()
        for child in child_tuple:
            if child.pid not in reaper.events:
                reaper.configure(child, -15)
        readiness = readiness or FakeReadinessProbe()
        ownership_arguments = {}
        if close_authority_on_shutdown is not None:
            ownership_arguments["close_authority_on_shutdown"] = (
                close_authority_on_shutdown
            )
        driver = MacOSWorkerProcessDriver(
            spawn=spawner,
            revalidate_artifacts=revalidator,
            signal_group=signals,
            reap_process=reaper,
            readiness_probe=readiness,
            process_is_live=lambda child: child.alive,
            clock=asyncio.get_running_loop().time,
            token_factory=lambda: "token-" + str(len(spawner.specifications) + 1),
            authority=authority or MacOSDriverAuthority(),
            environment={"LC_ALL": "C", "HOME": "/private/wayline"},
            cwd="/",
            require_descriptor_binding=require_descriptor_binding,
            term_grace_seconds=0.01,
            late_cleanup_seconds=0.05,
            max_log_bytes=128,
            **ownership_arguments,
        )
        return driver, spawner, revalidator, signals, reaper, readiness

    async def start(self, driver):
        return await driver.start(
            self.argv,
            start_new_session=True,
            deadline=asyncio.get_running_loop().time() + 0.5,
            artifacts=self.receipt,
        )

    async def test_start_returns_driver_owned_frozen_identity_bound_handle(self):
        driver, spawner, revalidator, _signals, _reaper, _readiness = (
            self.make_driver()
        )

        handle = await self.start(driver)

        self.assertIsInstance(handle, MacOSProcessHandle)
        self.assertIs(handle.launch_artifacts, self.receipt)
        self.assertEqual(handle.launch_argv_sha256, canonical_argv_sha256(self.argv))
        self.assertNotIsInstance(
            handle.process_identity,
            (bool, int, float, str, bytes, tuple, frozenset),
        )
        self.assertNotEqual(handle, replace(handle))
        with self.assertRaises(FrozenInstanceError):
            handle.pid = 9
        self.assertIs(revalidator.ownerships[0].receipt, self.receipt)
        self.assertFalse(revalidator.ownerships[0].closed)
        self.assertTrue(driver.descriptor_binding_supported)

        specification = spawner.specifications[0]
        self.assertEqual(specification.argv, self.argv)
        self.assertEqual(specification.executable, self.receipt.binary_path)
        self.assertFalse(specification.shell)
        self.assertTrue(specification.start_new_session)
        self.assertIs(specification.stdin, subprocess.DEVNULL)
        self.assertIs(specification.stdout, subprocess.PIPE)
        self.assertIs(specification.stderr, subprocess.PIPE)
        self.assertTrue(specification.close_fds)
        self.assertEqual(specification.cwd, "/")
        self.assertEqual(dict(specification.env), {"HOME": "/private/wayline", "LC_ALL": "C"})
        self.assertIs(specification.artifact_ownership, revalidator.ownerships[0])
        self.assertNotIn(specification.readiness_api_key, repr(specification))
        self.assertNotIn(specification.readiness_nonce, repr(specification))
        self.assertNotIn(
            specification.readiness_api_key,
            specification.readiness_alias,
        )
        self.assertNotIn(
            specification.readiness_api_key,
            specification.readiness_nonce,
        )
        self.assertNotIn("token-1", specification.readiness_alias)
        self.assertNotIn("token-1", specification.readiness_nonce)

    def test_spawn_ownership_can_require_secret_file_flag_without_secret_value(self):
        ownership = SpawnOwnership(
            lambda claim: None,
            lambda result: None,
            required_argv_pairs=(("--alias", "safe-alias"),),
            required_argv_flags=("--api-key-file",),
        )

        digest = ownership.bind_executed_argv(
            (
                "/dev/fd/10",
                "--api-key-file",
                "/dev/fd/12",
                "--alias",
                "safe-alias",
            )
        )

        self.assertEqual(len(digest), 64)
        for invalid in (
            ("/dev/fd/10", "--alias", "safe-alias"),
            (
                "/dev/fd/10",
                "--api-key-file",
                "/dev/fd/12",
                "--api-key-file",
                "/dev/fd/13",
                "--alias",
                "safe-alias",
            ),
        ):
            fresh = SpawnOwnership(
                lambda claim: None,
                lambda result: None,
                required_argv_pairs=(("--alias", "safe-alias"),),
                required_argv_flags=("--api-key-file",),
            )
            with self.assertRaises(WorkerError):
                fresh.bind_executed_argv(invalid)

    async def test_forged_or_copied_handle_cannot_signal(self):
        driver, _spawner, _revalidator, signals, _reaper, _readiness = (
            self.make_driver()
        )
        handle = await self.start(driver)

        for forged, identity in (
            (replace(handle), handle.process_identity),
            (handle, object()),
        ):
            with self.subTest(forged=forged is not handle):
                with self.assertRaises(WorkerError):
                    driver.terminate_group(forged, process_identity=identity)

        self.assertEqual(signals.calls, [])

    async def test_mutated_public_pid_never_selects_signal_target(self):
        child = FakeChild(4101)
        driver, _spawner, _revalidator, signals, _reaper, _readiness = (
            self.make_driver(children=(child,))
        )
        handle = await self.start(driver)
        object.__setattr__(handle, "pid", 99999)

        driver.terminate_group(
            handle,
            process_identity=handle.process_identity,
        )

        self.assertEqual(signals.calls, [(4101, signal.SIGTERM)])

    async def test_mutated_artifact_or_argv_binding_is_rejected_before_signal(self):
        for field, value in (
            ("launch_artifacts", replace(self.receipt)),
            ("launch_argv_sha256", "f" * 64),
            ("process_identity", object()),
        ):
            with self.subTest(field=field):
                driver, _spawner, _revalidator, signals, _reaper, _readiness = (
                    self.make_driver()
                )
                handle = await self.start(driver)
                identity = handle.process_identity
                object.__setattr__(handle, field, value)
                with self.assertRaises(WorkerError):
                    driver.kill_group(handle, process_identity=identity)
                self.assertEqual(signals.calls, [])

    async def test_stale_handle_cannot_signal_reused_pid(self):
        first = FakeChild(4101)
        second = FakeChild(4101)
        reaper = FakeReaper()
        reaper.configure(first, -15)
        reaper.configure(second, -15)
        signals = FakeSignals()
        signals.on_signal = lambda pgid, _sig: reaper.release(first) if pgid == 4101 else None
        driver, _spawner, _revalidator, signals, reaper, _readiness = self.make_driver(
            children=(first, second), signals=signals, reaper=reaper
        )
        first_handle = await self.start(driver)
        driver.terminate_group(first_handle, process_identity=first_handle.process_identity)
        first_exit = await driver.wait_reaped(
            first_handle,
            process_identity=first_handle.process_identity,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )
        self.assertIsInstance(first_exit, ProcessExit)

        second_handle = await self.start(driver)
        with self.assertRaises(WorkerError):
            driver.kill_group(first_handle, process_identity=first_handle.process_identity)

        self.assertEqual(signals.calls, [(4101, signal.SIGTERM)])
        driver.kill_group(second_handle, process_identity=second_handle.process_identity)
        self.assertEqual(signals.calls[-1], (4101, signal.SIGKILL))

    async def test_shared_authority_denies_concurrent_driver_starts(self):
        authority = MacOSDriverAuthority()
        first_child = FakeChild(4101)
        first_spawner = FakeSpawner((first_child,))
        first_spawner.block = True
        first_reaper = FakeReaper()
        first_reaper.configure(first_child, -15)
        first_signals = FakeSignals()
        first_signals.on_signal = (
            lambda _pgid, _signum: first_reaper.release(first_child)
        )
        first, *_ = self.make_driver(
            children=(first_child,),
            authority=authority,
            spawner=first_spawner,
            reaper=first_reaper,
            signals=first_signals,
        )
        second, *_ = self.make_driver(
            children=(FakeChild(4202),), authority=authority
        )
        first_call = asyncio.create_task(self.start(first))
        await first_spawner.entered.wait()

        with self.assertRaises(WorkerError) as caught:
            await self.start(second)

        self.assertEqual(caught.exception.code, "worker_unsafe_state")
        first_call.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first_call
        first_spawner.release.set()
        await first.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.3)

    async def test_cancelled_start_retains_and_reaps_late_child(self):
        child = FakeChild(4101)
        spawner = FakeSpawner((child,))
        spawner.block = True
        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _sig: reaper.release(child)
        driver, *_ = self.make_driver(
            children=(child,), spawner=spawner, reaper=reaper, signals=signals
        )
        call = asyncio.create_task(self.start(driver))
        await spawner.entered.wait()

        call.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await call
        spawner.release.set()
        exits = await driver.shutdown_all(
            deadline=asyncio.get_running_loop().time() + 0.3
        )

        self.assertEqual(signals.calls[0], (4101, signal.SIGTERM))
        self.assertEqual(exits[0].pid, 4101)

    async def test_pre_spawn_artifact_replacement_fails_before_spawn(self):
        revalidator = FakeArtifactRevalidator(changes={"model_inode": 99})
        driver, spawner, revalidator, *_ = self.make_driver(
            revalidator=revalidator
        )

        with self.assertRaises(WorkerError) as caught:
            await self.start(driver)

        self.assertEqual(caught.exception.code, "artifact_revalidation_failed")
        self.assertEqual(spawner.specifications, [])
        self.assertTrue(revalidator.ownerships[0].closed)

    async def test_required_descriptor_binding_fails_closed(self):
        revalidator = FakeArtifactRevalidator(descriptor_binding_supported=False)
        driver, spawner, *_ = self.make_driver(revalidator=revalidator)

        with self.assertRaises(WorkerError) as caught:
            await self.start(driver)

        self.assertEqual(caught.exception.code, "descriptor_binding_unavailable")
        self.assertFalse(driver.descriptor_binding_supported)
        self.assertEqual(spawner.specifications, [])

    async def test_explicit_path_revalidation_policy_can_be_selected(self):
        revalidator = FakeArtifactRevalidator(descriptor_binding_supported=False)
        driver, spawner, *_ = self.make_driver(
            revalidator=revalidator,
            require_descriptor_binding=False,
        )

        await self.start(driver)

        self.assertFalse(driver.descriptor_binding_supported)
        self.assertEqual(len(spawner.specifications), 1)

    async def test_readiness_requires_live_child_and_exact_authenticated_proof(self):
        child = FakeChild(4101)
        readiness = FakeReadinessProbe()
        readiness.proof_factory = lambda challenge: ReadinessProof(
            authenticated=True,
            nonce="wrong-peer",
            alias=challenge.alias,
            port=challenge.port,
        )
        driver, _spawner, revalidator, _signals, _reaper, readiness = (
            self.make_driver(children=(child,), readiness=readiness)
        )
        handle = await self.start(driver)

        ready = await driver.await_ready(
            handle,
            port=18081,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )

        self.assertFalse(ready)
        self.assertEqual(len(readiness.calls), 1)
        self.assertFalse(revalidator.ownerships[0].closed)

        child.alive = False
        readiness.calls.clear()
        self.assertFalse(
            await driver.await_ready(
                handle,
                port=18081,
                deadline=asyncio.get_running_loop().time() + 0.2,
            )
        )
        self.assertEqual(readiness.calls, [])

    async def test_successful_readiness_releases_retained_artifact_descriptors(self):
        driver, _spawner, revalidator, *_ = self.make_driver()
        handle = await self.start(driver)

        ready = await driver.await_ready(
            handle,
            port=18081,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )

        self.assertTrue(ready)
        self.assertTrue(revalidator.ownerships[0].closed)

    async def test_readiness_cancellation_propagates_and_retains_probe_cleanup(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocking_probe(_child, challenge, *, deadline):
            entered.set()
            await release.wait()
            return ReadinessProof(
                authenticated=True,
                nonce=challenge.nonce,
                alias=challenge.alias,
                port=challenge.port,
            )

        driver, _spawner, revalidator, *_ = self.make_driver(
            readiness=blocking_probe
        )
        handle = await self.start(driver)
        call = asyncio.create_task(
            driver.await_ready(
                handle,
                port=18081,
                deadline=asyncio.get_running_loop().time() + 0.5,
            )
        )
        await entered.wait()

        call.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await call

        self.assertFalse(revalidator.ownerships[0].closed)
        release.set()
        await asyncio.sleep(0)

    async def test_term_timeout_then_kill_returns_exact_identity_receipt(self):
        child = FakeChild(4101)
        reaper = FakeReaper()
        reaper.configure(child, -9)
        signals = FakeSignals()
        signals.on_signal = (
            lambda _pgid, signum: reaper.release(child)
            if signum == signal.SIGKILL
            else None
        )
        driver, *_ = self.make_driver(
            children=(child,), reaper=reaper, signals=signals
        )
        handle = await self.start(driver)
        driver.terminate_group(handle, process_identity=handle.process_identity)

        term_exit = await driver.wait_reaped(
            handle,
            process_identity=handle.process_identity,
            deadline=asyncio.get_running_loop().time() + 0.01,
        )
        driver.kill_group(handle, process_identity=handle.process_identity)
        killed_exit = await driver.wait_reaped(
            handle,
            process_identity=handle.process_identity,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )

        self.assertIsNone(term_exit)
        self.assertIsInstance(killed_exit, ProcessExit)
        self.assertEqual(killed_exit.pid, 4101)
        self.assertEqual(killed_exit.returncode, -9)
        self.assertIs(killed_exit.process_identity, handle.process_identity)
        self.assertEqual(
            signals.calls,
            [(4101, signal.SIGTERM), (4101, signal.SIGKILL)],
        )

    async def test_signal_after_exact_reap_is_noop_until_handle_is_superseded(self):
        child = FakeChild(4101)
        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _signum: reaper.release(child)
        driver, *_ = self.make_driver(
            children=(child,), reaper=reaper, signals=signals
        )
        handle = await self.start(driver)
        driver.terminate_group(handle, process_identity=handle.process_identity)
        await driver.wait_reaped(
            handle,
            process_identity=handle.process_identity,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )

        driver.kill_group(handle, process_identity=handle.process_identity)

        self.assertEqual(signals.calls, [(4101, signal.SIGTERM)])

    async def test_exact_reap_scrubs_registry_secrets_but_retains_stale_receipt(self):
        child = FakeChild(4101)
        driver, _spawner, _revalidator, _signals, reaper, _readiness = (
            self.make_driver(children=(child,))
        )
        handle = await self.start(driver)
        record = driver._records[handle]
        bearer = record.api_key
        nonce = record.nonce
        alias = record.alias
        self.assertTrue(bearer and nonce and alias)
        collectors = (record.stdout_collector, record.stderr_collector)
        collector_secrets = (
            bearer,
            nonce,
            alias,
            record.artifacts.binary_path,
            record.artifacts.model_path,
        )
        for collector in collectors:
            self.assertEqual(
                set(collector._sensitive_values),
                {value.encode("utf-8") for value in collector_secrets},
            )
            collector.feed(b"server ready\n")
        live_rendered = repr(record)
        self.assertNotIn(bearer, live_rendered)
        self.assertNotIn(nonce, live_rendered)
        self.assertNotIn(alias, live_rendered)

        reaper.release(child)
        exit_receipt = await driver.wait_reaped(
            handle,
            process_identity=handle.process_identity,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )

        self.assertEqual(exit_receipt.pid, handle.pid)
        self.assertIs(record.api_key, None)
        self.assertIs(record.nonce, None)
        self.assertIs(record.alias, None)
        self.assertIs(record.transport_credentials, None)
        self.assertIs(record.process_exit, exit_receipt)
        for collector in collectors:
            self.assertEqual(collector._sensitive_values, ())
            self.assertEqual(collector._pending, bytearray())
            self.assertIn("server ready", collector.snapshot())
            collector_state = repr(collector.__dict__)
            for secret in collector_secrets:
                self.assertNotIn(secret, collector_state)
            retained = collector.snapshot()
            collector.feed((bearer + "\n").encode("utf-8"))
            self.assertEqual(collector.snapshot(), retained)
        rendered = repr(record)
        self.assertNotIn(bearer, rendered)
        self.assertNotIn(nonce, rendered)
        self.assertNotIn(alias, rendered)

    async def test_stuck_reap_is_deadline_bounded_and_retains_authority(self):
        child = FakeChild(4101)
        authority = MacOSDriverAuthority()
        reaper = FakeReaper()
        reaper.configure(child, -9)
        driver, *_ = self.make_driver(
            children=(child,), authority=authority, reaper=reaper
        )
        handle = await self.start(driver)
        driver.terminate_group(handle, process_identity=handle.process_identity)
        before = asyncio.get_running_loop().time()

        result = await driver.wait_reaped(
            handle,
            process_identity=handle.process_identity,
            deadline=before + 0.01,
        )

        self.assertIsNone(result)
        self.assertLess(asyncio.get_running_loop().time() - before, 0.2)
        replacement, *_ = self.make_driver(
            children=(FakeChild(4202),), authority=authority
        )
        with self.assertRaises(WorkerError):
            await self.start(replacement)

        reaper.release(child)
        exit_receipt = await driver.wait_reaped(
            handle,
            process_identity=handle.process_identity,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )
        self.assertEqual(exit_receipt.pid, child.pid)

    async def test_shutdown_all_terms_reaps_and_permanently_closes_driver(self):
        child = FakeChild(4101)
        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _signum: reaper.release(child)
        driver, _spawner, revalidator, signals, *_ = self.make_driver(
            children=(child,), reaper=reaper, signals=signals
        )
        await self.start(driver)

        exits = await driver.shutdown_all(
            deadline=asyncio.get_running_loop().time() + 0.2
        )

        self.assertEqual([receipt.pid for receipt in exits], [4101])
        self.assertEqual(signals.calls, [(4101, signal.SIGTERM)])
        self.assertTrue(revalidator.ownerships[0].closed)
        with self.assertRaises(WorkerError) as caught:
            await self.start(driver)
        self.assertEqual(caught.exception.code, "worker_driver_closed")

    async def test_owned_authority_close_failure_is_retryable_and_close_once(self):
        interprocess_lock = FakeInterprocessLock(close_failures=1)
        authority = MacOSDriverAuthority(interprocess_lock=interprocess_lock)
        driver, *_ = self.make_driver(
            children=(),
            authority=authority,
            close_authority_on_shutdown=True,
        )

        with self.assertRaisesRegex(WorkerError, "worker_shutdown_incomplete"):
            await driver.shutdown_all(
                deadline=asyncio.get_running_loop().time() + 0.2
            )

        self.assertEqual(interprocess_lock.close_calls, 1)
        self.assertFalse(interprocess_lock.closed)
        self.assertEqual(
            await driver.shutdown_all(
                deadline=asyncio.get_running_loop().time() + 0.2
            ),
            (),
        )
        self.assertTrue(interprocess_lock.closed)
        self.assertEqual(interprocess_lock.close_calls, 2)

        self.assertEqual(
            await driver.shutdown_all(
                deadline=asyncio.get_running_loop().time() + 0.2
            ),
            (),
        )
        self.assertEqual(interprocess_lock.close_calls, 2)
        with self.assertRaisesRegex(WorkerError, "worker_unsafe_state"):
            authority.reserve(object())

    def test_bounded_output_redacts_secrets_headers_and_structured_prompts(self):
        collector = BoundedRedactedOutput(
            max_bytes=96,
            sensitive_values=("top-secret-token", "/private/model.gguf"),
            max_line_bytes=40,
        )

        collector.feed(b"Authorization: Bearer top-")
        collector.feed(b"secret-token\n")
        collector.feed(b'{"messages":[{"content":"student answer"}]}\n')
        collector.feed(b"model=/private/model.gguf\n")
        collector.feed(b"x" * 200 + b"\n")
        output = collector.snapshot()

        self.assertNotIn("top-secret-token", output)
        self.assertNotIn("student answer", output)
        self.assertNotIn("/private/model.gguf", output)
        self.assertIn("[REDACTED]", output)
        self.assertIn("[REDACTED STRUCTURED OUTPUT]", output)
        self.assertIn("[REDACTED OVERSIZED LINE]", output)
        self.assertLessEqual(len(output.encode("utf-8")), 96)

    def test_bounded_output_is_default_deny_for_plain_and_multiline_text(self):
        collector = BoundedRedactedOutput(
            max_bytes=256,
            sensitive_values=("line-wrapped-secret",),
            max_line_bytes=80,
        )

        collector.feed(b"A student asks why 3/4 divided by 2 equals 3/8\n")
        collector.feed(b"model response first line\nsecond line\n")
        collector.feed(b"line-wrapped-\nsecret\n")
        collector.feed(b"server ready\n")
        output = collector.snapshot()

        self.assertNotIn("student", output.lower())
        self.assertNotIn("model response", output.lower())
        self.assertNotIn("line-wrapped", output)
        self.assertNotIn("secret", output)
        self.assertIn("[REDACTED UNRECOGNIZED OUTPUT]", output)
        self.assertIn("server ready", output)

    def test_bounded_output_scrub_preserves_safe_text_and_discards_future_feed(self):
        collector = BoundedRedactedOutput(
            max_bytes=256,
            sensitive_values=("top-secret-token", "/private/model.gguf"),
            max_line_bytes=80,
        )
        collector.feed(b"server ready\n")
        collector.feed(b"partial top-secret-token")

        collector.scrub()

        retained = collector.snapshot()
        self.assertIn("server ready", retained)
        self.assertIn("[REDACTED]", retained)
        self.assertNotIn("top-secret-token", retained)
        self.assertEqual(collector._sensitive_values, ())
        self.assertEqual(collector._pending, bytearray())

        collector.feed(b"top-secret-token\n")
        collector.feed(b"server stopped\n")
        collector.scrub()

        self.assertEqual(collector.snapshot(), retained)

    async def test_claimed_child_survives_post_spawn_port_failure_for_exact_reap(self):
        child = FakeChild(4101)
        spawner = ExplicitContractSpawner(child)
        reaper = FakeReaper()
        reaper.configure(child, 17)
        driver, *_ = self.make_driver(
            children=(child,),
            spawner=spawner,
            reaper=reaper,
        )
        bad_argv = list(self.argv)
        bad_argv[bad_argv.index("--port") + 1] = "not-a-port"

        with self.assertRaises(WorkerError):
            await driver.start(
                bad_argv,
                start_new_session=True,
                deadline=asyncio.get_running_loop().time() + 0.2,
                artifacts=self.receipt,
            )
        reaper.release(child)
        exits = await driver.shutdown_all(
            deadline=asyncio.get_running_loop().time() + 0.2
        )

        self.assertEqual(reaper.calls, [child])
        self.assertEqual([item.pid for item in exits], [child.pid])

    async def test_claimed_child_with_invalid_pid_or_pgid_is_still_exact_reaped(self):
        for field in ("pid", "pgid"):
            with self.subTest(field=field):
                child = FakeChild(4101)
                kwargs = {f"claimed_{field}": 0}
                spawner = ExplicitContractSpawner(child, **kwargs)
                reaper = FakeReaper()
                reaper.configure(child, 18)
                driver, *_ = self.make_driver(
                    children=(child,), spawner=spawner, reaper=reaper
                )

                with self.assertRaises(WorkerError):
                    await self.start(driver)
                reaper.release(child)
                exits = await driver.shutdown_all(
                    deadline=asyncio.get_running_loop().time() + 0.2
                )

                self.assertEqual(reaper.calls, [child])
                if field == "pid":
                    self.assertEqual(exits, ())
                else:
                    self.assertEqual([item.pid for item in exits], [child.pid])

    async def test_spawn_created_then_raised_remains_cleanup_capable(self):
        child = FakeChild(4101)
        spawner = ExplicitContractSpawner(child, raise_after_claim=True)
        reaper = FakeReaper()
        reaper.configure(child, 19)
        driver, *_ = self.make_driver(
            children=(child,), spawner=spawner, reaper=reaper
        )

        with self.assertRaisesRegex(RuntimeError, "post-spawn"):
            await self.start(driver)
        reaper.release(child)
        await driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.2)

        self.assertEqual(reaper.calls, [child])

    async def test_child_is_owned_before_a_raising_pid_accessor(self):
        class RaisingPidChild:
            alive = True

            @property
            def pid(self):
                raise RuntimeError("hostile pid accessor")

        child = RaisingPidChild()
        reaped = asyncio.Event()
        reap_calls = []

        async def reaper(candidate):
            reap_calls.append(candidate)
            await reaped.wait()
            return 21

        async def spawner(specification):
            spawner.specifications.append(specification)
            specification.spawn_ownership.bind_executed_argv(
                (
                    *specification.argv,
                    "--api-key-file",
                    "/dev/fd/99",
                    "--alias",
                    specification.readiness_alias,
                )
            )
            specification.spawn_ownership.claim_child(child)
            _ = child.pid
            raise AssertionError("unreachable")

        spawner.specifications = []
        driver, *_ = self.make_driver(
            children=(),
            spawner=spawner,
            reaper=reaper,
        )

        with self.assertRaises((AttributeError, RuntimeError)):
            await self.start(driver)
        reaped.set()
        await driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.2)

        self.assertEqual(reap_calls, [child])

    async def test_spawn_contract_requires_owned_stdout_and_stderr_drains(self):
        child = FakeChild(4101)
        spawner = ExplicitContractSpawner(child, include_drains=False)
        reaper = FakeReaper()
        reaper.configure(child, 20)
        driver, *_ = self.make_driver(
            children=(child,), spawner=spawner, reaper=reaper
        )

        with self.assertRaises(WorkerError) as caught:
            await self.start(driver)
        self.assertEqual(caught.exception.code, "invalid_worker_process")
        reaper.release(child)
        await driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.2)

        self.assertEqual(reaper.calls, [child])

    async def test_logical_and_effective_argv_hashes_are_distinct_and_bound(self):
        child = FakeChild(4101)
        spawner = ExplicitContractSpawner(child)
        driver, *_ = self.make_driver(children=(child,), spawner=spawner)

        handle = await self.start(driver)

        self.assertEqual(handle.launch_argv_sha256, canonical_argv_sha256(self.argv))
        self.assertNotEqual(
            spawner.result.executed_argv_sha256,
            handle.launch_argv_sha256,
        )
        self.assertTrue(
            driver.executed_argv_matches(
                handle,
                process_identity=handle.process_identity,
                executed_argv_sha256=spawner.result.executed_argv_sha256,
            )
        )

    async def test_effective_argv_must_bind_generation_authentication_pairs(self):
        child = FakeChild(4101)

        async def missing_auth_spawner(specification):
            missing_auth_spawner.specifications.append(specification)
            specification.spawn_ownership.bind_executed_argv(specification.argv)
            child_claim = specification.spawn_ownership.claim_child(child)
            return specification.spawn_ownership.complete(
                child_claim,
                pid=child.pid,
                pgid=child.pgid,
                stdout_drain=asyncio.create_task(asyncio.sleep(0)),
                stderr_drain=asyncio.create_task(asyncio.sleep(0)),
            )

        missing_auth_spawner.specifications = []

        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _sig: reaper.release(child)
        driver, *_ = self.make_driver(
            children=(child,),
            spawner=missing_auth_spawner,
            reaper=reaper,
            signals=signals,
        )
        caught = None
        try:
            await self.start(driver)
        except WorkerError as error:
            caught = error
        await driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.2)

        self.assertIsNotNone(caught)
        self.assertEqual(caught.code, "invalid_worker_argv")

    async def test_shutdown_reaps_claimed_child_when_inflight_spawn_then_raises(self):
        child = FakeChild(4101)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def claimed_then_raise(specification):
            claimed_then_raise.specifications.append(specification)
            entered.set()
            await release.wait()
            specification.spawn_ownership.bind_executed_argv(
                (
                    *specification.argv,
                    "--api-key-file",
                    "/dev/fd/99",
                    "--alias",
                    specification.readiness_alias,
                )
            )
            child_claim = specification.spawn_ownership.claim_child(child)
            specification.spawn_ownership.complete(
                child_claim,
                pid=child.pid,
                pgid=child.pgid,
                stdout_drain=asyncio.create_task(asyncio.sleep(0)),
                stderr_drain=asyncio.create_task(asyncio.sleep(0)),
            )
            raise RuntimeError("post-claim failure")

        claimed_then_raise.specifications = []

        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _sig: reaper.release(child)
        driver, *_ = self.make_driver(
            children=(child,),
            spawner=claimed_then_raise,
            reaper=reaper,
            signals=signals,
        )
        start_call = asyncio.create_task(self.start(driver))
        await entered.wait()
        shutdown_call = asyncio.create_task(
            driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.5)
        )
        await asyncio.sleep(0)
        release.set()
        with self.assertRaisesRegex(RuntimeError, "post-claim"):
            await start_call

        exits = await shutdown_call

        self.assertEqual([item.pid for item in exits], [child.pid])
        self.assertEqual(reaper.calls, [child])

    async def test_close_failure_before_spawn_is_retryable_and_releases_authority(self):
        close_calls = 0

        def fail_once_close():
            nonlocal close_calls
            close_calls += 1
            if close_calls == 1:
                raise RuntimeError("transient descriptor close failure")

        authority = MacOSDriverAuthority()
        revalidator = FakeArtifactRevalidator(
            changes={"model_inode": 99}, close_callback=fail_once_close
        )
        driver, *_ = self.make_driver(
            authority=authority, revalidator=revalidator
        )

        with self.assertRaises(WorkerError) as caught:
            await self.start(driver)
        self.assertEqual(caught.exception.code, "artifact_revalidation_failed")
        await driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.2)
        self.assertEqual(close_calls, 2)

        child = FakeChild(4202)
        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _sig: reaper.release(child)
        replacement, *_ = self.make_driver(
            children=(child,), authority=authority, reaper=reaper, signals=signals
        )
        await self.start(replacement)
        await replacement.shutdown_all(
            deadline=asyncio.get_running_loop().time() + 0.2
        )

    async def test_readiness_close_failure_is_retryable_without_false_ready(self):
        close_calls = 0

        def fail_once_close():
            nonlocal close_calls
            close_calls += 1
            if close_calls == 1:
                raise RuntimeError("transient descriptor close failure")

        revalidator = FakeArtifactRevalidator(close_callback=fail_once_close)
        driver, *_ = self.make_driver(revalidator=revalidator)
        handle = await self.start(driver)

        first = await driver.await_ready(
            handle,
            port=18081,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )
        second = await driver.await_ready(
            handle,
            port=18081,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )

        self.assertFalse(first)
        self.assertTrue(second)
        self.assertEqual(close_calls, 2)

    async def test_reap_close_failure_cannot_hide_exit_or_leak_authority(self):
        close_calls = 0

        def fail_once_close():
            nonlocal close_calls
            close_calls += 1
            if close_calls == 1:
                raise RuntimeError("transient descriptor close failure")

        authority = MacOSDriverAuthority()
        child = FakeChild(4101)
        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _sig: reaper.release(child)
        driver, *_ = self.make_driver(
            children=(child,),
            authority=authority,
            revalidator=FakeArtifactRevalidator(close_callback=fail_once_close),
            reaper=reaper,
            signals=signals,
        )
        handle = await self.start(driver)
        driver.terminate_group(handle, process_identity=handle.process_identity)

        exit_receipt = await driver.wait_reaped(
            handle,
            process_identity=handle.process_identity,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )

        self.assertEqual(exit_receipt.pid, child.pid)
        replacement, *_ = self.make_driver(
            children=(FakeChild(4202),), authority=authority
        )
        await self.start(replacement)
        await driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.2)
        self.assertEqual(close_calls, 2)

    async def test_transport_authority_is_opaque_exact_ready_identity(self):
        child = FakeChild(4101)
        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _sig: reaper.release(child)
        driver, *_ = self.make_driver(
            children=(child,), reaper=reaper, signals=signals
        )
        handle = await self.start(driver)

        self.assertTrue(hasattr(handle, "transport_authority"))
        self.assertNotIsInstance(
            handle.transport_authority,
            (bool, int, float, str, bytes, tuple, frozenset),
        )
        with self.assertRaises(WorkerError) as not_ready:
            driver.resolve_transport_credentials(handle.transport_authority)
        self.assertEqual(not_ready.exception.code, "stale_transport_authority")
        await driver.await_ready(
            handle,
            port=18081,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )

        credentials = driver.resolve_transport_credentials(
            handle.transport_authority
        )
        self.assertNotIn(credentials.bearer_token, repr(credentials))
        self.assertNotIn(credentials.model_alias, repr(credentials))
        with self.assertRaises(WorkerError):
            driver.resolve_transport_credentials(object())

        driver.terminate_group(handle, process_identity=handle.process_identity)
        await driver.wait_reaped(
            handle,
            process_identity=handle.process_identity,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )
        with self.assertRaises(WorkerError):
            driver.resolve_transport_credentials(handle.transport_authority)

    def test_authority_has_explicit_interprocess_lock_seam(self):
        parameters = inspect.signature(MacOSDriverAuthority).parameters
        self.assertIn("interprocess_lock", parameters)
        lock = FakeInterprocessLock()
        first_authority = MacOSDriverAuthority(interprocess_lock=lock)
        second_authority = MacOSDriverAuthority(interprocess_lock=lock)
        first = object()
        second = object()
        first_authority.reserve(first)

        with self.assertRaises(WorkerError):
            second_authority.reserve(second)
        first_authority.release(first)
        second_authority.reserve(second)
        second_authority.release(second)

        self.assertEqual(lock.acquire_calls, 3)
        self.assertEqual(lock.release_calls, [lock.lease, lock.lease])

    def test_driver_constructor_defaults_to_process_local_production_authority(self):
        parameter = inspect.signature(MacOSWorkerProcessDriver).parameters["authority"]
        self.assertIsNone(parameter.default)
        kwargs = {
            "spawn": FakeSpawner((FakeChild(1),)),
            "revalidate_artifacts": FakeArtifactRevalidator(),
            "signal_group": FakeSignals(),
            "reap_process": FakeReaper(),
            "readiness_probe": FakeReadinessProbe(),
            "process_is_live": lambda _child: True,
            "clock": lambda: 0.0,
            "token_factory": lambda: "token",
            "environment": {},
            "cwd": "/",
        }
        first = MacOSWorkerProcessDriver(**kwargs)
        second = MacOSWorkerProcessDriver(**kwargs)

        self.assertIs(first._authority, second._authority)

    async def test_second_event_loop_start_fails_closed_before_foreign_task_await(self):
        child = FakeChild(4101)
        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _sig: reaper.release(child)
        driver, *_ = self.make_driver(
            children=(child,), reaper=reaper, signals=signals
        )
        await self.start(driver)
        deadline = asyncio.get_running_loop().time() + 0.5

        def foreign_start_code():
            async def call():
                try:
                    await driver.start(
                        self.argv,
                        start_new_session=True,
                        deadline=deadline,
                        artifacts=self.receipt,
                    )
                except WorkerError as exc:
                    return exc.code
                return "unexpected-success"

            return asyncio.run(call())

        code = await asyncio.to_thread(foreign_start_code)

        self.assertEqual(code, "worker_loop_mismatch")
        await driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.2)

    async def test_cancelled_shutdown_continues_cleanup_and_retry_returns_receipt(self):
        child = FakeChild(4101)
        spawner = FakeSpawner((child,))
        spawner.block = True
        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _sig: reaper.release(child)
        driver, *_ = self.make_driver(
            children=(child,), spawner=spawner, reaper=reaper, signals=signals
        )
        start_call = asyncio.create_task(self.start(driver))
        await spawner.entered.wait()
        shutdown_call = asyncio.create_task(
            driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.5)
        )
        await asyncio.sleep(0)
        shutdown_call.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await shutdown_call

        spawner.release.set()
        await start_call
        await asyncio.sleep(0.02)
        autonomous_signals = tuple(signals.calls)
        exits = await driver.shutdown_all(
            deadline=asyncio.get_running_loop().time() + 0.2
        )

        self.assertEqual(autonomous_signals, ((4101, signal.SIGTERM),))
        self.assertEqual([item.pid for item in exits], [4101])

    async def test_concurrent_shutdown_calls_share_one_idempotent_cleanup(self):
        child = FakeChild(4101)
        reaper = FakeReaper()
        reaper.configure(child, -15)
        signals = FakeSignals()
        signals.on_signal = lambda _pgid, _sig: reaper.release(child)
        driver, *_ = self.make_driver(
            children=(child,), reaper=reaper, signals=signals
        )
        await self.start(driver)

        first, second = await asyncio.gather(
            driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.2),
            driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.2),
        )

        self.assertEqual(first, second)
        self.assertEqual(signals.calls, [(4101, signal.SIGTERM)])
        self.assertEqual(reaper.calls, [child])

    async def test_shutdown_awaits_concrete_seam_background_cleanup(self):
        class CleanupSpawner(FakeSpawner):
            def __init__(self):
                super().__init__(())
                self.cleanup_calls = 0

            async def wait_for_background_cleanup(self):
                self.cleanup_calls += 1

        class CleanupRevalidator(FakeArtifactRevalidator):
            def __init__(self):
                super().__init__()
                self.cleanup_calls = 0

            async def wait_for_background_cleanup(self):
                self.cleanup_calls += 1

        spawner = CleanupSpawner()
        revalidator = CleanupRevalidator()
        driver, *_ = self.make_driver(
            children=(),
            spawner=spawner,
            revalidator=revalidator,
        )

        self.assertEqual(
            await driver.shutdown_all(
                deadline=asyncio.get_running_loop().time() + 0.2
            ),
            (),
        )

        self.assertEqual(spawner.cleanup_calls, 1)
        self.assertEqual(revalidator.cleanup_calls, 1)

    async def test_failed_signal_is_not_marked_sent_and_can_be_retried(self):
        child = FakeChild(4101)
        calls = []

        def fail_once(request):
            calls.append((request.pgid, request.signum))
            if len(calls) == 1:
                raise OSError("signal failed")

        driver, *_ = self.make_driver(children=(child,), signals=fail_once)
        handle = await self.start(driver)

        with self.assertRaises(OSError):
            driver.terminate_group(handle, process_identity=handle.process_identity)
        driver.terminate_group(handle, process_identity=handle.process_identity)

        self.assertEqual(
            calls,
            [(4101, signal.SIGTERM), (4101, signal.SIGTERM)],
        )

    async def test_natural_exit_is_never_signalled_as_reused_group(self):
        child = FakeChild(4101)
        reaper = FakeReaper()
        reaper.configure(child, 0)
        signals = FakeSignals()
        driver, *_ = self.make_driver(
            children=(child,), reaper=reaper, signals=signals
        )
        handle = await self.start(driver)
        reaper.release(child)

        driver.terminate_group(handle, process_identity=handle.process_identity)
        receipt = await driver.wait_reaped(
            handle,
            process_identity=handle.process_identity,
            deadline=asyncio.get_running_loop().time() + 0.2,
        )

        self.assertEqual(signals.calls, [])
        self.assertEqual(receipt.returncode, 0)

    async def test_new_session_group_must_equal_pid(self):
        child = FakeChild(4101, pgid=7101)
        spawner = ExplicitContractSpawner(child)
        reaper = FakeReaper()
        reaper.configure(child, 0)
        signals = FakeSignals()
        driver, *_ = self.make_driver(
            children=(child,), spawner=spawner, reaper=reaper, signals=signals
        )

        with self.assertRaises(WorkerError) as caught:
            await self.start(driver)
        self.assertEqual(caught.exception.code, "invalid_worker_process_group")
        reaper.release(child)
        await driver.shutdown_all(deadline=asyncio.get_running_loop().time() + 0.2)

        self.assertEqual(signals.calls, [])
        self.assertEqual(reaper.calls, [child])

    async def test_start_rejects_relative_executable_and_unknown_environment(self):
        driver, *_ = self.make_driver()
        relative = ("llama-server", *self.argv[1:])
        with self.assertRaises(WorkerError) as caught:
            await driver.start(
                relative,
                start_new_session=True,
                deadline=asyncio.get_running_loop().time() + 0.2,
                artifacts=self.receipt,
            )
        self.assertEqual(caught.exception.code, "invalid_worker_argv")

        with self.assertRaises(ValueError):
            MacOSWorkerProcessDriver(
                spawn=FakeSpawner((FakeChild(1),)),
                revalidate_artifacts=FakeArtifactRevalidator(),
                signal_group=FakeSignals(),
                reap_process=FakeReaper(),
                readiness_probe=FakeReadinessProbe(),
                process_is_live=lambda _child: True,
                clock=asyncio.get_running_loop().time,
                token_factory=lambda: "token",
                authority=MacOSDriverAuthority(),
                environment={"TFY_API_KEY": "must-not-cross"},
                cwd="/",
            )


if __name__ == "__main__":
    unittest.main()
