"""Deterministic recorded provider for tests and offline replay."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from .distractor import ProviderError, RawSlmGeneration, SlmRequest


class RecordedDistractorProvider:
    def __init__(self, recordings: Mapping[str, RawSlmGeneration]):
        self._recordings = MappingProxyType(dict(recordings))

    async def generate(self, request: SlmRequest) -> RawSlmGeneration:
        try:
            generation = self._recordings[request.question_id]
        except KeyError as exc:
            raise ProviderError("recording_not_found") from exc
        if generation.prompt_sha256 != request.prompt_sha256:
            raise ProviderError("recording_receipt_mismatch")
        return generation
