import asyncio
from dataclasses import replace
import hashlib
import json
import os
import socket
import threading
import time
import unittest
from unittest.mock import patch

import services.wayline_forge.app.slm_prompt as slm_prompt
import services.wayline_forge.app.providers.llama_cpp as llama_cpp
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.llama_worker import (
    ArtifactVerificationReceipt,
    canonical_argv_sha256,
    ManagedLlamaWorker,
    ProcessExit,
    WorkerLaunchAuthority,
    WorkerLaunchSpec,
    WorkerState,
)
from services.wayline_forge.app.model_manifest import ModelManifest
from services.wayline_forge.app.orchestrator import BatchPreparationOrchestrator
from services.wayline_forge.app.providers.distractor import (
    PinnedSlmManifest,
    ProviderError,
    RawSlmGeneration,
    SlmRequest,
)
from services.wayline_forge.app.providers.llama_cpp import (
    LlamaCppProvider as ProductionLlamaCppProvider,
    StdlibAsyncJsonTransport,
)
from services.wayline_forge.app.providers.recorded import RecordedDistractorProvider
from services.wayline_forge.app.slm_prompt import (
    PROMPT_TEMPLATE_SHA256,
    build_slm_request,
    prompt_payload,
    validate_prompt_receipt,
)


def LlamaCppProvider(*args, **kwargs):
    """Route only test doubles through the provider's private injection seam."""

    transport = kwargs.get("transport")
    if transport is None and len(args) >= 2:
        transport = args[1]
    if type(transport) is StdlibAsyncJsonTransport:
        return ProductionLlamaCppProvider(*args, **kwargs)
    return ProductionLlamaCppProvider._for_tests(*args, **kwargs)


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def post_json(
        self,
        url,
        payload,
        *,
        timeout_seconds,
        max_response_bytes,
        transport_authority,
    ):
        self.calls.append(
            (
                url,
                payload,
                timeout_seconds,
                max_response_bytes,
                transport_authority,
            )
        )
        if self.error is not None:
            raise self.error
        return self.response


class AuthorityTamperingTransport(FakeTransport):
    def __init__(self, response, tamper, *, error=None):
        super().__init__(response=response, error=error)
        self.tamper = tamper

    async def post_json(
        self,
        url,
        payload,
        *,
        timeout_seconds,
        max_response_bytes,
        transport_authority,
    ):
        self.calls.append(
            (
                url,
                payload,
                timeout_seconds,
                max_response_bytes,
                transport_authority,
            )
        )
        self.tamper()
        if self.error is not None:
            raise self.error
        return self.response


class FakeManagedProcess:
    def __init__(self, pid, *, transport_authority, artifacts, argv_sha256):
        self.pid = pid
        self.process_identity = object()
        self.transport_authority = transport_authority
        self.launch_artifacts = artifacts
        self.launch_argv_sha256 = argv_sha256


class FakeTransportCredentials:
    __slots__ = ("bearer_token", "model_alias")

    def __init__(self, bearer_token, model_alias):
        self.bearer_token = bearer_token
        self.model_alias = model_alias

    def __repr__(self):
        return "FakeTransportCredentials(<redacted>)"


class FakeManagedArtifactVerifier:
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


