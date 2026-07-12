"""Fail-closed lifecycle launcher for the packaged Wayline Forge sidecar.

The live runtime factory is intentionally injected.  Production construction
must remain unavailable until the pinned GGUF, llama-server, reviewed cache,
and descriptor-binding release receipt all exist.  This launcher still owns
the transport socket, readiness handoff, and deterministic resource cleanup.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
import inspect
import json
import os
from pathlib import Path
import re
import socket
import sys
from typing import Any, Protocol

import uvicorn

from services.wayline_forge.app.api import (
    ProfileResolver,
    WaylineApiFacade,
    create_api,
)
from services.wayline_forge.app.loopback_security import (
    LEARNER_LOOPBACK_HOST,
    LaunchSecurityPolicy,
)
from services.wayline_forge.app.settings import Settings


_TOKEN = re.compile(r"[0-9a-f]{64}", re.ASCII)
_STARTUP_SCHEMA = "wayline.startup.v1"
_STARTUP_TIMEOUT_SECONDS = 10.0
_SHUTDOWN_TIMEOUT_SECONDS = 5.0
_EX_CONFIG = 78


class LaunchError(RuntimeError):
    """Stable, secret-free sidecar startup or cleanup failure."""

    _CODES = frozenset(
        {
            "api_runtime_failed",
            "api_start_failed",
            "api_start_timeout",
            "live_runtime_unavailable",
            "runtime_cleanup_failed",
            "runtime_composition_failed",
            "startup_handoff_failed",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown launch error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class StartupReceipt:
    """The one secret-bearing record written to an inherited descriptor."""

    host: str
    port: int
    launch_token: str
    schema_version: str = _STARTUP_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != _STARTUP_SCHEMA:
            raise ValueError("startup schema is invalid")
        if self.host != LEARNER_LOOPBACK_HOST:
            raise ValueError("startup host must be IPv4 loopback")
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65_535
        ):
            raise ValueError("startup port is invalid")
        if (
            not isinstance(self.launch_token, str)
            or _TOKEN.fullmatch(self.launch_token) is None
        ):
            raise ValueError("startup token is invalid")

    def __repr__(self) -> str:
        return (
            "StartupReceipt("
            f"host={self.host!r}, port={self.port!r}, launch_token=<redacted>)"
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "host": self.host,
                "launchToken": self.launch_token,
                "port": self.port,
                "schemaVersion": self.schema_version,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


Cleanup = Callable[[], object | Awaitable[object]]
_CONTROL_FLOW_EXCEPTIONS = (
    KeyboardInterrupt,
    SystemExit,
    GeneratorExit,
    asyncio.CancelledError,
)


async def _shield_cleanup(awaitable: Awaitable[object]) -> None:
    """Finish one cleanup despite caller cancellation, then preserve control."""

    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if task.done():
                if not task.cancelled() and cancellation is None:
                    cancellation = error
                break
            if cancellation is None:
                cancellation = error
        except BaseException:
            break
    task_error: BaseException | None = None
    try:
        task.result()
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


class RuntimeBundle:
    """Injected facade, identity resolver, and close-once owned resources.

    Cleanup callbacks are supplied in acquisition order and invoked in reverse.
    A production factory must include profile store, quiz store, reviewed-cache
    release, and managed worker callbacks in acquisition order.
    """

    __slots__ = ("facade", "resolve_profile_id", "_cleanup", "_closed")

    def __init__(
        self,
        *,
        facade: WaylineApiFacade,
        resolve_profile_id: ProfileResolver,
        cleanup: Sequence[Cleanup],
    ) -> None:
        if not callable(resolve_profile_id):
            raise TypeError("resolve_profile_id must be callable")
        callbacks = tuple(cleanup)
        if any(not callable(callback) for callback in callbacks):
            raise TypeError("every cleanup entry must be callable")
        self.facade = facade
        self.resolve_profile_id = resolve_profile_id
        self._cleanup = callbacks
        self._closed = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        failed = False
        special_failure: BaseException | None = None
        for callback in reversed(self._cleanup):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await _shield_cleanup(result)
            except _CONTROL_FLOW_EXCEPTIONS as error:
                if special_failure is None:
                    special_failure = error
            except BaseException:
                failed = True
        if special_failure is not None:
            raise special_failure
        if failed:
            raise LaunchError("runtime_cleanup_failed")


class _Server(Protocol):
    started: bool
    should_exit: bool

    async def serve(self, *, sockets: list[socket.socket]) -> None: ...


ServerFactory = Callable[[uvicorn.Config], _Server]
ReadinessPublisher = Callable[[StartupReceipt], object | Awaitable[object]]
RuntimeFactory = Callable[[Settings], RuntimeBundle | Awaitable[RuntimeBundle]]


def startup_failure_message(code: str) -> str:
    """Render only a stable machine-readable startup failure."""

    if code not in LaunchError._CODES:
        code = "runtime_composition_failed"
    return f"wayline_forge_startup_failed: {code}"


def publish_startup_receipt(descriptor: int, receipt: StartupReceipt) -> None:
    """Write one canonical receipt and close the inherited descriptor."""

    if (
        isinstance(descriptor, bool)
        or not isinstance(descriptor, int)
        or descriptor < 0
    ):
        raise LaunchError("startup_handoff_failed")
    if not isinstance(receipt, StartupReceipt):
        raise LaunchError("startup_handoff_failed")
    payload = receipt.to_json().encode("utf-8") + b"\n"
    view = memoryview(payload)
    offset = 0
    failed = False
    try:
        while offset < len(view):
            try:
                written = os.write(descriptor, view[offset:])
            except InterruptedError:
                continue
            if written <= 0:
                failed = True
                break
            offset += written
    except BaseException:
        failed = True
    finally:
        try:
            os.close(descriptor)
        except OSError:
            failed = True
    if failed or offset != len(view):
        raise LaunchError("startup_handoff_failed")


def open_listener(host: str, port: int) -> socket.socket:
    """Bind one non-inheritable IPv4 loopback listener."""

    if host != LEARNER_LOOPBACK_HOST:
        raise ValueError("listener host must be IPv4 loopback")
    if (
        isinstance(port, bool)
        or not isinstance(port, int)
        or not 0 <= port <= 65_535
    ):
        raise ValueError("listener port is invalid")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.set_inheritable(False)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise


async def _await_started(
    server: _Server,
    task: asyncio.Task[None],
    *,
    timeout_seconds: float,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while not server.started:
        if task.done():
            try:
                await task
            except BaseException:
                raise LaunchError("api_start_failed") from None
            raise LaunchError("api_start_failed")
        if loop.time() >= deadline:
            raise LaunchError("api_start_timeout")
        await asyncio.sleep(0.005)


async def _end_server(server: _Server, task: asyncio.Task[None]) -> None:
    if task.done():
        try:
            await task
        except BaseException:
            return
        return
    server.should_exit = True
    try:
        await asyncio.wait_for(
            asyncio.shield(task),
            timeout=_SHUTDOWN_TIMEOUT_SECONDS,
        )
    except BaseException:
        task.cancel()
        try:
            await task
        except BaseException:
            pass


async def serve_runtime(
    bundle: RuntimeBundle,
    *,
    settings: Settings,
    unity_origin: str,
    publish_ready: ReadinessPublisher,
    server_factory: ServerFactory = uvicorn.Server,
    random_bytes: Callable[[int], bytes] | None = None,
    startup_timeout_seconds: float = _STARTUP_TIMEOUT_SECONDS,
) -> None:
    """Serve one bundle, publish readiness, and close every owned resource."""

    if not isinstance(bundle, RuntimeBundle):
        raise TypeError("bundle must be a RuntimeBundle")
    if not isinstance(settings, Settings):
        raise TypeError("settings must be Settings")
    if not callable(publish_ready) or not callable(server_factory):
        raise TypeError("publisher and server factory must be callable")
    if (
        isinstance(startup_timeout_seconds, bool)
        or not isinstance(startup_timeout_seconds, (int, float))
        or not 0 < float(startup_timeout_seconds) <= 60.0
    ):
        raise ValueError("startup timeout is invalid")

    listener: socket.socket | None = None
    server: _Server | None = None
    server_task: asyncio.Task[None] | None = None
    primary_failure: BaseException | None = None
    try:
        security = LaunchSecurityPolicy.for_learner(
            unity_origin=unity_origin,
            random_bytes=random_bytes,
        )
        bind_validation = security.validate_bind_host(settings.host)
        if not bind_validation.accepted:
            raise LaunchError("runtime_composition_failed")
        listener = open_listener(settings.host, settings.port)
        bound_host, bound_port = listener.getsockname()
        api = create_api(
            bundle.facade,
            security=security,
            resolve_profile_id=bundle.resolve_profile_id,
        )
        config = uvicorn.Config(
            api,
            host=bound_host,
            port=bound_port,
            loop="asyncio",
            http="h11",
            ws="none",
            lifespan="off",
            access_log=False,
            log_config=None,
            log_level="critical",
            proxy_headers=False,
            server_header=False,
            date_header=False,
            timeout_keep_alive=5,
        )
        server = server_factory(config)
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        await _await_started(
            server,
            server_task,
            timeout_seconds=float(startup_timeout_seconds),
        )
        receipt = StartupReceipt(
            host=bound_host,
            port=bound_port,
            launch_token=security.launch_token,
        )
        try:
            publication = publish_ready(receipt)
            if inspect.isawaitable(publication):
                await publication
        except _CONTROL_FLOW_EXCEPTIONS:
            raise
        except BaseException:
            raise LaunchError("startup_handoff_failed") from None
        try:
            await server_task
        except _CONTROL_FLOW_EXCEPTIONS:
            raise
        except BaseException:
            raise LaunchError("api_runtime_failed") from None
    except BaseException as error:
        primary_failure = error
    finally:
        if server is not None and server_task is not None:
            try:
                await _shield_cleanup(_end_server(server, server_task))
            except BaseException as cleanup_error:
                if isinstance(cleanup_error, _CONTROL_FLOW_EXCEPTIONS):
                    primary_failure = cleanup_error
                elif not isinstance(primary_failure, _CONTROL_FLOW_EXCEPTIONS):
                    primary_failure = LaunchError("runtime_cleanup_failed")
        if listener is not None:
            try:
                listener.close()
            except BaseException as cleanup_error:
                if isinstance(cleanup_error, _CONTROL_FLOW_EXCEPTIONS):
                    primary_failure = cleanup_error
                elif not isinstance(primary_failure, _CONTROL_FLOW_EXCEPTIONS):
                    primary_failure = LaunchError("runtime_cleanup_failed")
        try:
            await _shield_cleanup(bundle.aclose())
        except BaseException as cleanup_error:
            if isinstance(cleanup_error, _CONTROL_FLOW_EXCEPTIONS):
                primary_failure = cleanup_error
            elif not isinstance(primary_failure, _CONTROL_FLOW_EXCEPTIONS):
                primary_failure = LaunchError("runtime_cleanup_failed")

    if primary_failure is None:
        return
    if isinstance(primary_failure, LaunchError):
        raise primary_failure
    if isinstance(
        primary_failure,
        (KeyboardInterrupt, SystemExit, GeneratorExit, asyncio.CancelledError),
    ):
        raise primary_failure
    raise LaunchError("runtime_composition_failed") from None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="WaylineForge")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--unity-origin", required=True)
    parser.add_argument("--startup-fd", required=True, type=int)
    parser.add_argument("--port", type=int, default=0)
    return parser


async def _run_cli(
    arguments: argparse.Namespace,
    runtime_factory: RuntimeFactory | None,
) -> None:
    if runtime_factory is None:
        raise LaunchError("live_runtime_unavailable")
    try:
        settings = replace(
            Settings.for_tests(Path(arguments.runtime_root)),
            port=arguments.port,
        )
        candidate = runtime_factory(settings)
        bundle = await candidate if inspect.isawaitable(candidate) else candidate
    except LaunchError:
        raise
    except BaseException:
        raise LaunchError("runtime_composition_failed") from None
    if not isinstance(bundle, RuntimeBundle):
        raise LaunchError("runtime_composition_failed")
    await serve_runtime(
        bundle,
        settings=settings,
        unity_origin=arguments.unity_origin,
        publish_ready=lambda receipt: publish_startup_receipt(
            arguments.startup_fd,
            receipt,
        ),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: RuntimeFactory | None = None,
) -> int:
    """Run the packaged sidecar or return a stable configuration failure."""

    arguments = _build_parser().parse_args(argv)
    try:
        asyncio.run(_run_cli(arguments, runtime_factory))
    except LaunchError as error:
        print(startup_failure_message(error.code), file=sys.stderr)
        return _EX_CONFIG
    except (KeyboardInterrupt, SystemExit):
        return 130
    except BaseException:
        print(
            startup_failure_message("runtime_composition_failed"),
            file=sys.stderr,
        )
        return _EX_CONFIG
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LaunchError",
    "RuntimeBundle",
    "RuntimeFactory",
    "StartupReceipt",
    "main",
    "open_listener",
    "publish_startup_receipt",
    "serve_runtime",
    "startup_failure_message",
]
