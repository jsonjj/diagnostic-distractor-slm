"""PyInstaller entrypoint that injects the validated production factory."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import sys

from services.wayline_forge.app.launcher import main as launch_main
from services.wayline_forge.app.production_runtime import (
    build_production_runtime,
)
from services.wayline_forge.app.settings import Settings


async def packaged_runtime_factory(settings: Settings):
    """Compose from the frozen executable directory and writable state root."""

    return await build_production_runtime(
        settings,
        package_root=Path(sys.executable).parent,
    )


def main(argv: Sequence[str] | None = None) -> int:
    return launch_main(argv, runtime_factory=packaged_runtime_factory)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "packaged_runtime_factory"]