class FakeManagedProcessDriver:
    def __init__(
        self,
        *,
        wait_results=(),
        bearer_token="private-provider-bearer",
        model_alias="wayline-authority-alias",
    ):
        self.wait_results = list(wait_results)
        self.starts = []
        self.terms = []
        self.kills = []
        self.waits = []
        self.wait_started = asyncio.Event()
        self.wait_release = asyncio.Event()
        self.block_wait = False
        self.block_start = False
        self.start_started = asyncio.Event()
        self.start_release = asyncio.Event()
        self.identities = {}
        self.transport_authorities = {}
        self.ready_transport_authorities = set()
        self.transport_resolutions = []
        self.credentials = FakeTransportCredentials(bearer_token, model_alias)

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
        transport_authority = object()
        process = FakeManagedProcess(
            5101 + len(self.starts),
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
        self.ready_transport_authorities.add(
            self.transport_authorities[id(process)]
        )
        return True

    def resolve_transport_credentials(self, transport_authority):
        self.transport_resolutions.append(transport_authority)
        if not any(
            candidate is transport_authority
            for candidate in self.ready_transport_authorities
        ):
            from services.wayline_forge.app.llama_worker import WorkerError

            raise WorkerError("stale_transport_authority")
        return self.credentials

    def _validate_identity(self, process, process_identity):
        if self.identities.get(id(process)) is not process_identity:
            raise RuntimeError("stale process identity")

    def terminate_group(self, process, *, process_identity):
        self._validate_identity(process, process_identity)
        self.terms.append(process.pid)

    def kill_group(self, process, *, process_identity):
        self._validate_identity(process, process_identity)
        self.kills.append(process.pid)

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
                authority = self.transport_authorities.pop(id(process), None)
                self.ready_transport_authorities.discard(authority)
            return result
        authority = self.transport_authorities.pop(id(process), None)
        self.ready_transport_authorities.discard(authority)
        return ProcessExit(process.pid, -15, process_identity)


class ActiveCallTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.any_started = threading.Event()
        self.overlap = threading.Event()

    def enter(self):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.any_started.set()
            if self.active > 1:
                self.overlap.set()

    def leave(self):
        with self._lock:
            self.active -= 1


class GatedAsyncTransport:
    def __init__(self, response, tracker, *, error=None):
        self.response = response
        self.tracker = tracker
        self.error = error
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.calls = 0

    async def post_json(
        self,
        url,
        payload,
        *,
        timeout_seconds,
        max_response_bytes,
        transport_authority,
    ):
        self.calls += 1
        self.tracker.enter()
        self.started.set()
        try:
            await self.release.wait()
            if self.error is not None:
                raise self.error
            return self.response
        finally:
            self.tracker.leave()
            self.finished.set()


class CrossLoopGatedTransport:
    def __init__(self, response, tracker):
        self.response = response
        self.tracker = tracker
        self.started = threading.Event()
        self.finished = threading.Event()
        self._release_requested = threading.Event()
        self._state_lock = threading.Lock()
        self._loop = None
        self._release_future = None

    async def post_json(
        self,
        url,
        payload,
        *,
        timeout_seconds,
        max_response_bytes,
        transport_authority,
    ):
        loop = asyncio.get_running_loop()
        release_future = loop.create_future()
        with self._state_lock:
            self._loop = loop
            self._release_future = release_future
        self.tracker.enter()
        self.started.set()
        try:
            if not self._release_requested.is_set():
                await release_future
            return self.response
        finally:
            self.tracker.leave()
            self.finished.set()

    def release(self):
        self._release_requested.set()
        with self._state_lock:
            loop = self._loop
            release_future = self._release_future
        if loop is not None and release_future is not None:
            if loop.is_closed():
                return
            try:
                loop.call_soon_threadsafe(
                    lambda: (
                        None
                        if release_future.done()
                        else release_future.set_result(None)
                    )
                )
            except RuntimeError:
                if not loop.is_closed():
                    raise


class DelayedCloseWriter:
    def __init__(self):
        self.transport = self
        self.aborted = False
        self.request_written = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_finished = asyncio.Event()

    def write(self, data):
        self.request_written.set()
        return None

    async def drain(self):
        return None

    def abort(self):
        self.aborted = True

    def close(self):
        self.aborted = True

    async def wait_closed(self):
        self.cleanup_started.set()
        try:
            await self.cleanup_release.wait()
        finally:
            self.cleanup_finished.set()


async def _read_http_request(reader):
    header_block = await reader.readuntil(b"\r\n\r\n")
    lines = header_block[:-4].split(b"\r\n")
    headers = {}
    for line in lines[1:]:
        name, value = line.split(b":", 1)
        headers[name.strip().lower()] = value.strip()
    body_size = int(headers.get(b"content-length", b"0"))
    body = await reader.readexactly(body_size)
    return header_block, body


async def _write_json_response(writer, body, *, status=b"200 OK", headers=()):
    response_headers = [
        b"HTTP/1.1 " + status,
        b"Content-Type: application/json",
        f"Content-Length: {len(body)}".encode("ascii"),
        b"Connection: close",
        *headers,
        b"",
        b"",
    ]
    writer.write(b"\r\n".join(response_headers) + body)
    await writer.drain()


async def _socketpair_streams():
    client_socket, server_socket = socket.socketpair()
    client_reader, client_writer = await asyncio.open_connection(sock=client_socket)
    server_reader, server_writer = await asyncio.open_connection(sock=server_socket)
    return (client_reader, client_writer), (server_reader, server_writer)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.verifier = DistractorVerifier.for_tests()
        self.blueprint = self.verifier.reference_blueprint("decimal-add-731")
        self.request = build_slm_request(self.blueprint)
        self.raw_text = self.verifier.fixture_text("accepted.json")

    def new_managed_worker(self, driver=None):
        driver = driver or FakeManagedProcessDriver()
        epoch_ids = iter(range(1, 100))
        generation_ids = iter(range(1, 100))
        ports = iter(range(8081, 8180))
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeManagedArtifactVerifier(),
            launch_spec=WorkerLaunchSpec(
                binary_path="/Applications/Wayline/llama-server",
                model_path="/Applications/Wayline/wayline.gguf",
                binary_sha256="a" * 64,
                model_sha256=self.verifier.manifest.model_sha256,
            ),
            clock=time.monotonic,
            epoch_id_factory=lambda: f"provider-epoch-{next(epoch_ids)}",
            generation_id_factory=lambda: (
                f"provider-generation-{next(generation_ids)}"
            ),
            port_factory=lambda: next(ports),
            term_grace_seconds=0.05,
            launch_authority=WorkerLaunchAuthority(),
        )
        return worker, driver

    async def managed_worker(self, driver=None):
        worker, driver = self.new_managed_worker(driver)
        loop = asyncio.get_running_loop()
        await worker.begin_preparation(deadline=loop.time() + 1.0)
        return worker, driver

    def test_every_llama_transport_requires_launcher_owned_worker(self):
        driver = FakeManagedProcessDriver()
        transports = (
            StdlibAsyncJsonTransport(
                credential_resolver=driver.resolve_transport_credentials,
            ),
            FakeTransport({"choices": [{"message": {"content": self.raw_text}}]}),
        )
        for transport in transports:
            with self.subTest(transport=transport), self.assertRaises(
                ProviderError
            ) as caught:
                LlamaCppProvider(
                    self.verifier.manifest,
                    transport,
                )
            self.assertEqual(caught.exception.code, "managed_worker_required")

        with self.assertRaises(ProviderError) as forged:
            LlamaCppProvider(
                self.verifier.manifest,
                FakeTransport({}),
                worker=object(),
            )
        self.assertEqual(forged.exception.code, "managed_worker_required")

        forged_type = type("ForgedManagedWorker", (ManagedLlamaWorker,), {})
        with self.assertRaises(ProviderError) as subclassed:
            LlamaCppProvider(
                self.verifier.manifest,
                FakeTransport({}),
                worker=object.__new__(forged_type),
            )
        self.assertEqual(subclassed.exception.code, "managed_worker_required")

    def test_stdlib_transport_requires_a_bound_secret_safe_resolver(self):
        with self.assertRaises(ProviderError) as missing:
            StdlibAsyncJsonTransport()
        self.assertEqual(
            missing.exception.code,
            "transport_credential_resolver_required",
        )

        with self.assertRaises(ProviderError) as unbound:
            StdlibAsyncJsonTransport(
                credential_resolver=lambda _authority: None,
            )
        self.assertEqual(
            unbound.exception.code,
            "transport_credential_resolver_required",
        )

        driver = FakeManagedProcessDriver(
            bearer_token="private-repr-bearer",
            model_alias="private-repr-alias",
        )
        transport = StdlibAsyncJsonTransport(
            credential_resolver=driver.resolve_transport_credentials,
        )
        rendered = repr(transport)
        self.assertNotIn("private-repr-bearer", rendered)
        self.assertNotIn("private-repr-alias", rendered)
        self.assertNotIn("resolve_transport_credentials", rendered)

    def test_provider_rejects_transport_without_explicit_authority_parameter(self):
        class LegacyUnauthenticatedTransport:
            async def post_json(
                self,
                url,
                payload,
                *,
                timeout_seconds,
                max_response_bytes,
            ):
                raise AssertionError("unauthenticated transport was invoked")

        worker, _driver = self.new_managed_worker()
        with self.assertRaises(ProviderError) as caught:
            LlamaCppProvider(
                self.verifier.manifest,
                LegacyUnauthenticatedTransport(),
                worker=worker,
            )
        self.assertEqual(caught.exception.code, "managed_worker_required")

    def test_production_constructor_rejects_custom_and_subclass_transports(self):
        class StdlibTransportSubclass(StdlibAsyncJsonTransport):
            pass

        worker, driver = self.new_managed_worker()
        custom_transports = (
            FakeTransport(
                {"choices": [{"message": {"content": self.raw_text}}]}
            ),
            FakeTransport(
                {
                    "model": "wrong-response-alias",
                    "choices": [{"message": {"content": self.raw_text}}],
                }
            ),
        )
        transports = (
            *custom_transports,
            StdlibTransportSubclass(
                credential_resolver=driver.resolve_transport_credentials,
            ),
        )

        for transport in transports:
            with self.subTest(
                transport=type(transport).__name__
            ), self.assertRaisesRegex(
                ProviderError,
                "^managed_worker_required$",
            ):
                ProductionLlamaCppProvider(
                    self.verifier.manifest,
                    transport,
                    worker=worker,
                )

        self.assertEqual(driver.starts, [])
        self.assertIsNone(worker.active_lease)
        self.assertTrue(all(transport.calls == [] for transport in custom_transports))

    async def test_private_test_constructor_preserves_injected_transport_seam(self):
        self.assertTrue(hasattr(ProductionLlamaCppProvider, "_for_tests"))
        worker, _driver = await self.managed_worker()
        transport = FakeTransport(
            {"choices": [{"message": {"content": self.raw_text}}]}
        )

        provider = ProductionLlamaCppProvider._for_tests(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        generation = await provider.generate(self.request)

        self.assertEqual(generation.text, self.raw_text)
        self.assertEqual(len(transport.calls), 1)

    def test_production_constructor_rejects_replaced_stdlib_post_implementation(self):
        worker, driver = self.new_managed_worker()
        transport = StdlibAsyncJsonTransport(
            credential_resolver=driver.resolve_transport_credentials,
        )
        raw_text = self.raw_text

        async def alias_bypassing_post(
            transport_self,
            url,
            payload,
            *,
            timeout_seconds,
            max_response_bytes,
            transport_authority,
        ):
            del (
                transport_self,
                url,
                payload,
                timeout_seconds,
                max_response_bytes,
                transport_authority,
            )
            return {"choices": [{"message": {"content": raw_text}}]}

        with patch.object(
            StdlibAsyncJsonTransport,
            "post_json",
            new=alias_bypassing_post,
        ), self.assertRaisesRegex(
            ProviderError,
            "^managed_worker_required$",
        ):
            ProductionLlamaCppProvider(
                self.verifier.manifest,
                transport,
                worker=worker,
            )

        self.assertEqual(driver.starts, [])
        self.assertIsNone(worker.active_lease)

    def test_response_attestation_binds_payload_transport_and_call_authority(self):
        self.assertTrue(hasattr(llama_cpp, "_issue_response_attestation"))
        self.assertTrue(hasattr(llama_cpp, "_verify_response_attestation"))
        transport = object()
        transport_authority = object()
        response = {
            "model": "private-authority-alias",
            "choices": [{"message": {"content": self.raw_text}}],
        }
        attested = llama_cpp._issue_response_attestation(
            response,
            transport=transport,
            transport_authority=transport_authority,
        )

        self.assertIs(
            llama_cpp._verify_response_attestation(
                attested,
                transport=transport,
                transport_authority=transport_authority,
            ),
            response,
        )
        self.assertNotIn("private-authority-alias", repr(attested))

        invalid = (
            response,
            (attested, object(), transport_authority),
            (attested, transport, object()),
        )
        for case in invalid:
            if isinstance(case, tuple):
                candidate, candidate_transport, candidate_authority = case
            else:
                candidate = case
                candidate_transport = transport
                candidate_authority = transport_authority
            with self.subTest(case=type(case).__name__), self.assertRaisesRegex(
                ProviderError,
                "^manifest_worker_mismatch$",
            ):
                llama_cpp._verify_response_attestation(
                    candidate,
                    transport=candidate_transport,
                    transport_authority=candidate_authority,
                )

    def test_production_provider_unwraps_only_its_authority_bound_response(self):
        worker, driver = self.new_managed_worker()
        transport = StdlibAsyncJsonTransport(
            credential_resolver=driver.resolve_transport_credentials,
        )
        provider = ProductionLlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        self.assertTrue(hasattr(provider, "_unwrap_transport_response"))
        transport_authority = object()
        response = {
            "model": "private-authority-alias",
            "choices": [{"message": {"content": self.raw_text}}],
        }
        attested = llama_cpp._issue_response_attestation(
            response,
            transport=transport,
            transport_authority=transport_authority,
        )

        self.assertIs(
            provider._unwrap_transport_response(
                attested,
                transport_authority=transport_authority,
            ),
            response,
        )
        with self.assertRaisesRegex(
            ProviderError,
            "^manifest_worker_mismatch$",
        ):
            provider._unwrap_transport_response(
                response,
                transport_authority=transport_authority,
            )

    async def test_provider_transport_replacement_is_denied_before_acquire(self):
        worker, _driver = await self.managed_worker()
        original = FakeTransport(
            {"choices": [{"message": {"content": self.raw_text}}]}
        )
        replacement = FakeTransport(
            {"choices": [{"message": {"content": self.raw_text}}]}
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            original,
            worker=worker,
        )
        object.__setattr__(provider, "_transport", replacement)

        with self.assertRaises(ProviderError) as caught:
            await provider.generate(self.request)

        self.assertEqual(caught.exception.code, "managed_worker_required")
        self.assertEqual(original.calls, [])
        self.assertEqual(replacement.calls, [])
        self.assertEqual(worker.state, WorkerState.READY_IDLE)
        self.assertIsNone(worker.active_lease)

    async def test_transport_replacement_denies_preparation_without_launch(self):
        worker, driver = self.new_managed_worker()
        provider = LlamaCppProvider(
            self.verifier.manifest,
            FakeTransport({}),
            worker=worker,
        )
        replacement = FakeTransport({})
        object.__setattr__(provider, "_transport", replacement)

        with self.assertRaises(ProviderError) as caught:
            await provider.begin_preparation(
                deadline=asyncio.get_running_loop().time() + 1.0,
            )

        self.assertEqual(caught.exception.code, "managed_worker_required")
        self.assertEqual(driver.starts, [])
        self.assertEqual(replacement.calls, [])

    async def test_transport_resolver_replacement_is_denied_before_socket(self):
        class PermissiveReplacementRegistry:
            def resolve_transport_credentials(self, transport_authority):
                return FakeTransportCredentials(
                    "attacker-selected-bearer",
                    "attacker-selected-alias",
                )

        worker, driver = await self.managed_worker()
        transport = StdlibAsyncJsonTransport(
            credential_resolver=driver.resolve_transport_credentials,
        )
        replacement = PermissiveReplacementRegistry()
        object.__setattr__(
            transport,
            "_credential_resolver_authority",
            (replacement.resolve_transport_credentials,),
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        connection_attempts = []

        async def open_connection(*args, **kwargs):
            connection_attempts.append((args, kwargs))
            raise AssertionError("tampered resolver reached socket")

        with patch.object(
            llama_cpp.asyncio,
            "open_connection",
            new=open_connection,
        ):
            with self.assertRaises(ProviderError) as caught:
                await provider.generate(self.request)

        self.assertEqual(caught.exception.code, "managed_worker_required")
        self.assertEqual(connection_attempts, [])
        self.assertEqual(worker.state, WorkerState.STOPPED)

    async def test_forged_lease_authority_is_denied_before_transport(self):
        worker, _driver = await self.managed_worker()
        transport = FakeTransport(
            {"choices": [{"message": {"content": self.raw_text}}]}
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        original_acquire = ManagedLlamaWorker.acquire

        async def acquire_forged(worker_self, *args, **kwargs):
            lease = await original_acquire(worker_self, *args, **kwargs)
            self.assertTrue(hasattr(lease, "transport_authority"))
            return replace(lease, transport_authority=object())

        try:
            with patch.object(
                ManagedLlamaWorker,
                "acquire",
                new=acquire_forged,
            ):
                with self.assertRaises(ProviderError) as caught:
                    await provider.generate(self.request)
            self.assertEqual(caught.exception.code, "managed_worker_required")
            self.assertEqual(transport.calls, [])
        finally:
            active = worker.active_lease
            if active is not None:
                await worker.abort(
                    active,
                    reason="test_cleanup",
                    deadline=asyncio.get_running_loop().time() + 1.0,
                )

    async def test_stale_resolver_authority_fails_before_opening_connection(self):
        worker, _worker_driver = await self.managed_worker()
        unrelated_driver = FakeManagedProcessDriver()
        transport = StdlibAsyncJsonTransport(
            credential_resolver=unrelated_driver.resolve_transport_credentials,
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        connection_attempts = []

        async def open_connection(*args, **kwargs):
            connection_attempts.append((args, kwargs))
            raise AssertionError("stale authority reached the socket boundary")

        with patch.object(
            llama_cpp.asyncio,
            "open_connection",
            new=open_connection,
        ):
            with self.assertRaises(ProviderError) as caught:
                await provider.generate(self.request)

        self.assertEqual(caught.exception.code, "managed_worker_required")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(connection_attempts, [])
        self.assertEqual(worker.state, WorkerState.STOPPED)

    async def test_invalid_resolved_key_or_alias_fails_before_socket_and_is_secret_safe(self):
        invalid_credentials = (
            ("private\r\nInjected: key", "wayline-alias"),
            ("private-key", "private\nmodel-alias"),
            ("", "wayline-alias"),
            ("private-key", ""),
        )
        for index, (bearer_token, model_alias) in enumerate(invalid_credentials):
            with self.subTest(index=index):
                driver = FakeManagedProcessDriver(
                    bearer_token=bearer_token,
                    model_alias=model_alias,
                )
                worker, _driver = await self.managed_worker(driver)
                provider = LlamaCppProvider(
                    self.verifier.manifest,
                    StdlibAsyncJsonTransport(
                        credential_resolver=driver.resolve_transport_credentials,
                    ),
                    worker=worker,
                )
                connection_attempts = []

                async def open_connection(*args, **kwargs):
                    connection_attempts.append((args, kwargs))
                    raise AssertionError("invalid credentials reached socket")

                with patch.object(
                    llama_cpp.asyncio,
                    "open_connection",
                    new=open_connection,
                ):
                    with self.assertRaises(ProviderError) as caught:
                        await provider.generate(self.request)

                self.assertEqual(
                    caught.exception.code,
                    "managed_worker_required",
                )
                self.assertEqual(connection_attempts, [])
                rendered = repr(caught.exception)
                for private_value in (bearer_token, model_alias):
                    if private_value:
                        self.assertNotIn(private_value, rendered)

    def test_provider_lifecycle_configuration_is_read_only_and_slotted(self):
        worker, _driver = self.new_managed_worker()
        replacement, _replacement_driver = self.new_managed_worker()
        transport = FakeTransport({})
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )

        self.assertFalse(hasattr(provider, "__dict__"))
        attempts = {
            "manifest": PinnedSlmManifest.for_tests(),
            "transport": FakeTransport({}),
            "worker": None,
            "endpoint": "http://127.0.0.1:8082/v1/chat/completions",
            "timeout_seconds": 30.0,
            "clock": lambda: "private-clock",
            "monotonic": lambda: 0.0,
        }
        for field_name, value in attempts.items():
            with self.subTest(field_name=field_name), self.assertRaises(
                AttributeError
            ):
                setattr(provider, field_name, value)

        with self.assertRaises(AttributeError):
            provider.worker = replacement
        self.assertIs(provider.worker, worker)
        self.assertIs(provider.transport, transport)

    async def test_hostile_worker_replacement_denies_preparation_without_launch(self):
        worker, driver = self.new_managed_worker()
        replacement, replacement_driver = self.new_managed_worker()
        provider = LlamaCppProvider(
            self.verifier.manifest,
            FakeTransport({}),
            worker=worker,
        )
        object.__setattr__(provider, "_worker", replacement)

        with self.assertRaises(ProviderError) as caught:
            await provider.begin_preparation(
                deadline=asyncio.get_running_loop().time() + 1.0
            )

        self.assertEqual(caught.exception.code, "managed_worker_required")
        self.assertEqual(str(caught.exception), "managed_worker_required")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(driver.starts, [])
        self.assertEqual(replacement_driver.starts, [])

    async def test_hostile_worker_replacement_denies_acquire_before_transport(self):
        worker, driver = await self.managed_worker()
        replacement, _replacement_driver = self.new_managed_worker()
        transport = FakeTransport(
            {"choices": [{"message": {"content": self.raw_text}}]}
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        object.__setattr__(provider, "_worker", replacement)

        with self.assertRaises(ProviderError) as caught:
            await provider.generate(self.request)

        self.assertEqual(caught.exception.code, "managed_worker_required")
        self.assertEqual(transport.calls, [])
        self.assertEqual(worker.state, WorkerState.READY_IDLE)
        self.assertIsNone(worker.active_lease)

    async def test_authority_changed_during_acquire_denies_transport(self):
        worker, _driver = await self.managed_worker()
        replacement, _replacement_driver = self.new_managed_worker()
        transport = FakeTransport(
            {"choices": [{"message": {"content": self.raw_text}}]}
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        original_acquire = ManagedLlamaWorker.acquire

        async def acquire_then_tamper(worker_self, *args, **kwargs):
            lease = await original_acquire(worker_self, *args, **kwargs)
            object.__setattr__(provider, "_worker", replacement)
            return lease

        try:
            with patch.object(
                ManagedLlamaWorker,
                "acquire",
                new=acquire_then_tamper,
            ):
                with self.assertRaises(ProviderError) as caught:
                    await provider.generate(self.request)
            self.assertEqual(caught.exception.code, "managed_worker_required")
            self.assertEqual(transport.calls, [])
            self.assertEqual(replacement.state, WorkerState.STOPPED)
            self.assertIsNone(replacement.active_lease)
        finally:
            object.__setattr__(provider, "_worker", worker)
            if worker.active_lease is not None:
                await worker.abort(
                    worker.active_lease,
                    reason="test_cleanup",
                    deadline=asyncio.get_running_loop().time() + 1.0,
                )

    async def test_authority_changed_during_transport_denies_completion(self):
        worker, _driver = await self.managed_worker()
        replacement, _replacement_driver = self.new_managed_worker()
        provider = None

        def tamper():
            object.__setattr__(provider, "_worker", replacement)

        transport = AuthorityTamperingTransport(
            {"choices": [{"message": {"content": self.raw_text}}]},
            tamper,
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        try:
            with self.assertRaises(ProviderError) as caught:
                await provider.generate(self.request)
            self.assertEqual(caught.exception.code, "managed_worker_required")
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(replacement.state, WorkerState.STOPPED)
            self.assertIsNone(replacement.active_lease)
        finally:
            object.__setattr__(provider, "_worker", worker)
            if worker.active_lease is not None:
                await worker.abort(
                    worker.active_lease,
                    reason="test_cleanup",
                    deadline=asyncio.get_running_loop().time() + 1.0,
                )

    async def test_authority_changed_during_transport_denies_abort(self):
        worker, _driver = await self.managed_worker()
        replacement, _replacement_driver = self.new_managed_worker()
        provider = None

        def tamper():
            object.__setattr__(provider, "_worker", replacement)

        transport = AuthorityTamperingTransport(
            None,
            tamper,
            error=TimeoutError(
                "prompt=private-question token=private-provider-secret"
            ),
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        try:
            with self.assertRaises(ProviderError) as caught:
                await provider.generate(self.request)
            self.assertEqual(caught.exception.code, "managed_worker_required")
            self.assertEqual(str(caught.exception), "managed_worker_required")
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(replacement.state, WorkerState.STOPPED)
            self.assertIsNone(replacement.active_lease)
            for private_value in ("private-question", "private-provider-secret"):
                self.assertNotIn(private_value, str(caught.exception))
        finally:
            object.__setattr__(provider, "_worker", worker)
            if worker.active_lease is not None:
                await worker.abort(
                    worker.active_lease,
                    reason="test_cleanup",
                    deadline=asyncio.get_running_loop().time() + 1.0,
                )

    async def test_natural_full_response_confirms_idle_without_signals(self):
        worker, driver = await self.managed_worker()
        transport = FakeTransport(
            {"choices": [{"message": {"content": self.raw_text}}]}
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )

        generation = await provider.generate(self.request)

        self.assertEqual(generation.text, self.raw_text)
        self.assertEqual(worker.state, WorkerState.READY_IDLE)
        self.assertIsNone(worker.active_lease)
        self.assertEqual(driver.terms, [])
        self.assertEqual(driver.kills, [])
        self.assertEqual(transport.calls[0][0], worker.epoch.endpoint)

    async def test_provider_restarts_stopped_worker_only_on_explicit_preparation(self):
        driver = FakeManagedProcessDriver()
        loop = asyncio.get_running_loop()
        epoch_ids = iter(("provider-epoch-1", "provider-epoch-2"))
        ports = iter((8081, 8082))
        worker = ManagedLlamaWorker(
            driver=driver,
            artifact_verifier=FakeManagedArtifactVerifier(),
            launch_spec=WorkerLaunchSpec(
                binary_path="/Applications/Wayline/llama-server",
                model_path="/Applications/Wayline/wayline.gguf",
                binary_sha256="a" * 64,
                model_sha256=self.verifier.manifest.model_sha256,
            ),
            clock=loop.time,
            epoch_id_factory=lambda: next(epoch_ids),
            generation_id_factory=lambda: "provider-generation-1",
            port_factory=lambda: next(ports),
            launch_authority=WorkerLaunchAuthority(),
        )
        transport = FakeTransport(error=TimeoutError("ambiguous peer state"))
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )

        first_epoch = await provider.begin_preparation(deadline=loop.time() + 1.0)
        with self.assertRaises(ProviderError) as failed:
            await provider.generate(self.request)
        self.assertEqual(failed.exception.code, "transport_error")
        self.assertEqual(worker.state, WorkerState.STOPPED)
        self.assertEqual(len(transport.calls), 1)

        with self.assertRaises(ProviderError) as stopped:
            await provider.generate(self.request)
        self.assertEqual(stopped.exception.code, "worker_not_ready")
        self.assertEqual(len(transport.calls), 1)

        second_epoch = await provider.begin_preparation(deadline=loop.time() + 1.0)
        self.assertNotEqual(second_epoch.epoch_id, first_epoch.epoch_id)
        self.assertNotEqual(second_epoch.port, first_epoch.port)
        self.assertEqual(len(driver.starts), 2)

    async def test_starting_and_stopping_workers_are_unsafe_for_preparation(self):
        loop = asyncio.get_running_loop()
        starting_driver = FakeManagedProcessDriver()
        starting_driver.block_start = True
        starting_worker, _driver = self.new_managed_worker(starting_driver)
        starting_provider = LlamaCppProvider(
            self.verifier.manifest,
            FakeTransport({}),
            worker=starting_worker,
        )
        start_task = asyncio.create_task(
            starting_worker.begin_preparation(deadline=loop.time() + 1.0)
        )
        await starting_driver.start_started.wait()
        with self.assertRaises(ProviderError) as starting:
            await starting_provider.begin_preparation(deadline=loop.time() + 1.0)
        self.assertEqual(starting.exception.code, "worker_unsafe_state")
        starting_driver.start_release.set()
        await start_task
        await starting_worker.shutdown(deadline=loop.time() + 1.0)

        stopping_driver = FakeManagedProcessDriver()
        stopping_driver.block_wait = True
        stopping_worker, _driver = await self.managed_worker(stopping_driver)
        stopping_provider = LlamaCppProvider(
            self.verifier.manifest,
            FakeTransport({}),
            worker=stopping_worker,
        )
        lease = await stopping_worker.acquire(
            self.request.prompt_sha256,
            ready_deadline=loop.time() + 1.0,
        )
        stop_task = asyncio.create_task(
            stopping_worker.abort(
                lease,
                reason="test",
                deadline=loop.time() + 1.0,
            )
        )
        await stopping_driver.wait_started.wait()
        with self.assertRaises(ProviderError) as stopping:
            await stopping_provider.begin_preparation(deadline=loop.time() + 1.0)
        self.assertEqual(stopping.exception.code, "worker_unsafe_state")
        stopping_driver.wait_release.set()
        await stop_task

    async def test_stuck_wait_closed_keeps_busy_worker_unsafe(self):
        reader = asyncio.StreamReader()
        writer = DelayedCloseWriter()
        worker, driver = await self.managed_worker()

        async def open_connection(host, port, **kwargs):
            return reader, writer

        with patch.object(llama_cpp.asyncio, "open_connection", new=open_connection):
            provider = LlamaCppProvider(
                self.verifier.manifest,
                StdlibAsyncJsonTransport(
                    credential_resolver=driver.resolve_transport_credentials,
                ),
                worker=worker,
            )
            call = asyncio.create_task(provider.generate(self.request))
            await writer.request_written.wait()
            call.cancel()
            await writer.cleanup_started.wait()
            self.assertEqual(worker.state, WorkerState.BUSY)

            with self.assertRaises(ProviderError) as caught:
                await provider.begin_preparation(
                    deadline=asyncio.get_running_loop().time() + 1.0
                )
            self.assertEqual(caught.exception.code, "worker_unsafe_state")

            writer.cleanup_release.set()
            with self.assertRaises(asyncio.CancelledError):
                await call

    async def test_ambiguous_transport_failure_hard_stops_and_reaps_worker(self):
        worker, driver = await self.managed_worker()
        provider = LlamaCppProvider(
            self.verifier.manifest,
            FakeTransport(error=TimeoutError("ambiguous peer state")),
            worker=worker,
        )

        with self.assertRaises(ProviderError) as caught:
            await provider.generate(self.request)

        self.assertEqual(caught.exception.code, "transport_error")
        self.assertEqual(worker.state, WorkerState.STOPPED)
        self.assertEqual(driver.terms, [5101])
        self.assertEqual(driver.waits[0][0], 5101)

    async def test_unconfirmed_reap_quarantines_and_denies_future_post(self):
        driver = FakeManagedProcessDriver(wait_results=(None, None))
        worker, _driver = await self.managed_worker(driver)
        provider = LlamaCppProvider(
            self.verifier.manifest,
            FakeTransport(error=TimeoutError("ambiguous peer state")),
            worker=worker,
        )

        with self.assertRaises(ProviderError) as caught:
            await provider.generate(self.request)
        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(worker.state, WorkerState.QUARANTINED)

        followup_transport = FakeTransport(
            {"choices": [{"message": {"content": self.raw_text}}]}
        )
        followup = LlamaCppProvider(
            self.verifier.manifest,
            followup_transport,
            worker=worker,
        )
        with self.assertRaises(ProviderError) as denied:
            await followup.generate(self.request)
        self.assertEqual(denied.exception.code, "worker_quarantined")
        self.assertEqual(followup_transport.calls, [])

    async def test_repeated_cancellation_shields_reap_then_reraises_cancelled(self):
        driver = FakeManagedProcessDriver()
        driver.block_wait = True
        worker, _driver = await self.managed_worker(driver)
        active_transport = GatedAsyncTransport(
            {"choices": [{"message": {"content": self.raw_text}}]},
            ActiveCallTracker(),
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            active_transport,
            worker=worker,
        )
        call = asyncio.create_task(provider.generate(self.request))
        followup = None
        try:
            await asyncio.wait_for(active_transport.started.wait(), timeout=1.0)
            call.cancel()
            await asyncio.wait_for(driver.wait_started.wait(), timeout=1.0)
            call.cancel()
            await asyncio.sleep(0)
            self.assertFalse(call.done())

            followup_transport = FakeTransport(
                {"choices": [{"message": {"content": self.raw_text}}]}
            )
            followup = asyncio.create_task(
                LlamaCppProvider(
                    self.verifier.manifest,
                    followup_transport,
                    worker=worker,
                ).generate(self.request)
            )
            await asyncio.sleep(0)
            self.assertEqual(followup_transport.calls, [])

            driver.wait_release.set()
            with self.assertRaises(asyncio.CancelledError):
                await call
            with self.assertRaises(ProviderError) as stopped:
                await followup
            self.assertEqual(stopped.exception.code, "worker_not_ready")
            self.assertEqual(followup_transport.calls, [])
        finally:
            active_transport.release.set()
            driver.wait_release.set()
            pending = [task for task in (call, followup) if task is not None]
            await asyncio.gather(*pending, return_exceptions=True)

        self.assertEqual(worker.state, WorkerState.STOPPED)
        self.assertEqual(driver.terms, [5101])

    async def test_peer_eof_waits_for_child_reap_and_blocks_next_post(self):
        reader = asyncio.StreamReader()
        reader.feed_eof()
        writer = DelayedCloseWriter()
        driver = FakeManagedProcessDriver()
        driver.block_wait = True
        worker, _driver = await self.managed_worker(driver)

        async def open_connection(host, port, **kwargs):
            return reader, writer

        with patch.object(llama_cpp.asyncio, "open_connection", new=open_connection):
            provider = LlamaCppProvider(
                self.verifier.manifest,
                StdlibAsyncJsonTransport(
                    credential_resolver=driver.resolve_transport_credentials,
                ),
                worker=worker,
            )
            call = asyncio.create_task(provider.generate(self.request))
            followup = None
            try:
                await asyncio.wait_for(writer.cleanup_started.wait(), timeout=1.0)
                writer.cleanup_release.set()
                await asyncio.wait_for(driver.wait_started.wait(), timeout=1.0)
                self.assertFalse(call.done())

                followup_transport = FakeTransport(
                    {"choices": [{"message": {"content": self.raw_text}}]}
                )
                followup = asyncio.create_task(
                    LlamaCppProvider(
                        self.verifier.manifest,
                        followup_transport,
                        worker=worker,
                    ).generate(self.request)
                )
                await asyncio.sleep(0)
                self.assertEqual(followup_transport.calls, [])

                driver.wait_release.set()
                with self.assertRaises(ProviderError) as eof_error:
                    await call
                self.assertEqual(eof_error.exception.code, "invalid_provider_response")
                with self.assertRaises(ProviderError) as stopped:
                    await followup
                self.assertEqual(stopped.exception.code, "worker_not_ready")
                self.assertEqual(followup_transport.calls, [])
            finally:
                writer.cleanup_release.set()
                driver.wait_release.set()
                pending = [task for task in (call, followup) if task is not None]
                await asyncio.gather(*pending, return_exceptions=True)

        self.assertEqual(worker.state, WorkerState.STOPPED)
        self.assertEqual(driver.terms, [5101])

    def test_prompt_receipt_is_deterministic_and_input_bound(self):
        again = build_slm_request(self.blueprint)
        self.assertEqual(self.request, again)
        self.assertTrue(validate_prompt_receipt(self.request))
        self.assertEqual(len(self.request.prompt_sha256), 64)
        self.assertNotIn("prompt_sha256", prompt_payload(self.request))
        self.assertEqual(prompt_payload(self.request)["inference"]["max_tokens"], 768)
        self.assertFalse(
            prompt_payload(self.request)["inference"]["chat_template_kwargs"]["enable_thinking"]
        )

        changed = replace(self.request, question=self.request.question + " changed")
        self.assertFalse(validate_prompt_receipt(changed))
        self.assertNotEqual(
            build_slm_request(replace(self.blueprint, prompt=self.blueprint.prompt + " changed")).prompt_sha256,
            self.request.prompt_sha256,
        )

    def test_prompt_receipt_is_exactly_the_rendered_messages_and_sent_inference(self):
        receipt = prompt_payload(self.request)
        rendered = slm_prompt.openai_messages(self.request)
        expected_user = (
            f"Question: {self.request.question}\n"
            f"Correct answer: {self.request.correct_answer}\n"
            f"Topic: {self.request.topic}"
        )
        self.assertIn("messages", receipt)
        self.assertEqual(
            rendered,
            [
                {"role": "system", "content": slm_prompt.SYSTEM_PROMPT},
                {"role": "user", "content": expected_user},
            ],
        )
        self.assertEqual(receipt["messages"], rendered)
        self.assertNotIn("question_id", json.dumps(receipt))

        same_prompt_new_id = replace(self.request, question_id="different-opaque-id")
        self.assertTrue(validate_prompt_receipt(same_prompt_new_id))
        self.assertEqual(
            slm_prompt.prompt_sha256(same_prompt_new_id),
            self.request.prompt_sha256,
        )

    def test_template_receipt_binds_user_labels_order_and_newlines(self):
        canonical = (
            "Question: {question}\n"
            "Correct answer: {correct_answer}\n"
            "Topic: {topic}"
        )

        def receipt_for(user_template):
            with patch.object(
                slm_prompt,
                "USER_PROMPT_TEMPLATE",
                user_template,
                create=True,
            ):
                return hashlib.sha256(
                    json.dumps(
                        slm_prompt._template_receipt_payload(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()

        baseline = receipt_for(canonical)
        self.assertEqual(baseline, PROMPT_TEMPLATE_SHA256)
        variants = (
            canonical.replace("Question:", "Problem:"),
            "Topic: {topic}\nQuestion: {question}\nCorrect answer: {correct_answer}",
            canonical.replace("\n", "\r\n"),
        )
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(receipt_for(variant), baseline)

    def test_runtime_receipts_derive_from_authoritative_model_manifest(self):
        payload = {
            "schemaVersion": "wayline.model-manifest.v1",
            "baseModelId": "unsloth/Qwen3-4B-bnb-4bit",
            "baseModelRevision": "0" * 40,
            "adapterId": "j2ampn/qwen3-4b-distractor-lora-v7",
            "adapterRevision": "1" * 40,
            "llamaCppRevision": "2" * 40,
            "quantization": "Q4_K_M",
            "ggufFileName": "wayline.gguf",
            "ggufSha256": "3" * 64,
            "promptSha256": PROMPT_TEMPLATE_SHA256,
            "tokenizerSha256": "4" * 64,
            "contextSize": 2048,
            "threadCount": 8,
            "platform": "macos-arm64",
        }
        manifest = ModelManifest.model_validate(payload)
        runtime = PinnedSlmManifest.from_model_manifest(
            manifest,
            registry_id="wayline-procedures-v1",
            max_response_bytes=16_384,
            max_tokens=768,
        )
        self.assertEqual(runtime.model_id, "wayline.gguf")
        self.assertEqual(runtime.model_sha256, "3" * 64)
        self.assertEqual(runtime.gguf_sha256, "3" * 64)
        self.assertEqual(
            runtime.adapter_identity_receipt_sha256,
            hashlib.sha256(json.dumps(
                {
                    "id": payload["adapterId"],
                    "kind": "adapter",
                    "revision": payload["adapterRevision"],
                    "schema": "wayline.identity-receipt.v1",
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            runtime.generator_identity_receipt_sha256,
            hashlib.sha256(json.dumps(
                {
                    "id": "llama.cpp",
                    "kind": "generator",
                    "revision": payload["llamaCppRevision"],
                    "schema": "wayline.identity-receipt.v1",
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")).hexdigest(),
        )
        self.assertFalse(hasattr(runtime, "adapter_sha256"))
        self.assertFalse(hasattr(runtime, "generator_sha256"))
        self.assertEqual(runtime.max_response_bytes, 16_384)
        self.assertEqual(runtime.max_tokens, 768)

        wrong_prompt = manifest.model_copy(update={"prompt_sha256": "f" * 64})
        with self.assertRaises(ValueError):
            PinnedSlmManifest.from_model_manifest(
                wrong_prompt,
                registry_id="wayline-procedures-v1",
                max_response_bytes=16_384,
                max_tokens=768,
            )

    async def test_llama_provider_posts_bounded_deterministic_loopback_payload(self):
        worker, driver = await self.managed_worker()
        transport = FakeTransport(
            {"choices": [{"message": {"content": self.raw_text}}]}
        )
        provider = LlamaCppProvider(
            manifest=self.verifier.manifest,
            transport=transport,
            endpoint="http://127.0.0.1:8081/v1/chat/completions",
            clock=lambda: "2026-07-11T18:00:00Z",
            worker=worker,
        )
        generation = await provider.generate(self.request)

        self.assertEqual(generation.text, self.raw_text)
        self.assertEqual(generation.model_sha256, self.verifier.manifest.model_sha256)
        self.assertEqual(
            generation.adapter_identity_receipt_sha256,
            self.verifier.manifest.adapter_identity_receipt_sha256,
        )
        self.assertEqual(generation.gguf_sha256, self.verifier.manifest.gguf_sha256)
        self.assertEqual(
            generation.generator_identity_receipt_sha256,
            self.verifier.manifest.generator_identity_receipt_sha256,
        )
        self.assertEqual(generation.prompt_sha256, self.request.prompt_sha256)
        self.assertEqual(generation.generated_at_utc, "2026-07-11T18:00:00Z")

        self.assertEqual(len(transport.calls), 1)
        url, payload, timeout, maximum, transport_authority = transport.calls[0]
        self.assertEqual(url, "http://127.0.0.1:8081/v1/chat/completions")
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["seed"], 0)
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(payload["model"], self.verifier.manifest.model_id)
        receipt = prompt_payload(self.request)
        self.assertIn("messages", receipt)
        self.assertEqual(payload["messages"], receipt["messages"])
        for key, expected in receipt["inference"].items():
            self.assertEqual(payload[key], expected)
        self.assertEqual(timeout, 8.0)
        self.assertEqual(maximum, self.verifier.manifest.max_response_bytes)
        self.assertIs(
            transport_authority,
            driver.starts[0][0].transport_authority,
        )

    async def test_single_worker_is_process_wide_across_threads_and_event_loops(self):
        worker, _driver = await self.managed_worker()
        response = {"choices": [{"message": {"content": self.raw_text}}]}
        tracker = ActiveCallTracker()
        first_transport = CrossLoopGatedTransport(response, tracker)
        second_transport = CrossLoopGatedTransport(response, tracker)
        first_provider = LlamaCppProvider(
            self.verifier.manifest,
            first_transport,
            worker=worker,
        )
        second_provider = LlamaCppProvider(
            self.verifier.manifest,
            second_transport,
            worker=worker,
        )
        second_suspended = threading.Event()
        outcomes = []
        outcome_lock = threading.Lock()

        def run_provider(provider, suspended_event=None):
            async def invoke():
                if suspended_event is not None:
                    asyncio.get_running_loop().call_soon(suspended_event.set)
                return await provider.generate(self.request)

            try:
                outcome = asyncio.run(invoke())
            except BaseException as exc:
                outcome = exc
            with outcome_lock:
                outcomes.append(outcome)

        first_thread = threading.Thread(
            target=run_provider,
            args=(first_provider,),
            daemon=True,
        )
        second_thread = threading.Thread(
            target=run_provider,
            args=(second_provider, second_suspended),
            daemon=True,
        )
        threads = (first_thread, second_thread)
        second_thread_started = False
        first_thread.start()
        try:
            self.assertTrue(first_transport.started.wait(timeout=1.0))
            second_thread.start()
            second_thread_started = True
            self.assertTrue(second_suspended.wait(timeout=1.0))
            self.assertFalse(second_transport.started.is_set())

            first_transport.release()
            self.assertTrue(second_transport.started.wait(timeout=1.0))
            second_transport.release()
        finally:
            first_transport.release()
            second_transport.release()
            joinable = threads if second_thread_started else (first_thread,)
            for thread in joinable:
                thread.join(timeout=1.0)

        self.assertFalse(any(thread.is_alive() for thread in joinable))
        self.assertEqual(tracker.max_active, 1)
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(
            all(isinstance(outcome, RawSlmGeneration) for outcome in outcomes)
        )

    async def test_cancelling_a_queued_call_never_starts_it_later(self):
        worker, _driver = await self.managed_worker()
        response = {"choices": [{"message": {"content": self.raw_text}}]}
        tracker = ActiveCallTracker()
        active_transport = GatedAsyncTransport(response, tracker)
        queued_transport = GatedAsyncTransport(response, tracker)
        active_provider = LlamaCppProvider(
            self.verifier.manifest,
            active_transport,
            worker=worker,
        )
        queued_provider = LlamaCppProvider(
            self.verifier.manifest,
            queued_transport,
            worker=worker,
        )
        first = asyncio.create_task(active_provider.generate(self.request))
        second = None
        try:
            await asyncio.wait_for(active_transport.started.wait(), timeout=1.0)
            attempted = asyncio.Event()

            async def invoke_queued():
                attempted.set()
                return await queued_provider.generate(self.request)

            second = asyncio.create_task(invoke_queued())
            await asyncio.wait_for(attempted.wait(), timeout=1.0)
            second.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await second
            self.assertFalse(queued_transport.started.is_set())

            active_transport.release.set()
            await first
            self.assertEqual(queued_transport.calls, 0)
            self.assertEqual(tracker.max_active, 1)
        finally:
            active_transport.release.set()
            queued_transport.release.set()
            pending = [task for task in (first, second) if task is not None]
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_queued_third_call_recovers_when_granted_loop_stops(self):
        worker, _driver = await self.managed_worker()
        response = {"choices": [{"message": {"content": self.raw_text}}]}
        active_transport = GatedAsyncTransport(response, ActiveCallTracker())
        stale_transport = CrossLoopGatedTransport(response, ActiveCallTracker())
        next_transport = GatedAsyncTransport(response, ActiveCallTracker())
        active_provider = LlamaCppProvider(
            self.verifier.manifest,
            active_transport,
            worker=worker,
        )
        stale_provider = LlamaCppProvider(
            self.verifier.manifest,
            stale_transport,
            worker=worker,
        )
        next_provider = LlamaCppProvider(
            self.verifier.manifest,
            next_transport,
            worker=worker,
        )
        stale_loop_ready = threading.Event()
        stale_suspended = threading.Event()
        stale_loop_blocked = threading.Event()
        stop_stale_loop = threading.Event()
        stale_loop_stopped = threading.Event()
        cleanup_stale_loop = threading.Event()
        stale_state = {}

        def run_stale_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            def block_then_stop():
                stale_loop_blocked.set()
                stop_stale_loop.wait(timeout=1.0)
                loop.stop()

            async def invoke():
                loop.call_soon(stale_suspended.set)
                loop.call_soon(block_then_stop)
                return await stale_provider.generate(self.request)

            task = loop.create_task(invoke())
            stale_state.update(loop=loop, task=task)
            stale_loop_ready.set()
            loop.run_forever()
            stale_loop_stopped.set()
            cleanup_stale_loop.wait(timeout=1.0)
            stale_transport.release()
            for _turn in range(8):
                loop.call_soon(loop.stop)
                loop.run_forever()
            stale_state["terminated_on_resume"] = task.done()
            if not task.done():
                task.cancel()
            loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
            stale_state["cancelled_on_resume"] = task.cancelled()
            loop.close()

        first = asyncio.create_task(active_provider.generate(self.request))
        stale_thread = threading.Thread(target=run_stale_loop, daemon=True)
        third = None
        try:
            await asyncio.wait_for(active_transport.started.wait(), timeout=1.0)
            stale_thread.start()
            self.assertTrue(stale_loop_ready.wait(timeout=1.0))
            self.assertTrue(stale_suspended.wait(timeout=1.0))
            self.assertTrue(stale_loop_blocked.wait(timeout=1.0))

            next_suspended = asyncio.Event()

            async def invoke_next():
                asyncio.get_running_loop().call_soon(next_suspended.set)
                return await next_provider.generate(self.request)

            third = asyncio.create_task(invoke_next())
            await asyncio.wait_for(next_suspended.wait(), timeout=1.0)

            active_transport.release.set()
            await first
            stop_stale_loop.set()
            self.assertTrue(stale_loop_stopped.wait(timeout=1.0))

            await asyncio.wait_for(next_transport.started.wait(), timeout=1.0)
            next_transport.release.set()
            await third
            self.assertFalse(stale_transport.started.is_set())
        finally:
            active_transport.release.set()
            next_transport.release.set()
            stop_stale_loop.set()
            cleanup_stale_loop.set()
            if stale_thread.ident is not None:
                stale_thread.join(timeout=1.0)
            pending = [task for task in (first, third) if task is not None]
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        self.assertFalse(stale_thread.is_alive())
        self.assertTrue(stale_state["terminated_on_resume"])
        self.assertTrue(stale_state["cancelled_on_resume"])

    async def test_cancellation_waits_for_transport_cleanup_before_acknowledging(self):
        worker, _driver = await self.managed_worker()
        response = {"choices": [{"message": {"content": self.raw_text}}]}
        transport = GatedAsyncTransport(response, ActiveCallTracker())
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        call = asyncio.create_task(provider.generate(self.request))
        try:
            await asyncio.wait_for(transport.started.wait(), timeout=1.0)
            call.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await call
            self.assertTrue(transport.finished.is_set())
        finally:
            transport.release.set()
            await asyncio.gather(call, return_exceptions=True)

    async def test_cancellation_during_error_cleanup_remains_cancellation(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"not-http\r\n\r\n")
        reader.feed_eof()
        writer = DelayedCloseWriter()
        worker, driver = await self.managed_worker()

        async def open_connection(host, port, **kwargs):
            return reader, writer

        with patch.object(
            llama_cpp.asyncio,
            "open_connection",
            new=open_connection,
        ):
            provider = LlamaCppProvider(
                self.verifier.manifest,
                StdlibAsyncJsonTransport(
                    credential_resolver=driver.resolve_transport_credentials,
                ),
                worker=worker,
            )
            call = asyncio.create_task(provider.generate(self.request))
            await asyncio.wait_for(writer.cleanup_started.wait(), timeout=1.0)
            call.cancel()
            writer.cleanup_release.set()
            with self.assertRaises(asyncio.CancelledError):
                await call

        self.assertTrue(writer.aborted)
        self.assertTrue(writer.cleanup_finished.is_set())

    async def test_error_cleanup_cancellation_fits_orchestrator_ack_budget(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"not-http\r\n\r\n")
        reader.feed_eof()
        writer = DelayedCloseWriter()
        worker, driver = await self.managed_worker()

        async def open_connection(host, port, **kwargs):
            return reader, writer

        with patch.object(
            llama_cpp.asyncio,
            "open_connection",
            new=open_connection,
        ):
            provider = LlamaCppProvider(
                self.verifier.manifest,
                StdlibAsyncJsonTransport(
                    credential_resolver=driver.resolve_transport_credentials,
                ),
                worker=worker,
            )
            call = asyncio.create_task(provider.generate(self.request))
            await asyncio.wait_for(writer.cleanup_started.wait(), timeout=1.0)
            orchestrator = object.__new__(BatchPreparationOrchestrator)
            orchestrator._retained_tasks = set()
            loop = asyncio.get_running_loop()
            orchestrator._monotonic = loop.time
            orchestrator._sleeper = asyncio.sleep
            acknowledgment = asyncio.create_task(
                orchestrator._cancel_and_ack(
                    call,
                    deadline=loop.time() + 1.0,
                )
            )
            probe = asyncio.Event()
            loop.call_soon(probe.set)
            await probe.wait()
            self.assertFalse(acknowledgment.done())
            writer.cleanup_release.set()
            acknowledged = await acknowledgment

        try:
            self.assertTrue(acknowledged)
            self.assertTrue(call.cancelled())
            self.assertTrue(writer.aborted)
            self.assertTrue(writer.cleanup_finished.is_set())
            self.assertTrue(
                all(task.done() for task in orchestrator._retained_tasks)
            )
            await provider.begin_preparation(deadline=loop.time() + 1.0)
            followup = LlamaCppProvider(
                self.verifier.manifest,
                FakeTransport({
                    "choices": [{"message": {"content": self.raw_text}}]
                }),
                worker=worker,
            )
            self.assertEqual((await followup.generate(self.request)).text, self.raw_text)
        finally:
            writer.cleanup_release.set()
            await asyncio.gather(call, return_exceptions=True)

    async def test_delayed_close_callback_still_fits_orchestrator_ack_budget(self):
        reader = asyncio.StreamReader()
        writer = DelayedCloseWriter()
        worker, driver = await self.managed_worker()

        async def open_connection(host, port, **kwargs):
            return reader, writer

        with patch.object(
            llama_cpp.asyncio,
            "open_connection",
            new=open_connection,
        ):
            provider = LlamaCppProvider(
                self.verifier.manifest,
                StdlibAsyncJsonTransport(
                    credential_resolver=driver.resolve_transport_credentials,
                ),
                worker=worker,
            )
            call = asyncio.create_task(provider.generate(self.request))
            await asyncio.wait_for(writer.request_written.wait(), timeout=1.0)
            orchestrator = object.__new__(BatchPreparationOrchestrator)
            orchestrator._retained_tasks = set()
            loop = asyncio.get_running_loop()
            orchestrator._monotonic = loop.time
            orchestrator._sleeper = asyncio.sleep
            acknowledgment = asyncio.create_task(
                orchestrator._cancel_and_ack(
                    call,
                    deadline=loop.time() + 1.0,
                )
            )
            await asyncio.wait_for(writer.cleanup_started.wait(), timeout=1.0)
            probe = asyncio.Event()
            loop.call_soon(probe.set)
            await probe.wait()
            self.assertFalse(acknowledgment.done())
            writer.cleanup_release.set()
            acknowledged = await acknowledgment

        try:
            self.assertTrue(acknowledged)
            self.assertTrue(call.cancelled())
            self.assertTrue(writer.aborted)
        finally:
            writer.cleanup_release.set()
            await asyncio.gather(call, return_exceptions=True)

    async def test_repeated_cancellation_cannot_release_lease_before_close(self):
        reader = asyncio.StreamReader()
        writer = DelayedCloseWriter()
        response = {"choices": [{"message": {"content": self.raw_text}}]}
        followup_transport = GatedAsyncTransport(response, ActiveCallTracker())
        worker, driver = await self.managed_worker()

        async def open_connection(host, port, **kwargs):
            return reader, writer

        with patch.object(
            llama_cpp.asyncio,
            "open_connection",
            new=open_connection,
        ):
            provider = LlamaCppProvider(
                self.verifier.manifest,
                StdlibAsyncJsonTransport(
                    credential_resolver=driver.resolve_transport_credentials,
                ),
                worker=worker,
            )
            call = asyncio.create_task(provider.generate(self.request))
            await asyncio.wait_for(writer.request_written.wait(), timeout=1.0)
            call.cancel()
            await asyncio.wait_for(writer.cleanup_started.wait(), timeout=1.0)
            call.cancel()

            probe = asyncio.Event()
            asyncio.get_running_loop().call_soon(probe.set)
            await probe.wait()
            self.assertFalse(call.done())

            followup_suspended = asyncio.Event()

            async def invoke_followup():
                asyncio.get_running_loop().call_soon(followup_suspended.set)
                return await LlamaCppProvider(
                    self.verifier.manifest,
                    followup_transport,
                    worker=worker,
                ).generate(self.request)

            followup = asyncio.create_task(invoke_followup())
            await followup_suspended.wait()
            self.assertFalse(followup_transport.started.is_set())

            writer.cleanup_release.set()
            with self.assertRaises(asyncio.CancelledError):
                await call
            with self.assertRaises(ProviderError) as stopped:
                await followup
            self.assertEqual(stopped.exception.code, "worker_not_ready")
            self.assertFalse(followup_transport.started.is_set())

            await provider.begin_preparation(
                deadline=asyncio.get_running_loop().time() + 1.0
            )
            restarted = asyncio.create_task(
                LlamaCppProvider(
                    self.verifier.manifest,
                    followup_transport,
                    worker=worker,
                ).generate(self.request)
            )
            await asyncio.wait_for(followup_transport.started.wait(), timeout=1.0)
            followup_transport.release.set()
            await restarted

        self.assertTrue(writer.aborted)
        self.assertTrue(writer.cleanup_finished.is_set())

    async def test_background_transport_failure_is_consumed_and_releases_worker(self):
        worker, _driver = await self.managed_worker()
        response = {"choices": [{"message": {"content": self.raw_text}}]}
        tracker = ActiveCallTracker()
        failing = GatedAsyncTransport(
            response,
            tracker,
            error=RuntimeError(
                "prompt=private-question raw=private-output token=private-secret"
            ),
        )
        succeeding = GatedAsyncTransport(response, tracker)
        failing_provider = LlamaCppProvider(
            self.verifier.manifest,
            failing,
            worker=worker,
        )
        succeeding_provider = LlamaCppProvider(
            self.verifier.manifest,
            succeeding,
            worker=worker,
        )
        loop = asyncio.get_running_loop()
        old_handler = loop.get_exception_handler()
        unhandled = []
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        first = asyncio.create_task(failing_provider.generate(self.request))
        second = None
        try:
            await asyncio.wait_for(failing.started.wait(), timeout=1.0)
            failing.release.set()
            with self.assertRaises(ProviderError) as caught:
                await first
            self.assertEqual(str(caught.exception), "transport_error")
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)

            await failing_provider.begin_preparation(deadline=loop.time() + 1.0)
            second = asyncio.create_task(succeeding_provider.generate(self.request))
            await asyncio.wait_for(succeeding.started.wait(), timeout=1.0)
            succeeding.release.set()
            await second
            self.assertEqual(unhandled, [])
            self.assertEqual(tracker.max_active, 1)
        finally:
            loop.set_exception_handler(old_handler)
            failing.release.set()
            succeeding.release.set()
            pending = [task for task in (first, second) if task is not None]
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_recorded_provider_returns_only_the_matching_frozen_response(self):
        generation = self.verifier.fixture_generation(self.blueprint, "accepted.json")
        provider = RecordedDistractorProvider({self.request.question_id: generation})
        self.assertEqual(await provider.generate(self.request), generation)

        with self.assertRaises(ProviderError) as missing:
            await provider.generate(replace(self.request, question_id="missing-question"))
        self.assertEqual(missing.exception.code, "recording_not_found")

        mismatched = RecordedDistractorProvider(
            {self.request.question_id: replace(generation, prompt_sha256="f" * 64)}
        )
        with self.assertRaises(ProviderError) as forged:
            await mismatched.generate(self.request)
        self.assertEqual(forged.exception.code, "recording_receipt_mismatch")

    def test_rejects_non_loopback_or_credentialed_endpoints(self):
        worker, _driver = self.new_managed_worker()
        invalid = (
            "https://example.com/v1/chat/completions",
            "http://192.168.1.5:8080/v1/chat/completions",
            "http://localhost:8080/v1/chat/completions",
            "http://user:pass@127.0.0.1:8080/v1/chat/completions",
            "file:///tmp/socket",
        )
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint), self.assertRaises(ProviderError):
                LlamaCppProvider(
                    manifest=self.verifier.manifest,
                    transport=FakeTransport({}),
                    endpoint=endpoint,
                    worker=worker,
                )

        with self.assertRaises(ProviderError) as manifest_error:
            LlamaCppProvider(
                manifest=replace(self.verifier.manifest, max_tokens=512),
                transport=FakeTransport({}),
                worker=worker,
            )
        self.assertEqual(manifest_error.exception.code, "manifest_prompt_mismatch")

        with self.assertRaises(ProviderError) as malformed_port:
            LlamaCppProvider(
                manifest=self.verifier.manifest,
                transport=FakeTransport({}),
                endpoint=(
                    "http://127.0.0.1:private-provider-secret/"
                    "v1/chat/completions"
                ),
                worker=worker,
            )
        self.assertEqual(str(malformed_port.exception), "non_loopback_endpoint")
        self.assertIsNone(malformed_port.exception.__cause__)
        self.assertIsNone(malformed_port.exception.__context__)

    async def test_stdlib_transport_rejects_redirect_without_followup(self):
        client_stream, server_stream = await _socketpair_streams()
        worker, driver = await self.managed_worker()
        connection_calls = []
        handler_done = asyncio.Event()

        async def open_connection(host, port, **kwargs):
            connection_calls.append((host, port, kwargs))
            return client_stream

        async def handler():
            reader, writer = server_stream
            try:
                await _read_http_request(reader)
                await _write_json_response(
                    writer,
                    b"{}",
                    status=b"302 Found",
                    headers=(b"Location: https://external.invalid/private",),
                )
            finally:
                writer.close()
                await writer.wait_closed()
                handler_done.set()

        handler_task = asyncio.create_task(handler())
        try:
            with patch.object(
                llama_cpp.asyncio,
                "open_connection",
                new=open_connection,
            ):
                provider = LlamaCppProvider(
                    self.verifier.manifest,
                    StdlibAsyncJsonTransport(
                        credential_resolver=driver.resolve_transport_credentials,
                    ),
                    worker=worker,
                )
                with self.assertRaises(ProviderError) as caught:
                    await provider.generate(self.request)
            self.assertEqual(caught.exception.code, "provider_redirect_rejected")
            await asyncio.wait_for(handler_done.wait(), timeout=1.0)
            self.assertEqual(
                connection_calls,
                [("127.0.0.1", 8081, {"limit": llama_cpp._HTTP_STREAM_LIMIT})],
            )
        finally:
            client_stream[1].close()
            await client_stream[1].wait_closed()
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)

    async def test_stdlib_transport_uses_direct_loopback_despite_proxy_environment(self):
        client_stream, server_stream = await _socketpair_streams()
        worker, driver = await self.managed_worker()
        response = json.dumps(
            {
                "model": driver.credentials.model_alias,
                "choices": [{"message": {"content": self.raw_text}}],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        captured = []
        connection_calls = []
        handler_done = asyncio.Event()

        async def open_connection(host, port, **kwargs):
            connection_calls.append((host, port, kwargs))
            return client_stream

        async def handler():
            reader, writer = server_stream
            try:
                captured.append(await _read_http_request(reader))
                await _write_json_response(writer, response)
            finally:
                writer.close()
                await writer.wait_closed()
                handler_done.set()

        handler_task = asyncio.create_task(handler())
        try:
            with (
                patch.dict(
                    os.environ,
                    {
                        "HTTP_PROXY": "http://proxy.invalid:8080",
                        "HTTPS_PROXY": "http://proxy.invalid:8443",
                    },
                    clear=False,
                ),
                patch.object(
                    llama_cpp.asyncio,
                    "open_connection",
                    new=open_connection,
                ),
            ):
                provider = LlamaCppProvider(
                    self.verifier.manifest,
                    StdlibAsyncJsonTransport(
                        credential_resolver=driver.resolve_transport_credentials,
                    ),
                    worker=worker,
                )
                generation = await provider.generate(self.request)

            await asyncio.wait_for(handler_done.wait(), timeout=1.0)
            self.assertEqual(generation.text, self.raw_text)
            self.assertEqual(len(captured), 1)
            self.assertEqual(
                connection_calls,
                [("127.0.0.1", 8081, {"limit": llama_cpp._HTTP_STREAM_LIMIT})],
            )
            request_headers, request_body = captured[0]
            self.assertIn(b"POST /v1/chat/completions HTTP/1.1", request_headers)
            authorization_line = (
                b"Authorization: Bearer "
                + driver.credentials.bearer_token.encode("ascii")
            )
            self.assertEqual(request_headers.count(b"Authorization:"), 1)
            self.assertEqual(request_headers.count(authorization_line), 1)
            self.assertEqual(json.loads(request_body), {
                "model": driver.credentials.model_alias,
                "messages": prompt_payload(self.request)["messages"],
                **prompt_payload(self.request)["inference"],
            })
            public_receipts = repr(generation) + repr(worker.epoch)
            self.assertNotIn(driver.credentials.bearer_token, public_receipts)
            self.assertNotIn(driver.credentials.model_alias, public_receipts)
        finally:
            client_stream[1].close()
            await client_stream[1].wait_closed()
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)

    async def test_server_rejection_of_wrong_bearer_hard_stops_without_leak(self):
        client_stream, server_stream = await _socketpair_streams()
        private_wrong_key = "private-wrong-bearer"
        driver = FakeManagedProcessDriver(bearer_token=private_wrong_key)
        worker, _driver = await self.managed_worker(driver)
        captured = []

        async def open_connection(host, port, **kwargs):
            return client_stream

        async def handler():
            reader, writer = server_stream
            try:
                captured.append(await _read_http_request(reader))
                await _write_json_response(
                    writer,
                    b"{}",
                    status=b"401 Unauthorized",
                )
            finally:
                writer.close()
                await writer.wait_closed()

        handler_task = asyncio.create_task(handler())
        try:
            with patch.object(
                llama_cpp.asyncio,
                "open_connection",
                new=open_connection,
            ):
                provider = LlamaCppProvider(
                    self.verifier.manifest,
                    StdlibAsyncJsonTransport(
                        credential_resolver=driver.resolve_transport_credentials,
                    ),
                    worker=worker,
                )
                with self.assertRaises(ProviderError) as caught:
                    await provider.generate(self.request)

            self.assertEqual(caught.exception.code, "managed_worker_required")
            self.assertNotIn(private_wrong_key, repr(caught.exception))
            self.assertEqual(worker.state, WorkerState.STOPPED)
            self.assertEqual(len(captured), 1)
            self.assertEqual(
                captured[0][0].count(b"Authorization:"),
                1,
            )
        finally:
            client_stream[1].close()
            await client_stream[1].wait_closed()
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)

    async def test_response_alias_must_match_authority_selected_alias(self):
        client_stream, server_stream = await _socketpair_streams()
        driver = FakeManagedProcessDriver(model_alias="private-expected-alias")
        worker, _driver = await self.managed_worker(driver)
        response = json.dumps(
            {
                "model": "wrong-response-alias",
                "choices": [{"message": {"content": self.raw_text}}],
            },
            separators=(",", ":"),
        ).encode("utf-8")

        async def open_connection(host, port, **kwargs):
            return client_stream

        async def handler():
            reader, writer = server_stream
            try:
                await _read_http_request(reader)
                await _write_json_response(writer, response)
            finally:
                writer.close()
                await writer.wait_closed()

        handler_task = asyncio.create_task(handler())
        try:
            with patch.object(
                llama_cpp.asyncio,
                "open_connection",
                new=open_connection,
            ):
                provider = LlamaCppProvider(
                    self.verifier.manifest,
                    StdlibAsyncJsonTransport(
                        credential_resolver=driver.resolve_transport_credentials,
                    ),
                    worker=worker,
                )
                with self.assertRaises(ProviderError) as caught:
                    await provider.generate(self.request)

            self.assertEqual(caught.exception.code, "manifest_worker_mismatch")
            self.assertNotIn(driver.credentials.model_alias, repr(caught.exception))
            self.assertEqual(worker.state, WorkerState.STOPPED)
        finally:
            client_stream[1].close()
            await client_stream[1].wait_closed()
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)

    async def test_success_response_requires_authority_selected_model_alias(self):
        client_stream, server_stream = await _socketpair_streams()
        driver = FakeManagedProcessDriver(model_alias="private-required-alias")
        worker, _driver = await self.managed_worker(driver)
        response = json.dumps(
            {"choices": [{"message": {"content": self.raw_text}}]},
            separators=(",", ":"),
        ).encode("utf-8")

        async def open_connection(host, port, **kwargs):
            return client_stream

        async def handler():
            reader, writer = server_stream
            try:
                await _read_http_request(reader)
                await _write_json_response(writer, response)
            finally:
                writer.close()
                await writer.wait_closed()

        handler_task = asyncio.create_task(handler())
        try:
            with patch.object(
                llama_cpp.asyncio,
                "open_connection",
                new=open_connection,
            ):
                provider = LlamaCppProvider(
                    self.verifier.manifest,
                    StdlibAsyncJsonTransport(
                        credential_resolver=driver.resolve_transport_credentials,
                    ),
                    worker=worker,
                )
                with self.assertRaises(ProviderError) as caught:
                    await provider.generate(self.request)

            self.assertEqual(caught.exception.code, "manifest_worker_mismatch")
            self.assertEqual(worker.state, WorkerState.STOPPED)
            self.assertNotIn(driver.credentials.model_alias, repr(caught.exception))
        finally:
            client_stream[1].close()
            await client_stream[1].wait_closed()
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)

    async def test_duplicate_json_model_keys_are_hard_identity_failure(self):
        client_stream, server_stream = await _socketpair_streams()
        driver = FakeManagedProcessDriver(model_alias="private-unique-alias")
        worker, _driver = await self.managed_worker(driver)
        response = (
            "{\"model\":\"attacker-shadow-alias\",\"model\":"
            + json.dumps(driver.credentials.model_alias)
            + ",\"choices\":[{\"message\":{\"content\":"
            + json.dumps(self.raw_text)
            + "}}]}"
        ).encode("utf-8")

        async def open_connection(host, port, **kwargs):
            return client_stream

        async def handler():
            reader, writer = server_stream
            try:
                await _read_http_request(reader)
                await _write_json_response(writer, response)
            finally:
                writer.close()
                await writer.wait_closed()

        handler_task = asyncio.create_task(handler())
        try:
            with patch.object(
                llama_cpp.asyncio,
                "open_connection",
                new=open_connection,
            ):
                provider = LlamaCppProvider(
                    self.verifier.manifest,
                    StdlibAsyncJsonTransport(
                        credential_resolver=driver.resolve_transport_credentials,
                    ),
                    worker=worker,
                )
                with self.assertRaises(ProviderError) as caught:
                    await provider.generate(self.request)

            self.assertEqual(caught.exception.code, "manifest_worker_mismatch")
            self.assertEqual(worker.state, WorkerState.STOPPED)
            self.assertNotIn(driver.credentials.model_alias, repr(caught.exception))
        finally:
            client_stream[1].close()
            await client_stream[1].wait_closed()
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)

    async def test_root_model_identity_precedes_unrelated_duplicate_failures(self):
        expected_alias = "private-root-identity-alias"
        bodies = (
            b'[{"choices":[],"choices":[]}]',
            (
                b'{"choices":[],"choices":[],"model":"first",'
                b'"model":"private-root-identity-alias"}'
            ),
            b'{"choices":[],"choices":[]}',
            (
                b'{"choices":[],"choices":[],'
                b'"model":7}'
            ),
            (
                b'{"choices":[],"choices":[],'
                b'"model":"wrong-root-alias"}'
            ),
        )

        async def invoke(body):
            client_stream, server_stream = await _socketpair_streams()
            driver = FakeManagedProcessDriver(model_alias=expected_alias)
            worker, _driver = await self.managed_worker(driver)

            async def open_connection(host, port, **kwargs):
                return client_stream

            async def handler():
                reader, writer = server_stream
                try:
                    await _read_http_request(reader)
                    await _write_json_response(writer, body)
                finally:
                    writer.close()
                    await writer.wait_closed()

            handler_task = asyncio.create_task(handler())
            try:
                with patch.object(
                    llama_cpp.asyncio,
                    "open_connection",
                    new=open_connection,
                ):
                    provider = LlamaCppProvider(
                        self.verifier.manifest,
                        StdlibAsyncJsonTransport(
                            credential_resolver=(
                                driver.resolve_transport_credentials
                            ),
                        ),
                        worker=worker,
                    )
                    try:
                        await provider.generate(self.request)
                    except ProviderError as error:
                        return error.code, worker.state
                    return None, worker.state
            finally:
                client_stream[1].close()
                await client_stream[1].wait_closed()
                if not handler_task.done():
                    handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)

        for body in bodies:
            with self.subTest(body=body):
                code, worker_state = await invoke(body)
                self.assertEqual(code, "manifest_worker_mismatch")
                self.assertEqual(worker_state, WorkerState.STOPPED)

    async def test_duplicate_nonidentity_json_keys_remain_malformed_response(self):
        driver = FakeManagedProcessDriver()
        transport = StdlibAsyncJsonTransport(
            credential_resolver=driver.resolve_transport_credentials,
        )
        bodies = (
            b'{"model":"alias","choices":[],"choices":[]}',
            b'{"choices":[],"choices":[],"model":"alias"}',
            (
                b'{"model":"alias","choices":[{"message":'
                b'{"model":"inner-a","model":"inner-b"}}]}'
            ),
            (
                b'{"choices":[{"message":{"model":"inner-a",'
                b'"model":"inner-b"}}],"model":"alias"}'
            ),
        )

        for body in bodies:
            reader = asyncio.StreamReader()
            reader.feed_data(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + body
            )
            reader.feed_eof()
            with self.subTest(body=body), self.assertRaisesRegex(
                ProviderError,
                "^invalid_provider_response$",
            ):
                await transport._read_response(
                    reader,
                    16_384,
                    expected_model_alias="alias",
                )

    async def test_stdlib_transport_rejects_declared_oversized_response(self):
        client_stream, server_stream = await _socketpair_streams()
        worker, driver = await self.managed_worker()
        handler_done = asyncio.Event()

        async def open_connection(host, port, **kwargs):
            return client_stream

        async def handler():
            reader, writer = server_stream
            try:
                await _read_http_request(reader)
                writer.write(b"\r\n".join((
                    b"HTTP/1.1 200 OK",
                    (
                        "Content-Length: "
                        f"{self.verifier.manifest.max_response_bytes + 1}"
                    ).encode("ascii"),
                    b"Connection: close",
                    b"",
                    b"",
                )))
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                handler_done.set()

        handler_task = asyncio.create_task(handler())
        try:
            with patch.object(
                llama_cpp.asyncio,
                "open_connection",
                new=open_connection,
            ):
                provider = LlamaCppProvider(
                    self.verifier.manifest,
                    StdlibAsyncJsonTransport(
                        credential_resolver=driver.resolve_transport_credentials,
                    ),
                    worker=worker,
                )
                with self.assertRaises(ProviderError) as caught:
                    await provider.generate(self.request)
            self.assertEqual(caught.exception.code, "provider_response_too_large")
            await asyncio.wait_for(handler_done.wait(), timeout=1.0)
        finally:
            client_stream[1].close()
            await client_stream[1].wait_closed()
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)

    async def test_stdlib_transport_timeout_aborts_connection_and_is_sanitized(self):
        client_stream, server_stream = await _socketpair_streams()
        worker, driver = await self.managed_worker()
        request_received = asyncio.Event()
        disconnected = asyncio.Event()

        async def open_connection(host, port, **kwargs):
            return client_stream

        async def handler():
            reader, writer = server_stream
            try:
                await _read_http_request(reader)
                request_received.set()
                await reader.read()
                disconnected.set()
            finally:
                writer.close()
                await writer.wait_closed()

        handler_task = asyncio.create_task(handler())
        try:
            with patch.object(
                llama_cpp.asyncio,
                "open_connection",
                new=open_connection,
            ):
                provider = LlamaCppProvider(
                    self.verifier.manifest,
                    StdlibAsyncJsonTransport(
                        credential_resolver=driver.resolve_transport_credentials,
                    ),
                    timeout_seconds=0.1,
                    worker=worker,
                )
                call = asyncio.create_task(provider.generate(self.request))
                await asyncio.wait_for(request_received.wait(), timeout=1.0)
                with self.assertRaises(ProviderError) as caught:
                    await call
            self.assertEqual(str(caught.exception), "transport_error")
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            await asyncio.wait_for(disconnected.wait(), timeout=1.0)
        finally:
            client_stream[1].close()
            await client_stream[1].wait_closed()
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)

    async def test_real_transport_confirms_local_close_before_ack(self):
        client_stream, server_stream = await _socketpair_streams()
        worker, driver = await self.managed_worker()
        request_received = asyncio.Event()
        disconnected = asyncio.Event()
        allow_response = asyncio.Event()
        handler_done = asyncio.Event()
        response = json.dumps(
            {"choices": [{"message": {"content": self.raw_text}}]},
            separators=(",", ":"),
        ).encode("utf-8")

        async def open_connection(host, port, **kwargs):
            return client_stream

        async def handler():
            reader, writer = server_stream
            eof_task = None
            response_task = None
            try:
                await _read_http_request(reader)
                request_received.set()
                eof_task = asyncio.create_task(reader.read())
                response_task = asyncio.create_task(allow_response.wait())
                done, pending = await asyncio.wait(
                    {eof_task, response_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if eof_task in done:
                    disconnected.set()
                else:
                    try:
                        await _write_json_response(writer, response)
                    except (ConnectionError, OSError):
                        pass
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            finally:
                for task in (eof_task, response_task):
                    if task is not None and not task.done():
                        task.cancel()
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
                handler_done.set()

        handler_task = asyncio.create_task(handler())
        patcher = patch.object(
            llama_cpp.asyncio,
            "open_connection",
            new=open_connection,
        )
        patcher.start()
        provider = LlamaCppProvider(
            self.verifier.manifest,
            StdlibAsyncJsonTransport(
                credential_resolver=driver.resolve_transport_credentials,
            ),
            worker=worker,
        )
        call = asyncio.create_task(provider.generate(self.request))
        closed = False
        try:
            await asyncio.wait_for(request_received.wait(), timeout=1.0)
            orchestrator = object.__new__(BatchPreparationOrchestrator)
            orchestrator._retained_tasks = set()
            loop = asyncio.get_running_loop()
            orchestrator._monotonic = loop.time
            orchestrator._sleeper = asyncio.sleep
            acknowledged = await orchestrator._cancel_and_ack(
                call,
                deadline=loop.time() + 1.0,
            )
            self.assertTrue(acknowledged)
            self.assertTrue(call.cancelled())
            self.assertTrue(client_stream[1].transport.is_closing())
            self.assertIsNone(client_stream[1].transport._sock)
            await asyncio.wait_for(disconnected.wait(), timeout=1.0)
            closed = True
        finally:
            allow_response.set()
            await asyncio.wait_for(handler_done.wait(), timeout=1.0)
            await asyncio.gather(call, return_exceptions=True)
            patcher.stop()
            client_stream[1].close()
            await client_stream[1].wait_closed()
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)

        self.assertTrue(closed)

    async def test_rejects_tampered_prompt_before_transport(self):
        worker, _driver = await self.managed_worker()
        transport = FakeTransport({})
        provider = LlamaCppProvider(
            self.verifier.manifest,
            transport,
            worker=worker,
        )
        with self.assertRaises(ProviderError) as error:
            await provider.generate(replace(self.request, prompt_sha256="f" * 64))
        self.assertEqual(error.exception.code, "prompt_receipt_mismatch")
        self.assertEqual(transport.calls, [])

    async def test_rejects_malformed_or_oversized_responses(self):
        worker, _driver = await self.managed_worker()
        malformed = (
            None,
            {},
            {"choices": []},
            {"choices": [{"message": {"content": 7}}]},
            {"choices": [{"message": {"content": "{}"}}, {"message": {"content": "{}"}}]},
        )
        for response in malformed:
            with self.subTest(response=response):
                provider = LlamaCppProvider(
                    self.verifier.manifest,
                    FakeTransport(response),
                    worker=worker,
                )
                with self.assertRaises(ProviderError) as error:
                    await provider.generate(self.request)
                self.assertEqual(error.exception.code, "invalid_provider_response")

        oversized = "x" * (self.verifier.manifest.max_response_bytes + 1)
        provider = LlamaCppProvider(
            self.verifier.manifest,
            FakeTransport({"choices": [{"message": {"content": oversized}}]}),
            worker=worker,
        )
        with self.assertRaises(ProviderError) as error:
            await provider.generate(self.request)
        self.assertEqual(error.exception.code, "provider_response_too_large")

    async def test_lone_surrogate_content_is_a_stable_typed_failure(self):
        worker, _driver = await self.managed_worker()
        provider = LlamaCppProvider(
            self.verifier.manifest,
            FakeTransport({"choices": [{"message": {"content": "\ud800"}}]}),
            worker=worker,
        )

        with self.assertRaises(Exception) as caught:
            await provider.generate(self.request)

        self.assertIsInstance(caught.exception, ProviderError)
        self.assertEqual(caught.exception.code, "invalid_provider_response")

    async def test_transport_failure_is_typed_without_leaking_detail(self):
        worker, _driver = await self.managed_worker()
        private_detail = (
            "prompt=private-question raw=private-model-output "
            "api_key=private-provider-secret"
        )
        provider = LlamaCppProvider(
            self.verifier.manifest,
            FakeTransport(error=TimeoutError(private_detail)),
            worker=worker,
        )
        with self.assertRaises(ProviderError) as error:
            await provider.generate(self.request)
        self.assertEqual(error.exception.code, "transport_error")
        self.assertEqual(str(error.exception), "transport_error")
        self.assertIsNone(error.exception.__cause__)
        self.assertIsNone(error.exception.__context__)
        for private_value in (
            "private-question",
            "private-model-output",
            "private-provider-secret",
        ):
            self.assertNotIn(private_value, str(error.exception))

    def test_provider_dataclasses_are_immutable(self):
        request = SlmRequest("q-1", "question", "1", "topic", "a" * 64)
        generation = RawSlmGeneration(
            text="{}",
            model_sha256="1" * 64,
            prompt_sha256="a" * 64,
            generated_at_utc="2026-07-11T18:00:00Z",
        )
        with self.assertRaises(AttributeError):
            request.question = "changed"
        with self.assertRaises(AttributeError):
            generation.text = "changed"


if __name__ == "__main__":
    unittest.main()
