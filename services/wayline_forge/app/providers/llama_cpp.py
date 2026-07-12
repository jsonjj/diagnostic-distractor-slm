"""Bounded OpenAI-compatible client for a loopback llama.cpp worker."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import json
import inspect
import re
import threading
import time
from typing import Any, Protocol
from urllib.parse import urlparse

from .distractor import (
    PinnedSlmManifest,
    ProviderError,
    RawSlmGeneration,
    SlmRequest,
)
from ..llama_worker import (
    GenerationLease,
    ManagedLlamaWorker,
    WorkerEpochReceipt,
    WorkerError,
)
from ..slm_prompt import INFERENCE_PARAMETERS, prompt_payload, validate_prompt_receipt


class AsyncJsonTransport(Protocol):
    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
        transport_authority: object,
    ) -> Any: ...


class _GateWaiter:
    __slots__ = ("event", "loop", "state", "token")

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.event = asyncio.Event()
        self.token = object()
        self.state = "queued"


class _WorkerLease:
    __slots__ = ("_gate", "_released", "_token")

    def __init__(self, gate: "_ProcessWideWorkerGate", token: object) -> None:
        self._gate = gate
        self._token = token
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._gate._release(self._token)


class _ProcessWideWorkerGate:
    """A cancellation-safe FIFO mutex shared by every event loop and thread."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._owner: object | None = None
        self._waiters: deque[_GateWaiter] = deque()

    async def acquire(self) -> _WorkerLease:
        waiter = _GateWaiter(asyncio.get_running_loop())
        with self._guard:
            self._waiters.append(waiter)

        while True:
            with self._guard:
                self._prune_stopped_head_locked()
                if waiter.state == "cancelled":
                    raise asyncio.CancelledError
                if (
                    self._owner is None
                    and self._waiters
                    and self._waiters[0] is waiter
                ):
                    self._waiters.popleft()
                    waiter.state = "acquired"
                    self._owner = waiter.token
                    return _WorkerLease(self, waiter.token)
            try:
                await asyncio.wait_for(
                    waiter.event.wait(),
                    timeout=_GATE_RECHECK_SECONDS,
                )
            except TimeoutError:
                pass
            except BaseException:
                self._cancel(waiter)
                raise
            finally:
                waiter.event.clear()

    def _cancel(self, waiter: _GateWaiter) -> None:
        with self._guard:
            if waiter.state == "queued":
                waiter.state = "cancelled"
            should_notify = self._owner is None
            waiters = tuple(self._waiters) if should_notify else ()
        self._notify(waiters)

    def _release(self, token: object) -> None:
        with self._guard:
            if self._owner is not token:
                raise RuntimeError("single-worker gate released by non-owner")
            self._owner = None
            waiters = tuple(self._waiters)
        self._notify(waiters)

    def _prune_stopped_head_locked(self) -> None:
        while self._waiters:
            candidate = self._waiters[0]
            if (
                candidate.state == "queued"
                and not candidate.loop.is_closed()
                and candidate.loop.is_running()
            ):
                return
            self._waiters.popleft()
            candidate.state = "cancelled"

    @staticmethod
    def _notify(waiters: tuple[_GateWaiter, ...]) -> None:
        for waiter in waiters:
            if (
                waiter.state != "queued"
                or waiter.loop.is_closed()
                or not waiter.loop.is_running()
            ):
                continue
            try:
                waiter.loop.call_soon_threadsafe(waiter.event.set)
            except RuntimeError:
                pass


_SINGLE_WORKER = _ProcessWideWorkerGate()

