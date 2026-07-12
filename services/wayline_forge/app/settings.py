"""Packaged runtime configuration for Wayline Forge."""

from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Immutable paths and provider settings for one packaged runtime."""

    runtime_root: Path
    host: str
    port: int
    model_manifest: Path
    reviewed_cache_release_root: Path
    profile_db: Path
    truefoundry_base_url: str | None
    truefoundry_model: str | None
    truefoundry_api_key: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if _absolute_normalized_root(self.runtime_root) != self.runtime_root:
            raise ValueError("runtime_root must be absolute and normalized")
        if not 0 <= self.port <= 65_535:
            raise ValueError("port must be between 0 and 65535")

    @classmethod
    def for_tests(cls, runtime_root: Path) -> "Settings":
        """Build secret-free, ephemeral settings rooted at ``runtime_root``."""

        return cls._for_root(_absolute_normalized_root(runtime_root), port=0)

    @classmethod
    def from_environment(cls) -> "Settings":
        """Build settings from the packaged runtime environment."""

        runtime_root = _absolute_normalized_root(
            os.environ["WAYLINE_RUNTIME_ROOT"]
        )
        return cls._for_root(
            runtime_root,
            port=int(os.getenv("WAYLINE_PORT", "0")),
            truefoundry_base_url=os.getenv("TFY_BASE_URL"),
            truefoundry_model=os.getenv("TFY_MODEL"),
            truefoundry_api_key=os.getenv("TFY_API_KEY"),
        )

    @classmethod
    def _for_root(
        cls,
        runtime_root: Path,
        *,
        port: int,
        truefoundry_base_url: str | None = None,
        truefoundry_model: str | None = None,
        truefoundry_api_key: str | None = None,
    ) -> "Settings":
        return cls(
            runtime_root=runtime_root,
            host="127.0.0.1",
            port=port,
            model_manifest=runtime_root / "resources/model_manifest_v1.json",
            reviewed_cache_release_root=(
                runtime_root / "resources/reviewed_cache_release_v1"
            ),
            profile_db=runtime_root / "profiles/wayline_profiles_v1.sqlite",
            truefoundry_base_url=truefoundry_base_url,
            truefoundry_model=truefoundry_model,
            truefoundry_api_key=truefoundry_api_key,
        )


def _absolute_normalized_root(value: str | Path) -> Path:
    """Validate a path lexically without following any filesystem link."""

    raw = os.fspath(value)
    if (
        not isinstance(raw, str)
        or not raw
        or "\x00" in raw
        or not os.path.isabs(raw)
        or raw.startswith("//")
        or os.path.normpath(raw) != raw
    ):
        raise ValueError("runtime_root must be absolute and normalized")
    return Path(raw)
