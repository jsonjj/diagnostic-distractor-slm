"""Audited exact-child creation adapter for the production llama worker."""

from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any, Callable, Sequence


_ADAPTER_IDENTITY = {
    "callbackContract": "wayline_child_created.v1",
    "cleanup": "terminate-kill-wait.v1",
    "implementation": "python.subprocess.Popen",
    "schemaVersion": "wayline.production-spawn-adapter.v1",
}
PRODUCTION_SPAWN_ADAPTER_SHA256 = hashlib.sha256(
    json.dumps(
        _ADAPTER_IDENTITY,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()
_CLEANUP_TIMEOUT_SECONDS = 0.25


class ProductionSpawnError(RuntimeError):
    """The adapter could not prove cleanup of a created child."""


class ProductionPopenFactory:
    """Publish the exact Popen child or synchronously prove it was reaped."""

    __slots__ = ()

    wayline_child_created_callback = True
    wayline_spawn_adapter_sha256 = PRODUCTION_SPAWN_ADAPTER_SHA256

    def __call__(
        self,
        argv: Sequence[str],
        *,
        wayline_child_created: Callable[[object], None],
        **kwargs: Any,
    ) -> object:
        if not callable(wayline_child_created):
            raise ValueError("wayline_child_created callback is required")
        child = subprocess.Popen(argv, **kwargs)
        try:
            wayline_child_created(child)
        except BaseException as publication_error:
            try:
                self._reap_unpublished_child(child)
            except BaseException:
                raise ProductionSpawnError("created_child_cleanup_failed") from None
            raise publication_error
        return child

    @staticmethod
    def _reap_unpublished_child(child: object) -> None:
        terminate = getattr(child, "terminate", None)
        kill = getattr(child, "kill", None)
        wait = getattr(child, "wait", None)
        if not callable(terminate) or not callable(kill) or not callable(wait):
            raise ProductionSpawnError("created_child_cleanup_failed")
        try:
            terminate()
        except BaseException:
            try:
                kill()
            except BaseException:
                raise ProductionSpawnError("created_child_cleanup_failed") from None
        try:
            wait(timeout=_CLEANUP_TIMEOUT_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        except BaseException:
            pass
        try:
            kill()
            wait(timeout=_CLEANUP_TIMEOUT_SECONDS)
        except BaseException:
            raise ProductionSpawnError("created_child_cleanup_failed") from None


__all__ = [
    "PRODUCTION_SPAWN_ADAPTER_SHA256",
    "ProductionPopenFactory",
    "ProductionSpawnError",
]
