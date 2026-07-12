from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest

from services.wayline_forge.app.settings import Settings
from services.wayline_forge.tests.api_fixtures import (
    PROFILE_ID,
    RecordingFacade,
    SESSION_ID,
    UNITY_ORIGIN,
)


class _ImmediateServer:
    def __init__(self, _config: object) -> None:
        self.started = False
        self.should_exit = False
        self.socket_address: tuple[str, int] | None = None

    async def serve(self, *, sockets: list[socket.socket]) -> None:
        self.socket_address = sockets[0].getsockname()
        self.started = True
        await asyncio.sleep(0)


class _FailingServer:
    def __init__(self, _config: object) -> None:
        self.started = False
        self.should_exit = False

    async def serve(self, *, sockets: list[socket.socket]) -> None:
        del sockets
        raise RuntimeError("private failure detail")


class LauncherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    @staticmethod
    def _resolver(session_id: str) -> str:
        if session_id != SESSION_ID:
            raise LookupError("not current")
        return PROFILE_ID

    def test_startup_receipt_is_canonical_and_repr_redacts_token(self) -> None:
        from services.wayline_forge.app.launcher import StartupReceipt

        receipt = StartupReceipt(
            host="127.0.0.1",
            port=49152,
            launch_token="7" * 64,
        )

        payload = json.loads(receipt.to_json())
        self.assertEqual(payload["schemaVersion"], "wayline.startup.v1")
        self.assertEqual(payload["host"], "127.0.0.1")
        self.assertEqual(payload["port"], 49152)
        self.assertEqual(payload["launchToken"], "7" * 64)
        self.assertNotIn("7" * 64, repr(receipt))

    def test_startup_receipt_is_written_once_to_inherited_fd_and_fd_is_closed(
        self,
    ) -> None:
        from services.wayline_forge.app.launcher import (
            StartupReceipt,
            publish_startup_receipt,
        )

        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        receipt = StartupReceipt(
            host="127.0.0.1",
            port=49152,
            launch_token="8" * 64,
        )

        publish_startup_receipt(writer, receipt)
        received = os.read(reader, 4096)
        eof = os.read(reader, 1)

        self.assertEqual(received, receipt.to_json().encode("utf-8") + b"\n")
        self.assertEqual(eof, b"")
        with self.assertRaises(OSError):
            os.fstat(writer)

    def test_listener_binds_only_ipv4_loopback_and_reports_ephemeral_port(self) -> None:
        from services.wayline_forge.app.launcher import open_listener

        listener = open_listener("127.0.0.1", 0)
        self.addCleanup(listener.close)

        host, port = listener.getsockname()
        self.assertEqual(host, "127.0.0.1")
        self.assertGreater(port, 0)
        with self.assertRaisesRegex(ValueError, "loopback"):
            open_listener("0.0.0.0", 0)

    async def test_server_publishes_only_after_ready_and_closes_resources_in_reverse(
        self,
    ) -> None:
        from services.wayline_forge.app.launcher import (
            RuntimeBundle,
            serve_runtime,
        )

        closed: list[str] = []
        facade = RecordingFacade()
        bundle = RuntimeBundle(
            facade=facade,
            resolve_profile_id=self._resolver,
            cleanup=(
                lambda: closed.append("database"),
                lambda: closed.append("worker"),
            ),
        )
        settings = Settings.for_tests(self.root)
        servers: list[_ImmediateServer] = []
        published: list[object] = []

        def server_factory(config: object) -> _ImmediateServer:
            server = _ImmediateServer(config)
            servers.append(server)
            return server

        def publisher(receipt: object) -> None:
            self.assertTrue(servers[0].started)
            published.append(receipt)

        await serve_runtime(
            bundle,
            settings=settings,
            unity_origin=UNITY_ORIGIN,
            publish_ready=publisher,
            server_factory=server_factory,
            random_bytes=lambda size: b"\x09" * size,
        )

        self.assertEqual(len(published), 1)
        receipt = published[0]
        self.assertEqual(receipt.host, "127.0.0.1")
        self.assertEqual(receipt.port, servers[0].socket_address[1])
        self.assertEqual(receipt.launch_token, "09" * 32)
        self.assertEqual(closed, ["worker", "database"])

    async def test_startup_failure_is_stable_and_still_closes_every_resource(
        self,
    ) -> None:
        from services.wayline_forge.app.launcher import (
            LaunchError,
            RuntimeBundle,
            serve_runtime,
        )

        closed: list[str] = []
        bundle = RuntimeBundle(
            facade=RecordingFacade(),
            resolve_profile_id=self._resolver,
            cleanup=(
                lambda: closed.append("database"),
                lambda: closed.append("worker"),
            ),
        )
        published: list[object] = []

        with self.assertRaises(LaunchError) as caught:
            await serve_runtime(
                bundle,
                settings=Settings.for_tests(self.root),
                unity_origin=UNITY_ORIGIN,
                publish_ready=published.append,
                server_factory=_FailingServer,
                random_bytes=lambda size: b"\x0a" * size,
            )

        self.assertEqual(caught.exception.code, "api_start_failed")
        self.assertEqual(str(caught.exception), "api_start_failed")
        self.assertNotIn("private", repr(caught.exception))
        self.assertEqual(published, [])
        self.assertEqual(closed, ["worker", "database"])

    async def test_cleanup_attempts_all_resources_and_reports_one_redacted_failure(
        self,
    ) -> None:
        from services.wayline_forge.app.launcher import LaunchError, RuntimeBundle

        closed: list[str] = []

        def failing_cleanup() -> None:
            closed.append("failing")
            raise RuntimeError("secret cleanup text")

        bundle = RuntimeBundle(
            facade=RecordingFacade(),
            resolve_profile_id=self._resolver,
            cleanup=(
                lambda: closed.append("first"),
                failing_cleanup,
                lambda: closed.append("last"),
            ),
        )

        with self.assertRaises(LaunchError) as caught:
            await bundle.aclose()

        self.assertEqual(closed, ["last", "failing", "first"])
        self.assertEqual(caught.exception.code, "runtime_cleanup_failed")
        self.assertNotIn("secret", repr(caught.exception))

    async def test_cleanup_shields_to_completion_through_caller_cancellation(
        self,
    ) -> None:
        from services.wayline_forge.app.launcher import RuntimeBundle

        started = asyncio.Event()
        release = asyncio.Event()
        closed: list[str] = []

        async def slow_cleanup() -> None:
            started.set()
            await release.wait()
            closed.append("slow")

        bundle = RuntimeBundle(
            facade=RecordingFacade(),
            resolve_profile_id=self._resolver,
            cleanup=(
                lambda: closed.append("first"),
                slow_cleanup,
            ),
        )
        task = asyncio.create_task(bundle.aclose())
        await started.wait()
        task.cancel("owner cancellation")
        await asyncio.sleep(0)
        release.set()

        with self.assertRaises(asyncio.CancelledError) as caught:
            await task

        self.assertEqual(caught.exception.args, ("owner cancellation",))
        self.assertEqual(closed, ["slow", "first"])

    async def test_cleanup_preserves_exact_async_cancellation(
        self,
    ) -> None:
        from services.wayline_forge.app.launcher import RuntimeBundle

        cancellation = asyncio.CancelledError("cleanup cancellation")

        async def interrupted() -> None:
            raise cancellation

        bundle = RuntimeBundle(
            facade=RecordingFacade(),
            resolve_profile_id=self._resolver,
            cleanup=(interrupted,),
        )

        with self.assertRaises(asyncio.CancelledError) as caught:
            await bundle.aclose()

        self.assertIs(caught.exception, cancellation)

    async def test_cleanup_preserves_exact_sync_process_control(self) -> None:
        from services.wayline_forge.app.launcher import RuntimeBundle

        interruption = SystemExit(73)

        def interrupted() -> None:
            raise interruption

        bundle = RuntimeBundle(
            facade=RecordingFacade(),
            resolve_profile_id=self._resolver,
            cleanup=(interrupted,),
        )

        with self.assertRaises(SystemExit) as caught:
            await bundle.aclose()

        self.assertIs(caught.exception, interruption)

    async def test_serve_runtime_prefers_exact_cleanup_cancellation(
        self,
    ) -> None:
        from services.wayline_forge.app.launcher import (
            RuntimeBundle,
            serve_runtime,
        )

        cancellation = asyncio.CancelledError("cleanup cancellation")

        def interrupted() -> None:
            raise cancellation

        bundle = RuntimeBundle(
            facade=RecordingFacade(),
            resolve_profile_id=self._resolver,
            cleanup=(interrupted,),
        )

        with self.assertRaises(asyncio.CancelledError) as caught:
            await serve_runtime(
                bundle,
                settings=Settings.for_tests(self.root),
                unity_origin=UNITY_ORIGIN,
                publish_ready=lambda _receipt: None,
                server_factory=_FailingServer,
                random_bytes=lambda size: b"\x0b" * size,
            )

        self.assertIs(caught.exception, cancellation)

    def test_missing_live_runtime_factory_has_stable_secret_free_message(self) -> None:
        from services.wayline_forge.app.launcher import startup_failure_message

        message = startup_failure_message("live_runtime_unavailable")

        self.assertEqual(
            message,
            "wayline_forge_startup_failed: live_runtime_unavailable",
        )
        self.assertNotIn(str(self.root), message)


if __name__ == "__main__":
    unittest.main()