_HTTP_STREAM_LIMIT = 65_536
_MAX_HEADER_BYTES = 16_384
_MAX_STATUS_LINE_BYTES = 1_024
_MAX_CHUNK_LINE_BYTES = 128
_CLOSE_TIMEOUT_SECONDS = 0.5
_GATE_RECHECK_SECONDS = 0.05
_HEADER_NAME = re.compile(rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_BEARER_TOKEN = re.compile(r"[A-Za-z0-9._~+/\-]{1,512}={0,2}\Z")
_MODEL_ALIAS = re.compile(r"[A-Za-z0-9._\-]{1,256}\Z")
_MANAGED_AUTHORITY_FAILURES = frozenset(
    {
        "invalid_transport_credentials",
        "managed_transport_required",
        "stale_generation_lease",
        "stale_transport_authority",
        "stale_worker_epoch",
    }
)


def _provider_boundary_code(code: str) -> str:
    """Keep lifecycle/auth failures on the orchestrator's hard-fail path."""

    if code in _MANAGED_AUTHORITY_FAILURES:
        return "managed_worker_required"
    return code


class _DuplicateJsonKey(ValueError):
    pass


class _RootModelIdentityMismatch(ValueError):
    pass


class _JsonObjectPairs:
    __slots__ = ("pairs",)

    def __init__(self, pairs: list[tuple[str, Any]]) -> None:
        self.pairs = tuple(pairs)


def _preserve_json_object(pairs: list[tuple[str, Any]]) -> _JsonObjectPairs:
    return _JsonObjectPairs(pairs)


def _require_root_model_identity(
    decoded: Any,
    *,
    expected_model_alias: str,
) -> None:
    if not isinstance(decoded, _JsonObjectPairs):
        raise _RootModelIdentityMismatch
    model_values = tuple(
        value for key, value in decoded.pairs if key == "model"
    )
    if (
        len(model_values) != 1
        or not isinstance(model_values[0], str)
        or model_values[0] != expected_model_alias
    ):
        raise _RootModelIdentityMismatch


def _materialize_strict_json(value: Any) -> Any:
    if isinstance(value, _JsonObjectPairs):
        result: dict[str, Any] = {}
        for key, child in value.pairs:
            if key in result:
                raise _DuplicateJsonKey
            result[key] = _materialize_strict_json(child)
        return result
    if isinstance(value, list):
        return [
            _materialize_strict_json(child)
            for child in value
        ]
    return value


def _response_attestation_protocol():
    """Bind a validated response to one transport call inside this process.

    This capability closes accidental/custom-transport bypasses; Python code
    already executing in this process remains inside the trust boundary.
    """

    issuer_authority = object()

    class _AuthorityBoundJsonResponse:
        __slots__ = (
            "_issuer_authority",
            "_response",
            "_transport",
            "_transport_authority",
        )

        def __init__(
            self,
            response: Any,
            *,
            transport: object,
            transport_authority: object,
        ) -> None:
            object.__setattr__(self, "_issuer_authority", issuer_authority)
            object.__setattr__(self, "_response", response)
            object.__setattr__(self, "_transport", transport)
            object.__setattr__(
                self,
                "_transport_authority",
                transport_authority,
            )

        def __setattr__(self, name: str, value: object) -> None:
            raise AttributeError("response attestation is read-only")

        def __repr__(self) -> str:
            return "_AuthorityBoundJsonResponse()"

    def issue(
        response: Any,
        *,
        transport: object,
        transport_authority: object,
    ) -> object:
        return _AuthorityBoundJsonResponse(
            response,
            transport=transport,
            transport_authority=transport_authority,
        )

    def verify(
        attested: object,
        *,
        transport: object,
        transport_authority: object,
    ) -> Any:
        if (
            type(attested) is not _AuthorityBoundJsonResponse
            or attested._issuer_authority is not issuer_authority
            or attested._transport is not transport
            or attested._transport_authority is not transport_authority
        ):
            raise ProviderError("manifest_worker_mismatch")
        return attested._response

    return issue, verify


(
    _issue_response_attestation,
    _verify_response_attestation,
) = _response_attestation_protocol()


def _accept_injected_test_response(
    response: Any,
    *,
    transport: object,
    transport_authority: object,
) -> Any:
    del transport, transport_authority
    return response


def _parse_loopback_endpoint(endpoint: str) -> tuple[str, int, str]:
    parse_failed = False
    try:
        parsed = urlparse(endpoint)
        port = parsed.port
    except (TypeError, ValueError):
        parse_failed = True
    if parse_failed:
        raise ProviderError("non_loopback_endpoint")
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or parsed.path != "/v1/chat/completions"
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderError("non_loopback_endpoint")
    return parsed.hostname, port, parsed.path


class StdlibAsyncJsonTransport:
    __slots__ = (
        "_credential_resolver",
        "_credential_resolver_authority",
    )

    def __init__(
        self,
        *,
        credential_resolver: Callable[[object], object] | None = None,
    ) -> None:
        if (
            credential_resolver is None
            or not inspect.ismethod(credential_resolver)
            or credential_resolver.__self__ is None
            or inspect.iscoroutinefunction(credential_resolver)
        ):
            raise ProviderError("transport_credential_resolver_required")
        try:
            parameters = tuple(
                inspect.signature(credential_resolver).parameters.values()
            )
        except (TypeError, ValueError):
            raise ProviderError(
                "transport_credential_resolver_required"
            ) from None
        if (
            len(parameters) != 1
            or parameters[0].name != "transport_authority"
            or parameters[0].kind
            not in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }
        ):
            raise ProviderError("transport_credential_resolver_required")
        object.__setattr__(self, "_credential_resolver", credential_resolver)
        object.__setattr__(
            self,
            "_credential_resolver_authority",
            (credential_resolver,),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("StdlibAsyncJsonTransport is read-only")

    def __repr__(self) -> str:
        return "StdlibAsyncJsonTransport()"

    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
        transport_authority: object,
    ) -> Any:
        bearer_token, model_alias = self._resolve_credentials(
            transport_authority
        )
        host, port, target = _parse_loopback_endpoint(url)
        authorized_payload = dict(payload)
        authorized_payload["model"] = model_alias
        body = json.dumps(
            authorized_payload,
            separators=(",", ":"),
        ).encode("utf-8")
        host_header = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        authorization = f"Authorization: Bearer {bearer_token}".encode("ascii")
        request = b"\r\n".join(
            (
                f"POST {target} HTTP/1.1".encode("ascii"),
                f"Host: {host_header}".encode("ascii"),
                authorization,
                b"Content-Type: application/json",
                f"Content-Length: {len(body)}".encode("ascii"),
                b"Connection: close",
                b"",
                body,
            )
        )
        writer = None
        try:
            async with asyncio.timeout(timeout_seconds):
                reader, writer = await asyncio.open_connection(
                    host,
                    port,
                    limit=_HTTP_STREAM_LIMIT,
                )
                writer.write(request)
                await writer.drain()
                response = await self._read_response(
                    reader,
                    max_response_bytes,
                    expected_model_alias=model_alias,
                )
                if (
                    not isinstance(response, dict)
                    or not isinstance(response.get("model"), str)
                    or response["model"] != model_alias
                ):
                    raise ProviderError("manifest_worker_mismatch")
        except asyncio.CancelledError:
            await self._abort_cancelled_call(writer)
            raise
        except BaseException:
            await self._abort_failed_call(writer)
            raise
        else:
            await self._close_normally(writer)
            return _issue_response_attestation(
                response,
                transport=self,
                transport_authority=transport_authority,
            )

    def _resolve_credentials(
        self,
        transport_authority: object,
    ) -> tuple[str, str]:
        try:
            resolver = self._credential_resolver
            binding = self._credential_resolver_authority
        except AttributeError:
            raise ProviderError("stale_transport_authority")
        if (
            type(binding) is not tuple
            or len(binding) != 1
            or binding[0] is not resolver
            or not inspect.ismethod(resolver)
            or resolver.__self__ is None
        ):
            raise ProviderError("stale_transport_authority")
        try:
            credentials = resolver(transport_authority)
        except WorkerError as error:
            raise ProviderError(error.code) from None
        except BaseException:
            raise ProviderError("stale_transport_authority") from None
        try:
            bearer_token = credentials.bearer_token
            model_alias = credentials.model_alias
        except BaseException:
            raise ProviderError("invalid_transport_credentials") from None
        if (
            not isinstance(bearer_token, str)
            or _BEARER_TOKEN.fullmatch(bearer_token) is None
            or not isinstance(model_alias, str)
            or _MODEL_ALIAS.fullmatch(model_alias) is None
        ):
            raise ProviderError("invalid_transport_credentials")
        return bearer_token, model_alias

    async def _read_response(
        self,
        reader: asyncio.StreamReader,
        max_response_bytes: int,
        *,
        expected_model_alias: str,
    ) -> Any:
        status_line = await self._readline(reader, _MAX_STATUS_LINE_BYTES)
        parts = status_line[:-2].split(b" ", 2)
        if (
            len(parts) < 2
            or parts[0] not in {b"HTTP/1.0", b"HTTP/1.1"}
            or len(parts[1]) != 3
            or not parts[1].isdigit()
        ):
            raise ProviderError("invalid_provider_response")
        status = int(parts[1])
        headers = await self._read_headers(reader, len(status_line))
        if 300 <= status <= 399:
            raise ProviderError("provider_redirect_rejected")
        if status in {401, 403}:
            raise ProviderError("managed_worker_required")
        if not 200 <= status <= 299:
            raise ProviderError("transport_error")
        if headers.get(b"content-encoding", b"identity").lower() != b"identity":
            raise ProviderError("invalid_provider_response")

        transfer_encoding = headers.get(b"transfer-encoding")
        content_length = headers.get(b"content-length")
        if transfer_encoding is not None and content_length is not None:
            raise ProviderError("invalid_provider_response")
        if transfer_encoding is not None:
            if transfer_encoding.lower() != b"chunked":
                raise ProviderError("invalid_provider_response")
            raw = await self._read_chunked(reader, max_response_bytes)
        elif content_length is not None:
            if not content_length.isdigit():
                raise ProviderError("invalid_provider_response")
            size = int(content_length)
            if size > max_response_bytes:
                raise ProviderError("provider_response_too_large")
            raw = await self._readexactly(reader, size)
        else:
            raw = await self._read_to_eof(reader, max_response_bytes)

        try:
            decoded = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_preserve_json_object,
            )
            _require_root_model_identity(
                decoded,
                expected_model_alias=expected_model_alias,
            )
            return _materialize_strict_json(decoded)
        except _RootModelIdentityMismatch:
            raise ProviderError("manifest_worker_mismatch") from None
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            _DuplicateJsonKey,
            RecursionError,
        ):
            raise ProviderError("invalid_provider_response") from None

    async def _read_headers(
        self,
        reader: asyncio.StreamReader,
        consumed: int,
    ) -> dict[bytes, bytes]:
        headers: dict[bytes, bytes] = {}
        while True:
            remaining = _MAX_HEADER_BYTES - consumed
            if remaining <= 0:
                raise ProviderError("invalid_provider_response")
            line = await self._readline(reader, remaining)
            consumed += len(line)
            if line == b"\r\n":
                return headers
            if line[:1] in {b" ", b"\t"} or b":" not in line:
                raise ProviderError("invalid_provider_response")
            name, value = line[:-2].split(b":", 1)
            name = name.strip().lower()
            value = value.strip()
            if not _HEADER_NAME.fullmatch(name) or name in headers:
                raise ProviderError("invalid_provider_response")
            if any(byte < 32 and byte != 9 for byte in value) or 127 in value:
                raise ProviderError("invalid_provider_response")
            headers[name] = value

    async def _read_chunked(
        self,
        reader: asyncio.StreamReader,
        max_response_bytes: int,
    ) -> bytes:
        chunks = bytearray()
        while True:
            line = await self._readline(reader, _MAX_CHUNK_LINE_BYTES)
            size_text = line[:-2]
            if not size_text or b";" in size_text:
                raise ProviderError("invalid_provider_response")
            try:
                size = int(size_text, 16)
            except ValueError:
                raise ProviderError("invalid_provider_response") from None
            if size == 0:
                await self._read_headers(reader, 0)
                return bytes(chunks)
            if size > max_response_bytes - len(chunks):
                raise ProviderError("provider_response_too_large")
            chunks.extend(await self._readexactly(reader, size))
            if await self._readexactly(reader, 2) != b"\r\n":
                raise ProviderError("invalid_provider_response")

    async def _read_to_eof(
        self,
        reader: asyncio.StreamReader,
        max_response_bytes: int,
    ) -> bytes:
        chunks = bytearray()
        while True:
            chunk = await reader.read(min(65_536, max_response_bytes + 1 - len(chunks)))
            if not chunk:
                return bytes(chunks)
            chunks.extend(chunk)
            if len(chunks) > max_response_bytes:
                raise ProviderError("provider_response_too_large")

    async def _readline(
        self,
        reader: asyncio.StreamReader,
        maximum: int,
    ) -> bytes:
        try:
            line = await reader.readline()
        except (ValueError, asyncio.LimitOverrunError):
            raise ProviderError("invalid_provider_response") from None
        if not line or len(line) > maximum or not line.endswith(b"\r\n"):
            raise ProviderError("invalid_provider_response")
        return line

    async def _readexactly(
        self,
        reader: asyncio.StreamReader,
        size: int,
    ) -> bytes:
        try:
            return await reader.readexactly(size)
        except asyncio.IncompleteReadError:
            raise ProviderError("invalid_provider_response") from None

    async def _abort_cancelled_call(
        self,
        writer: asyncio.StreamWriter | None,
    ) -> None:
        if writer is None:
            return
        writer.transport.abort()

        async def wait_for_close() -> None:
            try:
                await writer.wait_closed()
            except OSError:
                pass

        cleanup = asyncio.create_task(wait_for_close())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()

    async def _abort_failed_call(
        self,
        writer: asyncio.StreamWriter | None,
    ) -> None:
        if writer is None:
            return
        writer.transport.abort()

        async def wait_for_close() -> bool:
            try:
                async with asyncio.timeout(_CLOSE_TIMEOUT_SECONDS):
                    await writer.wait_closed()
                return True
            except (Exception, asyncio.CancelledError):
                return False

        cleanup = asyncio.create_task(wait_for_close())
        cancellation_requested = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancellation_requested = True
        close_confirmed = cleanup.result()
        if cancellation_requested:
            if not close_confirmed:
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
            raise asyncio.CancelledError

    async def _close_normally(self, writer: asyncio.StreamWriter | None) -> None:
        if writer is None:
            return
        writer.close()
        try:
            async with asyncio.timeout(_CLOSE_TIMEOUT_SECONDS):
                await writer.wait_closed()
        except (OSError, TimeoutError):
            pass


