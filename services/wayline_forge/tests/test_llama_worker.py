import asyncio
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest

from services.wayline_forge.app.llama_worker import (
    ArtifactVerificationReceipt,
    GenerationLease,
    ManagedLlamaWorker,
    ProcessExit,
    StdlibArtifactVerifier,
    WorkerLaunchAuthority,
    WorkerError,
    WorkerEpochReceipt,
    WorkerLaunchSpec,
    WorkerState,
    WorkerStopReceipt,
    canonical_argv_sha256,
)


class FakeProcess:
    def __init__(
        self,
        pid,
        *,
        process_identity=None,
        transport_authority=None,
        artifacts=None,
        argv_sha256=None,
    ):
        self.pid = pid
        self.process_identity = (
            object() if process_identity is None else process_identity
        )
        self.transport_authority = (
            object() if transport_authority is None else transport_authority
        )
        self.launch_artifacts = artifacts
        self.launch_argv_sha256 = argv_sha256


class DriverIssuedProcess:
    def __init__(
        self,
        pid,
        *,
        process_identity,
        transport_authority,
        artifacts,
        argv_sha256,
    ):
        self.pid = pid
        self.process_identity = process_identity
        self.transport_authority = transport_authority
        self.launch_artifacts = artifacts
        self.launch_argv_sha256 = argv_sha256


class FakeTransportCredentials:
    __slots__ = ("bearer_token", "model_alias")

    def __init__(self):
        self.bearer_token = "private-worker-bearer"
        self.model_alias = "private-worker-alias"

    def __repr__(self):
        return "FakeTransportCredentials(<redacted>)"


class FakeArtifactVerifier:
    async def verify(self, launch_spec, *, deadline):
        return ArtifactVerificationReceipt(
            binary_path=launch_spec.binary_path,
            model_path=launch_spec.model_path,
            binary_sha256=launch_spec.binary_sha256,
            model_sha256=launch_spec.model_sha256,
            binary_size=1,
            model_size=1,
            binary_device=1,
            binary_inode=1,
            model_device=1,
            model_inode=2,
        )


class FakeProcessDriver:
    def __init__(
        self,
        *,
        pids=(4101, 4102),
        wait_results=(),
        ready_result=True,
        ready_error=None,
        start_error=None,
        terminate_error=None,
        kill_error=None,
    ):
        self.pids = list(pids)
        self.wait_results = list(wait_results)
        self.ready_result = ready_result
        self.ready_error = ready_error
        self.start_error = start_error
        self.terminate_error = terminate_error
        self.kill_error = kill_error
        self.starts = []
        self.ready_calls = []
        self.terms = []
        self.kills = []
        self.waits = []
        self.wait_started = asyncio.Event()
        self.wait_release = asyncio.Event()
        self.block_wait = False
        self.identities = {}
        self.transport_authorities = {}
        self.transport_resolutions = []

    async def start(
        self,
        argv,
        *,
        start_new_session,
        deadline,
        artifacts,
    ):
        if self.start_error is not None:
            raise self.start_error
        transport_authority = object()
        process = FakeProcess(
            self.pids.pop(0),
            transport_authority=transport_authority,
            artifacts=artifacts,
            argv_sha256=canonical_argv_sha256(argv),
        )
        self.identities[id(process)] = process.process_identity
        self.transport_authorities[id(process)] = transport_authority
        self.starts.append(
            (process, tuple(argv), start_new_session, artifacts)
        )
        return process

    async def await_ready(self, process, *, port, deadline):
        self.ready_calls.append((process.pid, port, deadline))
        if self.ready_error is not None:
            raise self.ready_error
        return self.ready_result

    def resolve_transport_credentials(self, transport_authority):
        self.transport_resolutions.append(transport_authority)
        if not any(
            candidate is transport_authority
            for candidate in self.transport_authorities.values()
        ):
            raise WorkerError("stale_transport_authority")
        return FakeTransportCredentials()

    def _validate_identity(self, process, process_identity):
        if self.identities.get(id(process)) is not process_identity:
            raise RuntimeError("stale process identity")

    def terminate_group(self, process, *, process_identity):
        self._validate_identity(process, process_identity)
        self.terms.append(process.pid)
        if self.terminate_error is not None:
            raise self.terminate_error

    def kill_group(self, process, *, process_identity):
        self._validate_identity(process, process_identity)
        self.kills.append(process.pid)
        if self.kill_error is not None:
            raise self.kill_error

    async def wait_reaped(self, process, *, process_identity, deadline):
        self._validate_identity(process, process_identity)
        self.waits.append((process.pid, deadline))
        self.wait_started.set()
        if self.block_wait:
            await self.wait_release.wait()
        if self.wait_results:
            result = self.wait_results.pop(0)
            if callable(result):
                result = result(process, process_identity)
            if result is not None:
                self.transport_authorities.pop(id(process), None)
            return result
        self.transport_authorities.pop(id(process), None)
        return ProcessExit(
            pid=process.pid,
            returncode=-15,
            process_identity=process_identity,
        )


class CrossThreadBlockingStartDriver(FakeProcessDriver):
    def __init__(self):
        super().__init__(pids=(6101, 6102))
        self.start_entered = threading.Event()
        self.second_start_entered = threading.Event()
        self.start_release = threading.Event()
        self._start_guard = threading.Lock()

    async def start(
        self,
        argv,
        *,
        start_new_session,
        deadline,
        artifacts,
    ):
        with self._start_guard:
            transport_authority = object()
            process = FakeProcess(
                self.pids.pop(0),
                transport_authority=transport_authority,
                artifacts=artifacts,
                argv_sha256=canonical_argv_sha256(argv),
            )
            self.identities[id(process)] = process.process_identity
            self.transport_authorities[id(process)] = transport_authority
            self.starts.append(
                (process, tuple(argv), start_new_session, artifacts)
            )
            if len(self.starts) == 1:
                self.start_entered.set()
            else:
                self.second_start_entered.set()
        await asyncio.to_thread(self.start_release.wait)
        return process


class StaleHandleExitDriver(FakeProcessDriver):
    def __init__(self):
        super().__init__()
        self.wait_tokens = []

    async def wait_reaped(self, process, *, process_identity, deadline):
        self._validate_identity(process, process_identity)
        self.wait_tokens.append(process_identity)
        return ProcessExit(
            pid=process.pid,
            returncode=-15,
            process_identity=object(),
        )


class RaisingPidProcess:
    def __init__(self, *, transport_authority, artifacts, argv_sha256):
        self.process_identity = object()
        self.transport_authority = transport_authority
        self.launch_artifacts = artifacts
        self.launch_argv_sha256 = argv_sha256

    @property
    def pid(self):
        raise RuntimeError("private hostile pid detail")


class RaisingPidDriver(FakeProcessDriver):
    async def start(
        self,
        argv,
        *,
        start_new_session,
        deadline,
        artifacts,
    ):
        transport_authority = object()
        process = RaisingPidProcess(
            transport_authority=transport_authority,
            artifacts=artifacts,
            argv_sha256=canonical_argv_sha256(argv),
        )
        self.identities[id(process)] = process.process_identity
        self.transport_authorities[id(process)] = transport_authority
        self.starts.append(
            (process, tuple(argv), start_new_session, artifacts)
        )
        return process


class InvalidPidDriver(FakeProcessDriver):
    def __init__(self):
        super().__init__(pids=(0,))


