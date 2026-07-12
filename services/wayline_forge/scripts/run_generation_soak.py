"""Offline recorded-provider/verifier soak with no model or paid API calls."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import statistics
import sys
import time
from types import MappingProxyType

from services.wayline_forge.app.distractor_verifier import (
    DistractorVerifier,
    VerifiedDistractorSet,
)
from services.wayline_forge.app.providers.distractor import (
    DistractorProvider,
    ProviderError,
    RawSlmGeneration,
)
from services.wayline_forge.app.providers.recorded import (
    RecordedDistractorProvider,
)
from services.wayline_forge.app.question_kernel import (
    CompilationError,
    CompileRequest,
    QuestionBlueprint,
)
from services.wayline_forge.app.slm_prompt import build_slm_request


_REPORT_SCHEMA = "wayline.recorded-generation-soak.v1"
_ERROR_CODES = frozenset(
    {
        "fallback_unavailable",
        "fallback_unverified",
        "fixture_generation_failed",
        "report_write_failed",
    }
)


class GenerationSoakError(RuntimeError):
    """Stable, content-free failure for the offline safety soak."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_CODES:
            raise ValueError("unknown generation soak error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RecordedSoakFixture:
    verifier: DistractorVerifier
    blueprints: tuple[QuestionBlueprint, ...]
    live_recordings: Mapping[str, RawSlmGeneration]
    fallback_recordings: Mapping[str, RawSlmGeneration]


@dataclass(frozen=True, slots=True)
class GenerationSoakReport:
    requested_count: int
    displayed_count: int
    verified_live_count: int
    verified_fallback_count: int
    displayed_unverified_count: int
    rejection_counts: Mapping[str, int]
    mean_latency_ms: float
    p95_latency_ms: float
    maximum_latency_ms: float
    schema_version: str = _REPORT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != _REPORT_SCHEMA:
            raise ValueError("generation soak report schema is invalid")
        counts = (
            self.requested_count,
            self.displayed_count,
            self.verified_live_count,
            self.verified_fallback_count,
            self.displayed_unverified_count,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counts
        ):
            raise ValueError("generation soak counts are invalid")
        if self.displayed_count != (
            self.verified_live_count + self.verified_fallback_count
        ):
            raise ValueError("displayed count must be fully provenance-accounted")
        if self.displayed_count > self.requested_count:
            raise ValueError("displayed count cannot exceed requested count")
        if any(value < 0 for value in self.rejection_counts.values()):
            raise ValueError("rejection counts cannot be negative")

    def to_json(self) -> str:
        return json.dumps(
            {
                "displayedCount": self.displayed_count,
                "displayedUnverifiedCount": self.displayed_unverified_count,
                "maximumLatencyMs": self.maximum_latency_ms,
                "meanLatencyMs": self.mean_latency_ms,
                "mode": "recorded-provider-verifier-smoke",
                "p95LatencyMs": self.p95_latency_ms,
                "rejectionCounts": dict(sorted(self.rejection_counts.items())),
                "requestedCount": self.requested_count,
                "schemaVersion": self.schema_version,
                "verifiedFallbackCount": self.verified_fallback_count,
                "verifiedLiveCount": self.verified_live_count,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _valid_generation(
    verifier: DistractorVerifier,
    blueprint: QuestionBlueprint,
) -> RawSlmGeneration:
    procedures = blueprint.allowed_procedure_ids[:3]
    if len(procedures) != 3:
        raise GenerationSoakError("fixture_generation_failed")
    request = build_slm_request(blueprint)
    distractors = [
        {
            "answer": verifier.registry.evaluate(
                procedure_id,
                blueprint,
            ).display,
            "computation": verifier.registry.canonical_computation(
                procedure_id,
                blueprint,
            ),
            "misconception": verifier.registry.canonical_label(procedure_id),
        }
        for procedure_id in procedures
    ]
    manifest = verifier.manifest
    return RawSlmGeneration(
        text=_canonical_json({"distractors": distractors}),
        model_sha256=manifest.model_sha256,
        prompt_sha256=request.prompt_sha256,
        generated_at_utc="2026-07-12T12:00:00Z",
        adapter_identity_receipt_sha256=(
            manifest.adapter_identity_receipt_sha256
        ),
        gguf_sha256=manifest.gguf_sha256,
        generator_identity_receipt_sha256=(
            manifest.generator_identity_receipt_sha256
        ),
        registry_id=manifest.registry_id,
        prompt_template_sha256=manifest.prompt_template_sha256,
    )


def build_recorded_fixture(
    *,
    item_count: int,
    reject_every: int = 10,
) -> RecordedSoakFixture:
    """Create exact deterministic recordings across all launch families."""

    if (
        isinstance(item_count, bool)
        or not isinstance(item_count, int)
        or not 1 <= item_count <= 100_000
    ):
        raise ValueError("item_count must be between 1 and 100000")
    if (
        isinstance(reject_every, bool)
        or not isinstance(reject_every, int)
        or not 0 <= reject_every <= item_count
    ):
        raise ValueError("reject_every must be zero or a valid item interval")

    verifier = DistractorVerifier.for_tests()
    families = tuple(
        sorted(
            verifier.compiler.curriculum.families.values(),
            key=lambda family: family.family_id,
        )
    )
    blueprints: list[QuestionBlueprint] = []
    live: dict[str, RawSlmGeneration] = {}
    fallback: dict[str, RawSlmGeneration] = {}
    candidate_index = 0
    maximum_candidates = item_count * 8
    while len(blueprints) < item_count and candidate_index < maximum_candidates:
        family = families[candidate_index % len(families)]
        seed = 1_000_003 + candidate_index * 7_919
        difficulty = 1 + candidate_index % 3
        candidate_index += 1
        try:
            blueprint = verifier.compiler.compile(
                CompileRequest(
                    world_id=family.world_id,
                    skill_id=family.skill_id,
                    family_id=family.family_id,
                    difficulty=difficulty,
                    seed=seed,
                )
            )
        except CompilationError:
            continue
        if blueprint.question_id in fallback:
            continue
        valid = _valid_generation(verifier, blueprint)
        verification = verifier.verify_generation(blueprint, valid)
        if not verification.accepted:
            raise GenerationSoakError("fixture_generation_failed")
        ordinal = len(blueprints) + 1
        fallback[blueprint.question_id] = valid
        live[blueprint.question_id] = (
            replace(valid, text='{"distractors":[]}')
            if reject_every and ordinal % reject_every == 0
            else valid
        )
        blueprints.append(blueprint)
    if len(blueprints) != item_count:
        raise GenerationSoakError("fixture_generation_failed")
    return RecordedSoakFixture(
        verifier=verifier,
        blueprints=tuple(blueprints),
        live_recordings=MappingProxyType(live),
        fallback_recordings=MappingProxyType(fallback),
    )


async def execute_recorded_soak(
    blueprints: Sequence[QuestionBlueprint],
    *,
    live_provider: DistractorProvider,
    fallback_provider: DistractorProvider,
    verifier: DistractorVerifier,
    clock=time.perf_counter,
) -> GenerationSoakReport:
    """Verify each recording and make accepted value the sole display gate."""

    if not isinstance(verifier, DistractorVerifier):
        raise TypeError("verifier must be a DistractorVerifier")
    if not callable(clock):
        raise TypeError("clock must be callable")
    requested = len(blueprints)
    live_count = 0
    fallback_count = 0
    displayed = 0
    displayed_unverified = 0
    rejection_counts: Counter[str] = Counter()
    latencies_ms: list[float] = []

    for blueprint in blueprints:
        if not isinstance(blueprint, QuestionBlueprint):
            raise TypeError("every soak input must be a QuestionBlueprint")
        request = build_slm_request(blueprint)
        started = float(clock())
        accepted: VerifiedDistractorSet | None = None
        try:
            live_generation = await live_provider.generate(request)
        except ProviderError as error:
            rejection_counts[f"provider_{error.code}"] += 1
        else:
            live_verification = verifier.verify_generation(
                blueprint,
                live_generation,
            )
            if live_verification.accepted:
                accepted = live_verification.value
                live_count += 1
            else:
                rejection_counts[live_verification.code or "unknown"] += 1

        if accepted is None:
            try:
                fallback_generation = await fallback_provider.generate(request)
            except ProviderError:
                raise GenerationSoakError("fallback_unavailable") from None
            fallback_verification = verifier.verify_generation(
                blueprint,
                fallback_generation,
            )
            if (
                not fallback_verification.accepted
                or fallback_verification.value is None
            ):
                raise GenerationSoakError("fallback_unverified")
            accepted = fallback_verification.value
            fallback_count += 1

        if not isinstance(accepted, VerifiedDistractorSet):
            displayed_unverified += 1
            raise GenerationSoakError("fallback_unverified")
        displayed += 1
        elapsed = max(0.0, (float(clock()) - started) * 1000.0)
        latencies_ms.append(elapsed)

    ordered = sorted(latencies_ms)
    if ordered:
        p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
        mean_latency = statistics.fmean(ordered)
        p95_latency = ordered[p95_index]
        maximum_latency = ordered[-1]
    else:
        mean_latency = p95_latency = maximum_latency = 0.0
    return GenerationSoakReport(
        requested_count=requested,
        displayed_count=displayed,
        verified_live_count=live_count,
        verified_fallback_count=fallback_count,
        displayed_unverified_count=displayed_unverified,
        rejection_counts=MappingProxyType(dict(sorted(rejection_counts.items()))),
        mean_latency_ms=round(mean_latency, 3),
        p95_latency_ms=round(p95_latency, 3),
        maximum_latency_ms=round(maximum_latency, 3),
    )


async def run_recorded_soak(
    *,
    item_count: int = 1_000,
    reject_every: int = 10,
) -> GenerationSoakReport:
    fixture = build_recorded_fixture(
        item_count=item_count,
        reject_every=reject_every,
    )
    return await execute_recorded_soak(
        fixture.blueprints,
        live_provider=RecordedDistractorProvider(fixture.live_recordings),
        fallback_provider=RecordedDistractorProvider(
            fixture.fallback_recordings
        ),
        verifier=fixture.verifier,
    )


def _write_report(path: Path, report: GenerationSoakReport) -> None:
    if path.exists() or path.is_symlink():
        raise GenerationSoakError("report_write_failed")
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        temporary = parent / f".{path.name}.tmp-{os.getpid()}"
        temporary.write_text(report.to_json() + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        raise GenerationSoakError("report_write_failed") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_generation_soak")
    parser.add_argument("--items", type=int, default=1_000)
    parser.add_argument("--reject-every", type=int, default=10)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = asyncio.run(
            run_recorded_soak(
                item_count=arguments.items,
                reject_every=arguments.reject_every,
            )
        )
        if arguments.output is not None:
            _write_report(arguments.output.absolute(), report)
        else:
            print(report.to_json())
    except (GenerationSoakError, TypeError, ValueError) as error:
        code = getattr(error, "code", "fixture_generation_failed")
        print(f"wayline_generation_soak_failed: {code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GenerationSoakError",
    "GenerationSoakReport",
    "RecordedSoakFixture",
    "build_recorded_fixture",
    "execute_recorded_soak",
    "run_recorded_soak",
]