_STDLIB_POST_JSON = StdlibAsyncJsonTransport.post_json
_PRODUCTION_TRANSPORT_CONSTRUCTION = object()
_INJECTED_TEST_TRANSPORT_CONSTRUCTION = object()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LlamaCppProvider:
    __slots__ = (
        "_clock",
        "_endpoint",
        "_manifest",
        "_monotonic",
        "_timeout_seconds",
        "_transport",
        "_transport_authority",
        "_worker",
        "_worker_authority",
    )

    def __init__(
        self,
        manifest: PinnedSlmManifest,
        transport: AsyncJsonTransport | None = None,
        endpoint: str = "http://127.0.0.1:8081/v1/chat/completions",
        *,
        timeout_seconds: float = 8.0,
        clock: Callable[[], str] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        worker: ManagedLlamaWorker | None = None,
    ):
        self._initialize(
            manifest,
            transport,
            endpoint,
            timeout_seconds=timeout_seconds,
            clock=clock,
            monotonic=monotonic,
            worker=worker,
            transport_construction_authority=(
                _PRODUCTION_TRANSPORT_CONSTRUCTION
            ),
        )

    @classmethod
    def _for_tests(
        cls,
        manifest: PinnedSlmManifest,
        transport: AsyncJsonTransport,
        endpoint: str = "http://127.0.0.1:8081/v1/chat/completions",
        *,
        timeout_seconds: float = 8.0,
        clock: Callable[[], str] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        worker: ManagedLlamaWorker | None = None,
    ) -> "LlamaCppProvider":
        """Construct with an injected transport for isolated unit tests only.

        Production construction deliberately has no flag or capability that
        admits a custom transport.  Same-process test code must opt into this
        unmistakably private seam.
        """

        instance = cls.__new__(cls)
        instance._initialize(
            manifest,
            transport,
            endpoint,
            timeout_seconds=timeout_seconds,
            clock=clock,
            monotonic=monotonic,
            worker=worker,
            transport_construction_authority=(
                _INJECTED_TEST_TRANSPORT_CONSTRUCTION
            ),
        )
        return instance

    def _initialize(
        self,
        manifest: PinnedSlmManifest,
        transport: AsyncJsonTransport | None,
        endpoint: str,
        *,
        timeout_seconds: float,
        clock: Callable[[], str],
        monotonic: Callable[[], float],
        worker: ManagedLlamaWorker | None,
        transport_construction_authority: object,
    ) -> None:
        if manifest.max_tokens != INFERENCE_PARAMETERS["max_tokens"]:
            raise ProviderError("manifest_prompt_mismatch")
        if type(worker) is not ManagedLlamaWorker:
            raise ProviderError("managed_worker_required")
        if (
            transport_construction_authority
            is _INJECTED_TEST_TRANSPORT_CONSTRUCTION
        ):
            transport_post = self._validate_transport_shape(transport)
            response_attestor = _accept_injected_test_response
        elif (
            transport_construction_authority
            is _PRODUCTION_TRANSPORT_CONSTRUCTION
        ):
            transport_post = self._validate_transport(transport)
            response_attestor = _verify_response_attestation
        else:
            raise ProviderError("managed_worker_required")
        validated_endpoint = self._validate_endpoint(endpoint)
        if not 0.1 <= timeout_seconds <= 30.0:
            raise ProviderError("invalid_timeout")
        object.__setattr__(self, "_manifest", manifest)
        object.__setattr__(self, "_transport", transport)
        object.__setattr__(
            self,
            "_transport_authority",
            (
                transport,
                transport_post,
                response_attestor,
                transport_construction_authority,
            ),
        )
        object.__setattr__(self, "_worker", worker)
        object.__setattr__(self, "_worker_authority", (worker,))
        object.__setattr__(self, "_endpoint", validated_endpoint)
        object.__setattr__(self, "_timeout_seconds", float(timeout_seconds))
        object.__setattr__(self, "_clock", clock)
        object.__setattr__(self, "_monotonic", monotonic)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("LlamaCppProvider configuration is read-only")

    @property
    def manifest(self) -> PinnedSlmManifest:
        return self._manifest

    @property
    def transport(self) -> AsyncJsonTransport:
        return self._transport

    @property
    def worker(self) -> ManagedLlamaWorker:
        return self._worker

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def clock(self) -> Callable[[], str]:
        return self._clock

    @property
    def monotonic(self) -> Callable[[], float]:
        return self._monotonic

    def _require_managed_worker(self) -> ManagedLlamaWorker:
        try:
            worker = self._worker
            authority = self._worker_authority
        except AttributeError:
            raise ProviderError("managed_worker_required") from None
        if (
            type(worker) is not ManagedLlamaWorker
            or type(authority) is not tuple
            or len(authority) != 1
            or authority[0] is not worker
        ):
            raise ProviderError("managed_worker_required")
        return worker

    @staticmethod
    def _validate_transport(
        transport: AsyncJsonTransport | None,
    ) -> Callable[..., Awaitable[Any]]:
        if type(transport) is not StdlibAsyncJsonTransport:
            raise ProviderError("managed_worker_required")
        post_json = LlamaCppProvider._validate_transport_shape(transport)
        if (
            not inspect.ismethod(post_json)
            or post_json.__self__ is not transport
            or post_json.__func__ is not _STDLIB_POST_JSON
        ):
            raise ProviderError("managed_worker_required")
        return post_json

    @staticmethod
    def _validate_transport_shape(
        transport: AsyncJsonTransport | None,
    ) -> Callable[..., Awaitable[Any]]:
        if transport is None:
            raise ProviderError("managed_worker_required")
        post_json = getattr(transport, "post_json", None)
        if not inspect.iscoroutinefunction(post_json):
            raise ProviderError("managed_worker_required")
        try:
            parameters = inspect.signature(post_json).parameters.values()
        except (TypeError, ValueError):
            raise ProviderError("managed_worker_required") from None
        authority = tuple(
            parameter
            for parameter in parameters
            if parameter.name == "transport_authority"
        )
        if (
            len(authority) != 1
            or authority[0].kind is not inspect.Parameter.KEYWORD_ONLY
            or authority[0].default is not inspect.Parameter.empty
        ):
            raise ProviderError("managed_worker_required")
        return post_json

    def _require_managed_transport(
        self,
    ) -> tuple[
        Callable[..., Awaitable[Any]],
        Callable[..., Any],
    ]:
        try:
            transport = self._transport
            authority = self._transport_authority
        except AttributeError:
            raise ProviderError("managed_worker_required") from None
        if (
            type(authority) is not tuple
            or len(authority) != 4
            or authority[0] is not transport
            or not inspect.iscoroutinefunction(authority[1])
        ):
            raise ProviderError("managed_worker_required")
        post_json = authority[1]
        response_attestor = authority[2]
        construction_authority = authority[3]
        if construction_authority is _PRODUCTION_TRANSPORT_CONSTRUCTION:
            if (
                type(transport) is not StdlibAsyncJsonTransport
                or not inspect.ismethod(post_json)
                or post_json.__self__ is not transport
                or post_json.__func__ is not _STDLIB_POST_JSON
                or response_attestor is not _verify_response_attestation
            ):
                raise ProviderError("managed_worker_required")
        elif construction_authority is _INJECTED_TEST_TRANSPORT_CONSTRUCTION:
            if response_attestor is not _accept_injected_test_response:
                raise ProviderError("managed_worker_required")
        else:
            raise ProviderError("managed_worker_required")
        return post_json, response_attestor

    def _unwrap_transport_response(
        self,
        response: Any,
        *,
        transport_authority: object,
    ) -> Any:
        _, response_attestor = self._require_managed_transport()
        return response_attestor(
            response,
            transport=self._transport,
            transport_authority=transport_authority,
        )

    @staticmethod
    def _validate_endpoint(endpoint: str) -> str:
        _parse_loopback_endpoint(endpoint)
        return endpoint

    async def begin_preparation(
        self,
        *,
        deadline: float,
    ) -> WorkerEpochReceipt:
        """Explicitly start a stopped worker at a preparation-window boundary."""

        worker = self._require_managed_worker()
        self._require_managed_transport()
        try:
            return await worker.begin_preparation(deadline=deadline)
        except WorkerError as error:
            raise ProviderError(_provider_boundary_code(error.code)) from None

    async def generate(self, request: SlmRequest) -> RawSlmGeneration:
        started_at = self.monotonic()
        return await self.generate_before(
            request,
            ready_deadline=started_at + self.timeout_seconds,
            cancellation_deadline=started_at + self.timeout_seconds + 2.0,
        )

    async def generate_before(
        self,
        request: SlmRequest,
        *,
        ready_deadline: float,
        cancellation_deadline: float,
    ) -> RawSlmGeneration:
        if not validate_prompt_receipt(request):
            raise ProviderError("prompt_receipt_mismatch")
        self._require_managed_worker()
        self._require_managed_transport()
        receipt_payload = prompt_payload(request)
        payload = {
            "model": self.manifest.model_id,
            "messages": receipt_payload["messages"],
            **receipt_payload["inference"],
        }
        gate_lease = await _SINGLE_WORKER.acquire()
        response: Any = None
        failure_code: str | None = None
        cancellation_requested = False
        try:
            worker = self._require_managed_worker()
            worker_error_code = None
            try:
                worker_lease = await worker.acquire(
                    request.prompt_sha256,
                    ready_deadline=ready_deadline,
                )
            except WorkerError as error:
                worker_error_code = _provider_boundary_code(error.code)
            if worker_error_code is not None:
                raise ProviderError(worker_error_code)

            worker = self._require_managed_worker()
            endpoint = self._validate_endpoint(worker_lease.endpoint)
            epoch = worker.epoch
            if epoch is None or epoch.model_sha256 != self.manifest.model_sha256:
                await self._abort_worker(
                    worker_lease,
                    reason="manifest_mismatch",
                    deadline=cancellation_deadline,
                )
                raise ProviderError("manifest_worker_mismatch")

            try:
                self._require_managed_worker()
                transport_post, _response_attestor = (
                    self._require_managed_transport()
                )
                transport_authority = worker.transport_authority_for(
                    worker_lease
                )
                response = await transport_post(
                    endpoint,
                    payload,
                    timeout_seconds=self.timeout_seconds,
                    max_response_bytes=self.manifest.max_response_bytes,
                    transport_authority=transport_authority,
                )
                response = self._unwrap_transport_response(
                    response,
                    transport_authority=transport_authority,
                )
            except asyncio.CancelledError:
                cancellation_requested = True
                try:
                    await self._abort_worker(
                        worker_lease,
                        reason="live_deadline",
                        deadline=cancellation_deadline,
                    )
                except asyncio.CancelledError:
                    cancellation_requested = True
                except ProviderError as error:
                    cancellation_requested = False
                    failure_code = _provider_boundary_code(error.code)
            except WorkerError as error:
                failure_code = _provider_boundary_code(error.code)
                try:
                    await self._abort_worker(
                        worker_lease,
                        reason="transport_authority_error",
                        deadline=cancellation_deadline,
                    )
                except asyncio.CancelledError:
                    cancellation_requested = True
                    failure_code = None
                except ProviderError as cleanup_error:
                    failure_code = _provider_boundary_code(cleanup_error.code)
            except ProviderError as error:
                failure_code = _provider_boundary_code(error.code)
                try:
                    await self._abort_worker(
                        worker_lease,
                        reason="transport_error",
                        deadline=cancellation_deadline,
                    )
                except asyncio.CancelledError:
                    cancellation_requested = True
                    failure_code = None
                except ProviderError as cleanup_error:
                    failure_code = _provider_boundary_code(cleanup_error.code)
            except Exception:
                failure_code = "transport_error"
                try:
                    await self._abort_worker(
                        worker_lease,
                        reason="transport_error",
                        deadline=cancellation_deadline,
                    )
                except asyncio.CancelledError:
                    cancellation_requested = True
                    failure_code = None
                except ProviderError as cleanup_error:
                    failure_code = _provider_boundary_code(cleanup_error.code)

            if failure_code is None and not cancellation_requested:
                try:
                    worker = self._require_managed_worker()
                    await worker.confirm_complete(worker_lease)
                except WorkerError as error:
                    failure_code = _provider_boundary_code(error.code)
        finally:
            gate_lease.release()

        if failure_code is not None:
            raise ProviderError(failure_code)
        if cancellation_requested:
            raise asyncio.CancelledError

        content = self._extract_content(response)
        try:
            content_size = len(content.encode("utf-8"))
        except UnicodeEncodeError:
            raise ProviderError("invalid_provider_response") from None
        if content_size > self.manifest.max_response_bytes:
            raise ProviderError("provider_response_too_large")
        return RawSlmGeneration(
            text=content,
            model_sha256=self.manifest.model_sha256,
            adapter_identity_receipt_sha256=(
                self.manifest.adapter_identity_receipt_sha256
            ),
            gguf_sha256=self.manifest.gguf_sha256,
            generator_identity_receipt_sha256=(
                self.manifest.generator_identity_receipt_sha256
            ),
            prompt_sha256=request.prompt_sha256,
            prompt_template_sha256=self.manifest.prompt_template_sha256,
            registry_id=self.manifest.registry_id,
            generated_at_utc=self.clock(),
        )

    async def _abort_worker(
        self,
        lease: GenerationLease,
        *,
        reason: str,
        deadline: float,
    ) -> None:
        """Finish stop/reap despite repeated cancellation of the provider call."""

        worker = self._require_managed_worker()
        abort_task = asyncio.create_task(
            worker.abort(lease, reason=reason, deadline=deadline)
        )
        cancellation_requested = False
        while not abort_task.done():
            try:
                await asyncio.shield(abort_task)
            except asyncio.CancelledError:
                cancellation_requested = True
            except BaseException:
                # Normalize the worker result below without leaking driver
                # or transport details through this provider boundary.
                pass
        try:
            abort_task.result()
        except WorkerError as error:
            raise ProviderError(_provider_boundary_code(error.code)) from None
        except asyncio.CancelledError:
            cancellation_requested = True
        except BaseException:
            raise ProviderError("worker_quarantined") from None
        if cancellation_requested:
            raise asyncio.CancelledError

    @staticmethod
    def _extract_content(response: Any) -> str:
        if not isinstance(response, dict) or set(response).isdisjoint({"choices"}):
            raise ProviderError("invalid_provider_response")
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ProviderError("invalid_provider_response")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError("invalid_provider_response")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError("invalid_provider_response")
        content = message.get("content")
        if not isinstance(content, str):
            raise ProviderError("invalid_provider_response")
        return content