class IdentityRequiredDriver(FakeProcessDriver):
    def __init__(self, *, bind_artifacts=True, bind_argv=True):
        super().__init__()
        self.bind_artifacts = bind_artifacts
        self.bind_argv = bind_argv
        self.identities = {}
        self.signal_identities = []

    async def start(
        self,
        argv,
        *,
        start_new_session,
        deadline,
        artifacts,
    ):
        identity = object()
        transport_authority = object()
        bound_artifacts = artifacts if self.bind_artifacts else replace(artifacts)
        process = DriverIssuedProcess(
            self.pids.pop(0),
            process_identity=identity,
            transport_authority=transport_authority,
            artifacts=bound_artifacts,
            argv_sha256=(
                canonical_argv_sha256(argv)
                if self.bind_argv
                else "0" * 64
            ),
        )
        self.identities[id(process)] = identity
        self.transport_authorities[id(process)] = transport_authority
        self.starts.append((process, tuple(argv), start_new_session, artifacts))
        return process

    def _validate(self, process, process_identity):
        if self.identities.get(id(process)) is not process_identity:
            raise RuntimeError("stale process identity")
        self.signal_identities.append(process_identity)

    def terminate_group(self, process, *, process_identity):
        self._validate(process, process_identity)
        self.terms.append(process.pid)

    def kill_group(self, process, *, process_identity):
        self._validate(process, process_identity)
        self.kills.append(process.pid)

    async def wait_reaped(self, process, *, process_identity, deadline):
        self._validate(process, process_identity)
        self.waits.append((process.pid, deadline))
        return ProcessExit(process.pid, -15, process_identity)


class CorruptArtifactVerifier(FakeArtifactVerifier):
    def __init__(self, **changes):
        self.changes = changes

    async def verify(self, launch_spec, *, deadline):
        receipt = await super().verify(launch_spec, deadline=deadline)
        return replace(receipt, **self.changes)


class BlockingLifecycleDriver(FakeProcessDriver):
    def __init__(self, *, block_start=False, block_ready=False):
        super().__init__()
        self.block_start = block_start
        self.block_ready = block_ready
        self.start_started = asyncio.Event()
        self.start_release = asyncio.Event()
        self.ready_started = asyncio.Event()
        self.ready_release = asyncio.Event()

    async def start(
        self,
        argv,
        *,
        start_new_session,
        deadline,
        artifacts,
    ):
        self.start_started.set()
        if self.block_start:
            await self.start_release.wait()
        return await super().start(
            argv,
            start_new_session=start_new_session,
            deadline=deadline,
            artifacts=artifacts,
        )

    async def await_ready(self, process, *, port, deadline):
        self.ready_started.set()
        if self.block_ready:
            await self.ready_release.wait()
        return await super().await_ready(
            process,
            port=port,
            deadline=deadline,
        )


class CancellationResistantStartDriver(FakeProcessDriver):
    def __init__(self, *, wait_results=()):
        super().__init__(wait_results=wait_results)
        self.start_started = asyncio.Event()
        self.start_cancelled = asyncio.Event()
        self.start_release = asyncio.Event()

    async def start(
        self,
        argv,
        *,
        start_new_session,
        deadline,
        artifacts,
    ):
        self.start_started.set()
        try:
            await self.start_release.wait()
        except asyncio.CancelledError:
            self.start_cancelled.set()
            await self.start_release.wait()
        return await super().start(
            argv,
            start_new_session=start_new_session,
            deadline=deadline,
            artifacts=artifacts,
        )


class CancellationResistantReapDriver(FakeProcessDriver):
    def __init__(self):
        super().__init__()
        self.reap_cancelled = asyncio.Event()
        self.reap_release = asyncio.Event()

    async def wait_reaped(self, process, *, process_identity, deadline):
        self._validate_identity(process, process_identity)
        self.waits.append((process.pid, deadline))
        self.wait_started.set()
        try:
            await self.reap_release.wait()
        except asyncio.CancelledError:
            self.reap_cancelled.set()
            await self.reap_release.wait()
        return ProcessExit(process.pid, -15, process_identity)


class ManualClock:
    def __init__(self, now=0.0):
        self.now = float(now)

    def __call__(self):
        return self.now


class ClosedLoop:
    def create_task(self, coroutine):
        raise RuntimeError("event loop is closed")


class CompletedDriverTask:
    def __init__(self, result):
        self._result = result

    def add_done_callback(self, callback):
        callback(self)

    def result(self):
        return self._result

    def get_loop(self):
        return ClosedLoop()


class RejectSpawnMarkAuthority(WorkerLaunchAuthority):
    def mark_spawned(self, owner):
        raise WorkerError("worker_unsafe_state")


class SecretReprAuthority:
    def __repr__(self):
        return "PRIVATE_TRANSPORT_AUTHORITY"


class SecretAuthorityDriver(FakeProcessDriver):
    async def start(
        self,
        argv,
        *,
        start_new_session,
        deadline,
        artifacts,
    ):
        process = await super().start(
            argv,
            start_new_session=start_new_session,
            deadline=deadline,
            artifacts=artifacts,
        )
        authority = SecretReprAuthority()
        process.transport_authority = authority
        self.transport_authorities[id(process)] = authority
        return process


class MissingTransportAuthorityDriver(FakeProcessDriver):
    async def start(
        self,
        argv,
        *,
        start_new_session,
        deadline,
        artifacts,
    ):
        process = await super().start(
            argv,
            start_new_session=start_new_session,
            deadline=deadline,
            artifacts=artifacts,
        )
        del process.transport_authority
        return process


class ReplacedTransportAuthorityDriver(FakeProcessDriver):
    async def start(
        self,
        argv,
        *,
        start_new_session,
        deadline,
        artifacts,
    ):
        process = await super().start(
            argv,
            start_new_session=start_new_session,
            deadline=deadline,
            artifacts=artifacts,
        )
        process.transport_authority = object()
        return process


class PrimitiveTransportAuthorityDriver(FakeProcessDriver):
    async def start(
        self,
        argv,
        *,
        start_new_session,
        deadline,
        artifacts,
    ):
        process = await super().start(
            argv,
            start_new_session=start_new_session,
            deadline=deadline,
            artifacts=artifacts,
        )
        process.transport_authority = "forged-transport-authority"
        self.transport_authorities[id(process)] = process.transport_authority
        return process


class Ids:
    def __init__(self, prefix):
        self.prefix = prefix
        self.count = 0

    def __call__(self):
        self.count += 1
        return f"{self.prefix}-{self.count}"


class ManagedLlamaWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.spec = WorkerLaunchSpec(
            binary_path="/Applications/Wayline/llama-server",
            model_path="/Applications/Wayline/wayline.gguf",
            binary_sha256="a" * 64,
            model_sha256="b" * 64,
            extra_args=("--ctx-size", "2048"),
        )

    def worker(self, driver):
        ports = iter((18081, 18082, 18083))
        return ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=Ids("epoch"),
            generation_id_factory=Ids("generation"),
            port_factory=lambda: next(ports),
            term_grace_seconds=0.25,
            launch_authority=WorkerLaunchAuthority(),
        )

    async def test_stdlib_artifact_verifier_hashes_files_before_any_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary_path = Path(temporary) / "llama-server"
            model_path = Path(temporary) / "wayline.gguf"
            binary_bytes = b"verified llama binary"
            model_bytes = b"verified gguf model"
            binary_path.write_bytes(binary_bytes)
            model_path.write_bytes(model_bytes)
            spec = WorkerLaunchSpec(
                binary_path=str(binary_path),
                model_path=str(model_path),
                binary_sha256=hashlib.sha256(binary_bytes).hexdigest(),
                model_sha256=hashlib.sha256(model_bytes).hexdigest(),
            )
            driver = FakeProcessDriver()
            worker = ManagedLlamaWorker(
                driver=driver,
                artifact_verifier=StdlibArtifactVerifier(),
                launch_spec=spec,
                clock=self.clock,
                epoch_id_factory=Ids("artifact-epoch"),
                generation_id_factory=Ids("artifact-generation"),
                port_factory=lambda: 18081,
                launch_authority=WorkerLaunchAuthority(),
            )

            epoch = await worker.begin_preparation(deadline=2.0)

            self.assertEqual(epoch.binary_sha256, spec.binary_sha256)
            self.assertEqual(epoch.model_sha256, spec.model_sha256)
            self.assertEqual(len(driver.starts), 1)
            artifact_receipt = driver.starts[0][3]
            self.assertIsInstance(
                artifact_receipt,
                ArtifactVerificationReceipt,
            )
            self.assertEqual(artifact_receipt.binary_path, spec.binary_path)
            self.assertEqual(artifact_receipt.model_path, spec.model_path)

            mismatched = replace(spec, model_sha256="f" * 64)
            mismatched_driver = FakeProcessDriver()
            mismatched_worker = ManagedLlamaWorker(
                driver=mismatched_driver,
                artifact_verifier=StdlibArtifactVerifier(),
                launch_spec=mismatched,
                clock=self.clock,
                epoch_id_factory=Ids("mismatch-epoch"),
                generation_id_factory=Ids("mismatch-generation"),
                port_factory=lambda: 18082,
                launch_authority=WorkerLaunchAuthority(),
            )
            with self.assertRaises(WorkerError) as caught:
                await mismatched_worker.begin_preparation(deadline=2.0)
            self.assertEqual(caught.exception.code, "artifact_digest_mismatch")
            self.assertEqual(mismatched_driver.starts, [])

    def test_worker_construction_requires_artifact_authority(self):
        with self.assertRaises(ValueError):
            ManagedLlamaWorker(
                driver=FakeProcessDriver(),
                artifact_verifier=None,
                launch_spec=self.spec,
                clock=self.clock,
                epoch_id_factory=Ids("epoch"),
                generation_id_factory=Ids("generation"),
                port_factory=lambda: 18081,
            )

        class SyncStartDriver(FakeProcessDriver):
            def start(
                self,
                argv,
                *,
                start_new_session,
                deadline,
                artifacts,
            ):
                return FakeProcess(9999)

        with self.assertRaises(ValueError):
            ManagedLlamaWorker(
                driver=SyncStartDriver(),
                artifact_verifier=FakeArtifactVerifier(),
                launch_spec=self.spec,
                clock=self.clock,
                epoch_id_factory=Ids("epoch"),
                generation_id_factory=Ids("generation"),
                port_factory=lambda: 18081,
            )

        class PidOnlySignalDriver(FakeProcessDriver):
            def terminate_group(self, process):
                return None

        with self.assertRaises(ValueError):
            ManagedLlamaWorker(
                driver=PidOnlySignalDriver(),
                artifact_verifier=FakeArtifactVerifier(),
                launch_spec=self.spec,
                clock=self.clock,
                epoch_id_factory=Ids("epoch"),
                generation_id_factory=Ids("generation"),
                port_factory=lambda: 18081,
                launch_authority=WorkerLaunchAuthority(),
            )

        missing_resolver = FakeProcessDriver()
        missing_resolver.resolve_transport_credentials = None
        with self.assertRaises(ValueError):
            ManagedLlamaWorker(
                driver=missing_resolver,
                artifact_verifier=FakeArtifactVerifier(),
                launch_spec=self.spec,
                clock=self.clock,
                epoch_id_factory=Ids("epoch"),
                generation_id_factory=Ids("generation"),
                port_factory=lambda: 18081,
                launch_authority=WorkerLaunchAuthority(),
            )

    def test_cross_thread_begin_launches_at_most_one_child(self):
        driver = CrossThreadBlockingStartDriver()
        epoch_counter = iter(range(1, 4))
        port_counter = iter((18081, 18082, 18083))
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=time.monotonic,
            epoch_id_factory=lambda: f"thread-epoch-{next(epoch_counter)}",
            generation_id_factory=Ids("thread-generation"),
            port_factory=lambda: next(port_counter),
            launch_authority=WorkerLaunchAuthority(),
        )
        outcomes = []
        outcome_guard = threading.Lock()

        def run_begin():
            try:
                result = asyncio.run(
                    worker.begin_preparation(deadline=time.monotonic() + 1.0)
                )
            except BaseException as error:
                result = error
            with outcome_guard:
                outcomes.append(result)

        first = threading.Thread(target=run_begin, daemon=True)
        second = threading.Thread(target=run_begin, daemon=True)
        first.start()
        self.assertTrue(driver.start_entered.wait(timeout=0.5))
        second.start()
        second.join(timeout=0.1)
        second_finished_before_release = not second.is_alive()
        driver.start_release.set()
        first.join(timeout=1.0)
        second.join(timeout=1.0)

        self.assertTrue(second_finished_before_release)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(driver.starts), 1)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(
            sum(isinstance(outcome, WorkerEpochReceipt) for outcome in outcomes),
            1,
        )
        failures = [
            outcome for outcome in outcomes if isinstance(outcome, WorkerError)
        ]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "worker_unsafe_state")

    async def test_stale_handle_exit_cannot_acknowledge_reap(self):
        driver = StaleHandleExitDriver()
        worker = self.worker(driver)
        await worker.begin_preparation(deadline=2.0)
        lease = await worker.acquire("4" * 64, ready_deadline=2.0)

        with self.assertRaises(WorkerError) as caught:
            await worker.abort(lease, reason="live_deadline", deadline=2.0)

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(worker.state, WorkerState.QUARANTINED)
        self.assertEqual(len(driver.wait_tokens), 2)
        self.assertIs(driver.wait_tokens[0], driver.wait_tokens[1])
        self.assertEqual(worker.active_lease, lease)

    async def test_boolean_pid_exit_cannot_acknowledge_exact_child_reap(self):
        driver = FakeProcessDriver(
            pids=(1,),
            wait_results=(
                lambda _process, identity: ProcessExit(True, -15, identity),
                lambda _process, identity: ProcessExit(True, -9, identity),
            ),
        )
        worker = self.worker(driver)
        await worker.begin_preparation(deadline=2.0)
        lease = await worker.acquire("4" * 64, ready_deadline=2.0)

        with self.assertRaises(WorkerError) as caught:
            await worker.abort(lease, reason="invalid-exit", deadline=2.0)

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(worker.state, WorkerState.QUARANTINED)

    async def test_driver_issued_identity_is_required_for_every_signal_and_reap(self):
        driver = IdentityRequiredDriver()
        worker = self.worker(driver)
        await worker.begin_preparation(deadline=2.0)
        process = driver.starts[0][0]
        lease = await worker.acquire("4" * 64, ready_deadline=2.0)

        receipt = await worker.abort(
            lease,
            reason="identity-test",
            deadline=2.0,
        )

        self.assertEqual(receipt.pid, process.pid)
        self.assertGreaterEqual(len(driver.signal_identities), 2)
        self.assertTrue(
            all(
                identity is process.process_identity
                for identity in driver.signal_identities
            )
        )

    async def test_launch_handle_must_bind_exact_verified_handoff(self):
        drivers = (
            IdentityRequiredDriver(bind_artifacts=False),
            IdentityRequiredDriver(bind_argv=False),
        )
        for driver in drivers:
            with self.subTest(driver=driver):
                worker = self.worker(driver)
                with self.assertRaises(WorkerError) as caught:
                    await worker.begin_preparation(deadline=2.0)

                self.assertEqual(caught.exception.code, "worker_quarantined")
                self.assertEqual(worker.state, WorkerState.QUARANTINED)
                self.assertTrue(worker.cleanup_authority_retained)

    async def test_hostile_pid_property_is_sanitized_and_quarantined(self):
        driver = RaisingPidDriver()
        worker = self.worker(driver)

        with self.assertRaises(WorkerError) as caught:
            await worker.begin_preparation(deadline=2.0)

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(str(caught.exception), "worker_quarantined")
        self.assertEqual(worker.state, WorkerState.QUARANTINED)

    async def test_invalid_pid_handle_is_cleaned_or_retained_for_cleanup(self):
        driver = InvalidPidDriver()
        worker = self.worker(driver)

        with self.assertRaises(WorkerError):
            await worker.begin_preparation(deadline=2.0)

        self.assertTrue(
            driver.terms and driver.waits
            or worker.cleanup_authority_retained
        )
        if not driver.waits:
            self.assertEqual(worker.state, WorkerState.QUARANTINED)

    async def test_spawn_handle_is_retained_before_authority_transition(self):
        driver = FakeProcessDriver()
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=Ids("transition-epoch"),
            generation_id_factory=Ids("transition-generation"),
            port_factory=lambda: 18301,
            launch_authority=RejectSpawnMarkAuthority(),
        )

        with self.assertRaises(WorkerError) as caught:
            await worker.begin_preparation(deadline=2.0)

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(worker.state, WorkerState.QUARANTINED)
        self.assertTrue(worker.cleanup_authority_retained)

    async def test_raising_pid_handle_is_retained_when_cleanup_cannot_be_proven(self):
        driver = RaisingPidDriver()
        worker = self.worker(driver)

        with self.assertRaises(WorkerError):
            await worker.begin_preparation(deadline=2.0)

        self.assertEqual(worker.state, WorkerState.QUARANTINED)
        self.assertTrue(worker.cleanup_authority_retained)

    async def test_outer_deadline_bounds_driver_start_without_handle(self):
        driver = BlockingLifecycleDriver(block_start=True)
        loop = asyncio.get_running_loop()
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=loop.time,
            epoch_id_factory=Ids("deadline-epoch"),
            generation_id_factory=Ids("deadline-generation"),
            port_factory=lambda: 18081,
            term_grace_seconds=0.01,
            launch_authority=WorkerLaunchAuthority(),
        )
        call = asyncio.create_task(
            worker.begin_preparation(deadline=loop.time() + 0.25)
        )
        await driver.start_started.wait()
        done, _pending = await asyncio.wait({call}, timeout=0.75)
        finished_before_release = call in done
        driver.start_release.set()
        outcome = await asyncio.gather(call, return_exceptions=True)

        self.assertTrue(finished_before_release)
        self.assertIsInstance(outcome[0], WorkerError)
        self.assertEqual(outcome[0].code, "worker_quarantined")
        self.assertEqual(worker.state, WorkerState.QUARANTINED)

    async def test_late_start_handle_is_terminated_after_timeout(self):
        driver = CancellationResistantStartDriver(
            wait_results=(
                None,
                lambda process, token: ProcessExit(process.pid, -9, token),
            )
        )
        loop = asyncio.get_running_loop()
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=loop.time,
            epoch_id_factory=Ids("late-epoch"),
            generation_id_factory=Ids("late-generation"),
            port_factory=lambda: 18081,
            term_grace_seconds=0.02,
            launch_authority=WorkerLaunchAuthority(),
        )
        with self.assertRaises(WorkerError) as caught:
            await worker.begin_preparation(deadline=loop.time() + 0.25)
        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertFalse(driver.start_cancelled.is_set())

        driver.start_release.set()
        for _turn in range(50):
            if len(driver.waits) >= 2:
                break
            await asyncio.sleep(0)

        self.assertEqual(driver.terms, [4101])
        self.assertEqual(driver.kills, [4101])
        self.assertEqual(len(driver.waits), 2)
        self.assertEqual(worker.state, WorkerState.QUARANTINED)

    async def test_outer_deadline_stops_handle_when_readiness_ignores_deadline(self):
        driver = BlockingLifecycleDriver(block_ready=True)
        loop = asyncio.get_running_loop()
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=loop.time,
            epoch_id_factory=Ids("ready-deadline-epoch"),
            generation_id_factory=Ids("ready-deadline-generation"),
            port_factory=lambda: 18081,
            term_grace_seconds=0.01,
            launch_authority=WorkerLaunchAuthority(),
        )
        call = asyncio.create_task(
            worker.begin_preparation(deadline=loop.time() + 0.25)
        )
        await driver.ready_started.wait()
        done, _pending = await asyncio.wait({call}, timeout=0.75)
        finished_before_release = call in done
        driver.ready_release.set()
        outcome = await asyncio.gather(call, return_exceptions=True)

        self.assertTrue(finished_before_release)
        self.assertIsInstance(outcome[0], WorkerError)
        self.assertIn(
            outcome[0].code,
            {"worker_not_ready", "worker_quarantined"},
        )
        self.assertTrue(driver.terms)
        self.assertIn(worker.state, {WorkerState.STOPPED, WorkerState.QUARANTINED})

    async def test_outer_deadline_bounds_reap_when_driver_ignores_deadline(self):
        driver = FakeProcessDriver()
        driver.block_wait = True
        loop = asyncio.get_running_loop()
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=loop.time,
            epoch_id_factory=Ids("reap-deadline-epoch"),
            generation_id_factory=Ids("reap-deadline-generation"),
            port_factory=lambda: 18081,
            term_grace_seconds=0.01,
            launch_authority=WorkerLaunchAuthority(),
        )
        await worker.begin_preparation(deadline=loop.time() + 0.5)
        lease = await worker.acquire(
            "1" * 64,
            ready_deadline=loop.time() + 0.5,
        )
        call = asyncio.create_task(
            worker.abort(
                lease,
                reason="live_deadline",
                deadline=loop.time() + 0.25,
            )
        )
        await driver.wait_started.wait()
        done, _pending = await asyncio.wait({call}, timeout=0.75)
        finished_before_release = call in done
        driver.wait_release.set()
        outcome = await asyncio.gather(call, return_exceptions=True)

        self.assertTrue(finished_before_release)
        self.assertIsInstance(outcome[0], WorkerError)
        self.assertEqual(outcome[0].code, "worker_quarantined")
        self.assertEqual(worker.state, WorkerState.QUARANTINED)

    async def test_reap_deadline_does_not_wait_for_cancellation_resistant_driver(self):
        driver = CancellationResistantReapDriver()
        loop = asyncio.get_running_loop()
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=loop.time,
            epoch_id_factory=Ids("resistant-reap-epoch"),
            generation_id_factory=Ids("resistant-reap-generation"),
            port_factory=lambda: 18081,
            term_grace_seconds=0.01,
            launch_authority=WorkerLaunchAuthority(),
        )
        await worker.begin_preparation(deadline=loop.time() + 0.5)
        lease = await worker.acquire(
            "0" * 64,
            ready_deadline=loop.time() + 0.5,
        )
        call = asyncio.create_task(
            worker.abort(
                lease,
                reason="live_deadline",
                deadline=loop.time() + 0.25,
            )
        )
        await driver.wait_started.wait()
        done, _pending = await asyncio.wait({call}, timeout=0.75)
        self.assertIn(call, done)
        outcome = await asyncio.gather(call, return_exceptions=True)
        self.assertIsInstance(outcome[0], WorkerError)
        self.assertEqual(outcome[0].code, "worker_quarantined")
        self.assertTrue(driver.reap_cancelled.is_set())
        self.assertEqual(worker.state, WorkerState.QUARANTINED)

        driver.reap_release.set()
        await asyncio.sleep(0)

    async def test_epoch_and_lease_state_machine_confirms_natural_completion(self):
        driver = FakeProcessDriver()
        worker = self.worker(driver)

        epoch = await worker.begin_preparation(deadline=2.0)
        self.assertEqual(worker.state, WorkerState.READY_IDLE)
        self.assertEqual(epoch.epoch_id, "epoch-1")
        self.assertEqual(epoch.pid, 4101)
        self.assertEqual(epoch.port, 18081)
        self.assertEqual(epoch.binary_sha256, "a" * 64)
        self.assertEqual(epoch.model_sha256, "b" * 64)

        lease = await worker.acquire("c" * 64, ready_deadline=2.0)
        self.assertEqual(worker.state, WorkerState.BUSY)
        self.assertEqual(lease.epoch_id, epoch.epoch_id)
        self.assertEqual(lease.endpoint, "http://127.0.0.1:18081/v1/chat/completions")
        await worker.confirm_complete(lease)

        self.assertEqual(worker.state, WorkerState.READY_IDLE)
        self.assertEqual(driver.terms, [])
        self.assertEqual(driver.kills, [])
        self.assertEqual(driver.waits, [])

    async def test_driver_transport_authority_is_exactly_bound_and_repr_secret(self):
        driver = SecretAuthorityDriver()
        worker = self.worker(driver)

        epoch = await worker.begin_preparation(deadline=2.0)
        process = driver.starts[0][0]
        lease = await worker.acquire("c" * 64, ready_deadline=2.0)

        self.assertTrue(hasattr(lease, "transport_authority"))
        self.assertIs(
            lease.transport_authority,
            process.transport_authority,
        )
        self.assertIs(
            worker.transport_authority_for(lease),
            process.transport_authority,
        )
        self.assertGreaterEqual(len(driver.transport_resolutions), 2)
        self.assertTrue(
            all(
                authority is process.transport_authority
                for authority in driver.transport_resolutions
            )
        )
        self.assertNotIn("transport_authority", repr(lease))
        self.assertNotIn("PRIVATE_TRANSPORT_AUTHORITY", repr(lease))
        self.assertNotIn("PRIVATE_TRANSPORT_AUTHORITY", repr(epoch))
        self.assertFalse(hasattr(epoch, "transport_authority"))

        await worker.confirm_complete(lease)

    async def test_replaced_transport_authority_is_reaped_before_any_lease(self):
        driver = ReplacedTransportAuthorityDriver()
        worker = self.worker(driver)

        with self.assertRaises(WorkerError) as caught:
            await worker.begin_preparation(deadline=2.0)

        self.assertEqual(caught.exception.code, "stale_transport_authority")
        self.assertEqual(worker.state, WorkerState.STOPPED)
        self.assertIsNone(worker.active_lease)
        self.assertEqual(driver.terms, [4101])
        self.assertEqual([pid for pid, _deadline in driver.waits], [4101])

    async def test_missing_or_primitive_transport_authority_never_becomes_ready(self):
        for driver in (
            MissingTransportAuthorityDriver(),
            PrimitiveTransportAuthorityDriver(),
        ):
            with self.subTest(driver=type(driver).__name__):
                worker = self.worker(driver)
                with self.assertRaises(WorkerError) as caught:
                    await worker.begin_preparation(deadline=2.0)

                self.assertEqual(caught.exception.code, "worker_quarantined")
                self.assertEqual(worker.state, WorkerState.QUARANTINED)
                self.assertIsNone(worker.active_lease)

    async def test_lease_transport_authority_copy_is_rejected_before_use(self):
        worker = self.worker(FakeProcessDriver())
        await worker.begin_preparation(deadline=2.0)
        lease = await worker.acquire("c" * 64, ready_deadline=2.0)
        self.assertTrue(hasattr(lease, "transport_authority"))
        forged = replace(lease, transport_authority=object())

        with self.assertRaises(WorkerError) as caught:
            worker.transport_authority_for(forged)

        self.assertEqual(caught.exception.code, "stale_generation_lease")
        self.assertIs(worker.active_lease, lease)
        await worker.confirm_complete(lease)

    async def test_equal_value_lease_copy_cannot_control_generation(self):
        worker = self.worker(FakeProcessDriver())
        await worker.begin_preparation(deadline=2.0)
        lease = await worker.acquire("c" * 64, ready_deadline=2.0)
        forged = replace(lease)
        self.assertNotEqual(forged, lease)

        with self.assertRaises(WorkerError) as caught:
            await worker.confirm_complete(forged)

        self.assertEqual(caught.exception.code, "stale_generation_lease")
        self.assertIs(worker.active_lease, lease)

    async def test_acquire_distinguishes_stopped_from_unsafe_states(self):
        stopped = self.worker(FakeProcessDriver())
        with self.assertRaises(WorkerError) as stopped_error:
            await stopped.acquire("a" * 64, ready_deadline=2.0)
        self.assertEqual(stopped_error.exception.code, "worker_not_ready")

        busy = self.worker(FakeProcessDriver())
        await busy.begin_preparation(deadline=2.0)
        await busy.acquire("b" * 64, ready_deadline=2.0)
        with self.assertRaises(WorkerError) as busy_error:
            await busy.acquire("c" * 64, ready_deadline=2.0)
        self.assertEqual(busy_error.exception.code, "worker_unsafe_state")

        starting_driver = BlockingLifecycleDriver(block_start=True)
        loop = asyncio.get_running_loop()
        starting = ManagedLlamaWorker(
            driver=starting_driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=loop.time,
            epoch_id_factory=Ids("starting-epoch"),
            generation_id_factory=Ids("starting-generation"),
            port_factory=lambda: 18091,
            launch_authority=WorkerLaunchAuthority(),
        )
        start_task = asyncio.create_task(
            starting.begin_preparation(deadline=loop.time() + 1.0)
        )
        await starting_driver.start_started.wait()
        with self.assertRaises(WorkerError) as starting_error:
            await starting.acquire(
                "d" * 64,
                ready_deadline=loop.time() + 1.0,
            )
        self.assertEqual(starting_error.exception.code, "worker_unsafe_state")
        starting_driver.start_release.set()
        await start_task

        stopping_driver = FakeProcessDriver()
        stopping_driver.block_wait = True
        stopping = self.worker(stopping_driver)
        await stopping.begin_preparation(deadline=2.0)
        lease = await stopping.acquire("e" * 64, ready_deadline=2.0)
        stop_task = asyncio.create_task(
            stopping.abort(lease, reason="test", deadline=2.0)
        )
        await stopping_driver.wait_started.wait()
        with self.assertRaises(WorkerError) as stopping_error:
            await stopping.acquire("f" * 64, ready_deadline=2.0)
        self.assertEqual(stopping_error.exception.code, "worker_unsafe_state")
        stopping_driver.wait_release.set()
        await stop_task

    async def test_term_resistance_escalates_to_kill_and_reaps_before_release(self):
        driver = FakeProcessDriver(
            wait_results=(
                None,
                lambda process, token: ProcessExit(process.pid, -9, token),
            ),
        )
        worker = self.worker(driver)
        epoch = await worker.begin_preparation(deadline=2.0)
        lease = await worker.acquire("d" * 64, ready_deadline=2.0)

        receipt = await worker.abort(
            lease,
            reason="live_deadline",
            deadline=2.0,
        )

        self.assertEqual(worker.state, WorkerState.STOPPED)
        self.assertEqual(receipt.epoch_id, epoch.epoch_id)
        self.assertEqual(receipt.generation_id, lease.generation_id)
        self.assertEqual(receipt.pid, epoch.pid)
        self.assertEqual(receipt.signals, ("SIGTERM", "SIGKILL"))
        self.assertEqual(receipt.returncode, -9)
        self.assertEqual(driver.terms, [epoch.pid])
        self.assertEqual(driver.kills, [epoch.pid])
        self.assertEqual(len(driver.waits), 2)

    async def test_unreaped_child_quarantines_and_denies_future_work(self):
        driver = FakeProcessDriver(wait_results=(None, None))
        worker = self.worker(driver)
        await worker.begin_preparation(deadline=2.0)
        lease = await worker.acquire("e" * 64, ready_deadline=2.0)

        with self.assertRaises(WorkerError) as caught:
            await worker.abort(lease, reason="live_deadline", deadline=2.0)

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(worker.state, WorkerState.QUARANTINED)
        with self.assertRaises(WorkerError):
            await worker.acquire("f" * 64, ready_deadline=3.0)
        with self.assertRaises(WorkerError):
            await worker.begin_preparation(deadline=3.0)

    async def test_restart_is_explicit_and_uses_fresh_epoch_and_port(self):
        driver = FakeProcessDriver(pids=(4101, 4202))
        worker = self.worker(driver)
        first_epoch = await worker.begin_preparation(deadline=2.0)
        first_lease = await worker.acquire("1" * 64, ready_deadline=2.0)
        await worker.abort(first_lease, reason="live_deadline", deadline=2.0)

        with self.assertRaises(WorkerError) as stopped:
            await worker.acquire("2" * 64, ready_deadline=3.0)
        self.assertEqual(stopped.exception.code, "worker_not_ready")

        second_epoch = await worker.begin_preparation(deadline=4.0)
        self.assertNotEqual(second_epoch.epoch_id, first_epoch.epoch_id)
        self.assertNotEqual(second_epoch.pid, first_epoch.pid)
        self.assertNotEqual(second_epoch.port, first_epoch.port)
        self.assertEqual(len(driver.starts), 2)

    async def test_stale_epoch_or_stop_receipt_cannot_release_replacement(self):
        driver = FakeProcessDriver(pids=(4101, 4202))
        worker = self.worker(driver)
        first_epoch = await worker.begin_preparation(deadline=2.0)
        first_lease = await worker.acquire("3" * 64, ready_deadline=2.0)
        stale_receipt = await worker.abort(
            first_lease,
            reason="live_deadline",
            deadline=2.0,
        )
        await worker.begin_preparation(deadline=4.0)
        replacement = await worker.acquire("4" * 64, ready_deadline=4.0)

        with self.assertRaises(WorkerError) as stale:
            worker._accept_stop_receipt(stale_receipt)
        self.assertEqual(stale.exception.code, "stale_worker_epoch")
        self.assertEqual(worker.state, WorkerState.BUSY)
        self.assertEqual(worker.active_lease, replacement)

        with self.assertRaises(WorkerError):
            await worker.confirm_complete(first_lease)
        self.assertEqual(worker.active_lease, replacement)
        await worker.confirm_complete(replacement)

    async def test_shutdown_terminates_and_reaps_exact_child(self):
        driver = FakeProcessDriver()
        worker = self.worker(driver)
        epoch = await worker.begin_preparation(deadline=2.0)

        receipt = await worker.shutdown(deadline=3.0)

        self.assertEqual(receipt.pid, epoch.pid)
        self.assertEqual(receipt.reason, "shutdown")
        self.assertEqual(driver.terms, [epoch.pid])
        self.assertEqual(worker.state, WorkerState.STOPPED)
        self.assertIsNone(await worker.shutdown(deadline=4.0))

    async def test_launch_argv_is_single_slot_and_digest_bound(self):
        driver = FakeProcessDriver()
        worker = self.worker(driver)
        epoch = await worker.begin_preparation(deadline=2.0)
        _process, argv, start_new_session, _artifacts = driver.starts[0]

        self.assertTrue(start_new_session)
        self.assertIn("--parallel", argv)
        parallel_index = argv.index("--parallel")
        self.assertEqual(argv[parallel_index + 1], "1")
        self.assertEqual(epoch.argv_sha256, canonical_argv_sha256(argv))
        self.assertEqual(len(epoch.argv_sha256), 64)

        changed = (*argv[:-1], "changed")
        self.assertNotEqual(canonical_argv_sha256(changed), epoch.argv_sha256)

    def test_launch_spec_rejects_authority_overrides_and_invalid_argv(self):
        invalid_args = (
            ("--parallel", "2"),
            ("--parallel=2",),
            ("--port", "9000"),
            ("--host=0.0.0.0",),
            ("--model", "/tmp/other.gguf"),
            ("-m/tmp/other.gguf",),
            ("--api-key", "attacker-key"),
            ("--api-key=attacker-key",),
            ("--api-key-file", "/tmp/attacker-key"),
            ("--api-key-file=/tmp/attacker-key",),
            ("--alias", "attacker-alias"),
            ("--alias=attacker-alias",),
            ("-a", "attacker-alias"),
            ("-a=attacker-alias",),
            ("-aattacker-alias",),
            ("--", "/tmp/other.gguf"),
            ("",),
            ("--log-file=unsafe\x00path",),
            (7,),
        )
        for extra_args in invalid_args:
            with self.subTest(extra_args=extra_args), self.assertRaises(ValueError):
                replace(self.spec, extra_args=extra_args)

    def test_launch_spec_normalizes_artifact_paths_once(self):
        spec = WorkerLaunchSpec(
            binary_path="/Applications/Wayline/bin/../llama-server",
            model_path="/Applications/Wayline/models/../wayline.gguf",
            binary_sha256="a" * 64,
            model_sha256="b" * 64,
        )

        self.assertEqual(
            spec.binary_path,
            "/Applications/Wayline/llama-server",
        )
        self.assertEqual(
            spec.model_path,
            "/Applications/Wayline/wayline.gguf",
        )

    async def test_every_artifact_identity_fact_is_validated_before_spawn(self):
        corruptions = (
            {"binary_device": True},
            {"binary_inode": -1},
            {"model_device": -1},
            {"model_inode": True},
            {"binary_sha256": "A" * 64},
            {"model_path": "/Applications/Wayline/other.gguf"},
        )
        for index, changes in enumerate(corruptions):
            with self.subTest(changes=changes):
                driver = FakeProcessDriver()
                worker = ManagedLlamaWorker(
                    driver=driver,
                    artifact_verifier=CorruptArtifactVerifier(**changes),
                    launch_spec=self.spec,
                    clock=self.clock,
                    epoch_id_factory=Ids(f"facts-epoch-{index}"),
                    generation_id_factory=Ids(f"facts-generation-{index}"),
                    port_factory=lambda: 18100 + index,
                    launch_authority=WorkerLaunchAuthority(),
                )
                with self.assertRaises(WorkerError) as caught:
                    await worker.begin_preparation(deadline=2.0)
                self.assertEqual(caught.exception.code, "artifact_digest_mismatch")
                self.assertEqual(driver.starts, [])

    async def test_deadline_equal_to_clock_is_already_elapsed(self):
        worker = self.worker(FakeProcessDriver())

        with self.assertRaises(WorkerError) as caught:
            await worker.begin_preparation(deadline=self.clock())

        self.assertEqual(caught.exception.code, "worker_deadline_elapsed")

    async def test_completion_observed_at_deadline_is_late_and_cleanup_runs(self):
        clock = ManualClock()
        driver = IdentityRequiredDriver()

        class DeadlineStartDriver(IdentityRequiredDriver):
            async def start(self, *args, **kwargs):
                process = await super().start(*args, **kwargs)
                clock.now = 1.0
                return process

        driver = DeadlineStartDriver()
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=clock,
            epoch_id_factory=Ids("deadline-tie-epoch"),
            generation_id_factory=Ids("deadline-tie-generation"),
            port_factory=lambda: 18190,
            term_grace_seconds=0.25,
            launch_authority=WorkerLaunchAuthority(),
        )

        with self.assertRaises(WorkerError) as caught:
            await worker.begin_preparation(deadline=1.0)
        for _turn in range(20):
            if driver.waits:
                break
            await asyncio.sleep(0)

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(driver.terms, [4101])
        self.assertTrue(driver.waits)
        self.assertEqual(worker.state, WorkerState.QUARANTINED)

    def test_closed_loop_keeps_late_process_handle_under_cleanup_authority(self):
        worker = self.worker(FakeProcessDriver())
        process = FakeProcess(
            4199,
            artifacts=FakeArtifactVerifier(),
            argv_sha256="a" * 64,
        )
        late_task = CompletedDriverTask(process)

        worker._retain_driver_task(
            late_task,
            on_late_result=worker._cleanup_late_started_process,
        )

        self.assertTrue(worker.cleanup_authority_retained)

    async def test_shared_launch_authority_allows_only_one_controller(self):
        authority = WorkerLaunchAuthority()
        loop = asyncio.get_running_loop()
        first_driver = BlockingLifecycleDriver(block_start=True)
        second_driver = FakeProcessDriver()
        first = ManagedLlamaWorker(
            driver=first_driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=loop.time,
            epoch_id_factory=lambda: "shared-epoch-1",
            generation_id_factory=Ids("shared-generation-1"),
            port_factory=lambda: 18201,
            launch_authority=authority,
        )
        second = ManagedLlamaWorker(
            driver=second_driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=loop.time,
            epoch_id_factory=lambda: "shared-epoch-2",
            generation_id_factory=Ids("shared-generation-2"),
            port_factory=lambda: 18202,
            launch_authority=authority,
        )
        first_start = asyncio.create_task(
            first.begin_preparation(deadline=loop.time() + 1.0)
        )
        await first_driver.start_started.wait()

        with self.assertRaises(WorkerError) as caught:
            await second.begin_preparation(deadline=loop.time() + 1.0)

        self.assertEqual(caught.exception.code, "worker_unsafe_state")
        self.assertEqual(second_driver.starts, [])
        first_driver.start_release.set()
        await first_start
        await first.shutdown(deadline=loop.time() + 1.0)

    async def test_shared_authority_rejects_cross_controller_id_reuse(self):
        authority = WorkerLaunchAuthority()
        first = ManagedLlamaWorker(
            driver=IdentityRequiredDriver(),
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=lambda: "same-epoch",
            generation_id_factory=lambda: "same-generation",
            port_factory=lambda: 18211,
            launch_authority=authority,
        )
        await first.begin_preparation(deadline=2.0)
        first_lease = await first.acquire("1" * 64, ready_deadline=2.0)
        await first.confirm_complete(first_lease)
        await first.shutdown(deadline=2.0)

        second_driver = IdentityRequiredDriver()
        second = ManagedLlamaWorker(
            driver=second_driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=lambda: "same-epoch",
            generation_id_factory=lambda: "same-generation",
            port_factory=lambda: 18212,
            launch_authority=authority,
        )
        with self.assertRaises(WorkerError) as caught:
            await second.begin_preparation(deadline=2.0)

        self.assertEqual(caught.exception.code, "reused_worker_epoch")
        self.assertEqual(second_driver.starts, [])

    async def test_shared_authority_rejects_cross_controller_generation_reuse(self):
        authority = WorkerLaunchAuthority()
        first = ManagedLlamaWorker(
            driver=IdentityRequiredDriver(),
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=lambda: "generation-owner-epoch",
            generation_id_factory=lambda: "shared-generation",
            port_factory=lambda: 18213,
            launch_authority=authority,
        )
        await first.begin_preparation(deadline=2.0)
        first_lease = await first.acquire("1" * 64, ready_deadline=2.0)
        await first.confirm_complete(first_lease)
        await first.shutdown(deadline=2.0)

        second = ManagedLlamaWorker(
            driver=IdentityRequiredDriver(),
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=lambda: "generation-follower-epoch",
            generation_id_factory=lambda: "shared-generation",
            port_factory=lambda: 18214,
            launch_authority=authority,
        )
        await second.begin_preparation(deadline=2.0)
        with self.assertRaises(WorkerError) as caught:
            await second.acquire("2" * 64, ready_deadline=2.0)

        self.assertEqual(caught.exception.code, "reused_generation_id")
        self.assertEqual(second.state, WorkerState.READY_IDLE)

    async def test_quarantined_shared_authority_never_releases_ownership(self):
        authority = WorkerLaunchAuthority()
        bad = ManagedLlamaWorker(
            driver=RaisingPidDriver(),
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=lambda: "quarantined-epoch",
            generation_id_factory=Ids("quarantined-generation"),
            port_factory=lambda: 18221,
            launch_authority=authority,
        )
        with self.assertRaises(WorkerError):
            await bad.begin_preparation(deadline=2.0)

        follower = ManagedLlamaWorker(
            driver=FakeProcessDriver(),
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=lambda: "follower-epoch",
            generation_id_factory=Ids("follower-generation"),
            port_factory=lambda: 18222,
            launch_authority=authority,
        )
        with self.assertRaises(WorkerError) as caught:
            await follower.begin_preparation(deadline=2.0)

        self.assertEqual(caught.exception.code, "worker_quarantined")

    async def test_unreaped_child_quarantines_process_wide_authority(self):
        authority = WorkerLaunchAuthority()
        driver = FakeProcessDriver(wait_results=(None, None))
        owner = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=lambda: "unreaped-epoch",
            generation_id_factory=lambda: "unreaped-generation",
            port_factory=lambda: 18231,
            launch_authority=authority,
        )
        await owner.begin_preparation(deadline=2.0)
        lease = await owner.acquire("7" * 64, ready_deadline=2.0)
        with self.assertRaises(WorkerError):
            await owner.abort(lease, reason="unreaped", deadline=2.0)
        calls_after_quarantine = (
            len(driver.terms),
            len(driver.kills),
            len(driver.waits),
        )
        with self.assertRaises(WorkerError) as shutdown_error:
            await owner.shutdown(deadline=2.0)
        self.assertEqual(shutdown_error.exception.code, "worker_quarantined")
        self.assertEqual(owner.state, WorkerState.QUARANTINED)
        self.assertEqual(
            (
                len(driver.terms),
                len(driver.kills),
                len(driver.waits),
            ),
            calls_after_quarantine,
        )

        follower = ManagedLlamaWorker(
            driver=FakeProcessDriver(),
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=lambda: "unreaped-follower-epoch",
            generation_id_factory=lambda: "unreaped-follower-generation",
            port_factory=lambda: 18232,
            launch_authority=authority,
        )
        with self.assertRaises(WorkerError) as caught:
            await follower.begin_preparation(deadline=2.0)

        self.assertEqual(caught.exception.code, "worker_quarantined")

    async def test_readiness_false_stops_and_reaps_spawned_child(self):
        driver = FakeProcessDriver(ready_result=False)
        worker = self.worker(driver)

        with self.assertRaises(WorkerError) as caught:
            await worker.begin_preparation(deadline=2.0)

        self.assertEqual(caught.exception.code, "worker_not_ready")
        self.assertEqual(worker.state, WorkerState.STOPPED)
        self.assertEqual(driver.terms, [4101])
        self.assertEqual(driver.waits[0][0], 4101)

    async def test_uncertain_start_exception_quarantines_and_denies_retry(self):
        driver = FakeProcessDriver(
            start_error=RuntimeError("private launch detail")
        )
        worker = self.worker(driver)

        with self.assertRaises(WorkerError) as caught:
            await worker.begin_preparation(deadline=2.0)

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(str(caught.exception), "worker_quarantined")
        self.assertEqual(worker.state, WorkerState.QUARANTINED)
        with self.assertRaises(WorkerError) as denied:
            await worker.begin_preparation(deadline=3.0)
        self.assertEqual(denied.exception.code, "worker_quarantined")

    async def test_readiness_exception_with_unconfirmed_reap_quarantines(self):
        driver = FakeProcessDriver(
            ready_error=RuntimeError("private readiness detail"),
            wait_results=(None, None),
        )
        worker = self.worker(driver)

        with self.assertRaises(WorkerError) as caught:
            await worker.begin_preparation(deadline=2.0)

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(str(caught.exception), "worker_quarantined")
        self.assertEqual(worker.state, WorkerState.QUARANTINED)
        self.assertEqual(driver.terms, [4101])
        self.assertEqual(driver.kills, [4101])

    async def test_repeated_cancellation_during_abort_reaps_then_reraises(self):
        driver = FakeProcessDriver()
        driver.block_wait = True
        worker = self.worker(driver)
        await worker.begin_preparation(deadline=2.0)
        lease = await worker.acquire("9" * 64, ready_deadline=2.0)

        abort = asyncio.create_task(
            worker.abort(lease, reason="live_deadline", deadline=2.0)
        )
        await driver.wait_started.wait()
        abort.cancel()
        abort.cancel()
        await asyncio.sleep(0)
        self.assertFalse(abort.done())

        driver.wait_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await abort

        self.assertEqual(worker.state, WorkerState.STOPPED)
        self.assertEqual(driver.terms, [4101])
        self.assertEqual(driver.waits[0][0], 4101)

    async def test_driver_signal_or_wait_exception_quarantines(self):
        drivers = (
            FakeProcessDriver(terminate_error=RuntimeError("private signal detail")),
            FakeProcessDriver(
                wait_results=(
                    lambda _process, _token: (_ for _ in ()).throw(
                        RuntimeError("private wait detail")
                    ),
                )
            ),
        )
        for driver in drivers:
            with self.subTest(driver=driver):
                worker = self.worker(driver)
                await worker.begin_preparation(deadline=2.0)
                lease = await worker.acquire("8" * 64, ready_deadline=2.0)
                with self.assertRaises(WorkerError) as caught:
                    await worker.abort(
                        lease,
                        reason="live_deadline",
                        deadline=2.0,
                    )
                self.assertEqual(caught.exception.code, "worker_quarantined")
                self.assertEqual(str(caught.exception), "worker_quarantined")
                self.assertEqual(worker.state, WorkerState.QUARANTINED)

    async def test_epoch_identifier_cannot_be_reused_even_if_pid_repeats(self):
        driver = FakeProcessDriver(pids=(4101, 4101))
        ports = iter((18081, 18082))
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=lambda: "reused-epoch",
            generation_id_factory=Ids("generation"),
            port_factory=lambda: next(ports),
            term_grace_seconds=0.25,
            launch_authority=WorkerLaunchAuthority(),
        )
        first_epoch = await worker.begin_preparation(deadline=2.0)
        first_lease = await worker.acquire("7" * 64, ready_deadline=2.0)
        await worker.abort(first_lease, reason="live_deadline", deadline=2.0)

        with self.assertRaises(WorkerError) as caught:
            await worker.begin_preparation(deadline=3.0)

        self.assertEqual(caught.exception.code, "reused_worker_epoch")
        self.assertEqual(worker.state, WorkerState.STOPPED)
        self.assertEqual(first_epoch.epoch_id, "reused-epoch")
        self.assertEqual(len(driver.starts), 1)

    async def test_generation_identifier_is_unique_for_controller_lifetime(self):
        driver = FakeProcessDriver()
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeArtifactVerifier(),
            launch_spec=self.spec,
            clock=self.clock,
            epoch_id_factory=Ids("epoch"),
            generation_id_factory=lambda: "reused-generation",
            port_factory=lambda: 18081,
            launch_authority=WorkerLaunchAuthority(),
        )
        await worker.begin_preparation(deadline=2.0)
        first = await worker.acquire("3" * 64, ready_deadline=2.0)
        await worker.confirm_complete(first)

        with self.assertRaises(WorkerError) as caught:
            await worker.acquire("2" * 64, ready_deadline=2.0)

        self.assertEqual(caught.exception.code, "reused_generation_id")
        self.assertEqual(worker.state, WorkerState.READY_IDLE)
        self.assertIsNone(worker.active_lease)

    async def test_stop_receipt_must_match_generation_without_mutating_state(self):
        for bad_generation_id in (None, "different-generation"):
            with self.subTest(bad_generation_id=bad_generation_id):
                driver = FakeProcessDriver()
                worker = self.worker(driver)
                epoch = await worker.begin_preparation(deadline=2.0)
                lease = await worker.acquire("6" * 64, ready_deadline=2.0)
                receipt = WorkerStopReceipt(
                    epoch_id=epoch.epoch_id,
                    generation_id=bad_generation_id,
                    pid=epoch.pid,
                    reason="stale",
                    signals=("SIGTERM",),
                    returncode=-15,
                )

                with self.assertRaises(WorkerError) as caught:
                    worker._accept_stop_receipt(receipt)

                self.assertEqual(caught.exception.code, "stale_generation_lease")
                self.assertEqual(worker.state, WorkerState.BUSY)
                self.assertEqual(worker.active_lease, lease)
                self.assertEqual(worker.epoch, epoch)

        driver = FakeProcessDriver()
        worker = self.worker(driver)
        epoch = await worker.begin_preparation(deadline=2.0)
        lease = await worker.acquire("5" * 64, ready_deadline=2.0)
        await worker.confirm_complete(lease)
        stale_busy_receipt = WorkerStopReceipt(
            epoch_id=epoch.epoch_id,
            generation_id=lease.generation_id,
            pid=epoch.pid,
            reason="stale",
            signals=("SIGTERM",),
            returncode=-15,
        )

        with self.assertRaises(WorkerError) as caught:
            worker._accept_stop_receipt(stale_busy_receipt)

        self.assertEqual(caught.exception.code, "stale_generation_lease")
        self.assertEqual(worker.state, WorkerState.READY_IDLE)
        self.assertIsNone(worker.active_lease)
        self.assertEqual(worker.epoch, epoch)

    def test_receipts_and_leases_are_immutable(self):
        authority = object()
        self.assertIn(
            "transport_authority",
            GenerationLease.__dataclass_fields__,
        )
        lease = GenerationLease(
            generation_id="generation-1",
            epoch_id="epoch-1",
            prompt_sha256="a" * 64,
            endpoint="http://127.0.0.1:18081/v1/chat/completions",
            transport_authority=authority,
        )
        with self.assertRaises(AttributeError):
            lease.epoch_id = "changed"
        self.assertNotEqual(replace(lease, epoch_id="changed"), lease)
        self.assertIs(lease.transport_authority, authority)
        self.assertNotIn(repr(authority), repr(lease))


if __name__ == "__main__":
    unittest.main()
