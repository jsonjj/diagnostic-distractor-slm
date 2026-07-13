"""Small resumable JSONL cache for bounded paid API batches."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from typing import Callable, Sequence, TypeVar


T = TypeVar("T")


class CachedRunError(RuntimeError):
    """One or more calls failed after successful calls were persisted."""


def _load(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    cached = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = str(row["cache_key"])
            cached[key] = row["result"]
    return cached


def _write(path: Path, cached: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for key in sorted(cached):
            handle.write(
                json.dumps(
                    {"cache_key": key, "result": cached[key]},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    temporary.replace(path)


def run_cached(
    tasks: Sequence[T],
    *,
    key_fn: Callable[[T], str],
    worker: Callable[[T], object],
    cache_path: str | Path,
    workers: int,
) -> tuple[list[object], dict]:
    """Run only missing tasks and persist each success immediately."""
    path = Path(cache_path)
    keys = [str(key_fn(task)) for task in tasks]
    if len(keys) != len(set(keys)):
        raise ValueError("cache keys must be unique")
    cached = _load(path)
    missing = [
        (key, task)
        for key, task in zip(keys, tasks)
        if key not in cached
    ]
    failures = []
    if missing:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(worker, task): key
                for key, task in missing
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    cached[key] = future.result()
                    _write(path, cached)
                except Exception as exc:  # noqa: BLE001
                    failures.append((key, type(exc).__name__, str(exc)[:240]))
    stats = {
        "requested": len(tasks),
        "resumed": len(tasks) - len(missing),
        "api_calls": len(missing),
        "completed_cached": sum(key in cached for key in keys),
        "failed": len(failures),
        "cache_path": str(path),
    }
    if failures:
        raise CachedRunError(
            f"{len(failures)} cached API task(s) failed; successes are persisted: "
            f"{failures}"
        )
    return [cached[key] for key in keys], stats
