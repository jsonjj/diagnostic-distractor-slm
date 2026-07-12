from __future__ import annotations

from dataclasses import replace
import json
import unittest

from services.wayline_forge.app.providers.recorded import (
    RecordedDistractorProvider,
)


class GenerationSoakTests(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_soak_displays_only_verifier_accepted_generations(
        self,
    ) -> None:
        from services.wayline_forge.scripts.run_generation_soak import (
            build_recorded_fixture,
            execute_recorded_soak,
        )

        fixture = build_recorded_fixture(item_count=30, reject_every=5)
        report = await execute_recorded_soak(
            fixture.blueprints,
            live_provider=RecordedDistractorProvider(fixture.live_recordings),
            fallback_provider=RecordedDistractorProvider(
                fixture.fallback_recordings
            ),
            verifier=fixture.verifier,
        )

        self.assertEqual(report.requested_count, 30)
        self.assertEqual(report.displayed_count, 30)
        self.assertEqual(report.verified_live_count, 24)
        self.assertEqual(report.verified_fallback_count, 6)
        self.assertEqual(report.displayed_unverified_count, 0)
        self.assertEqual(sum(report.rejection_counts.values()), 6)

    async def test_unverified_recorded_fallback_fails_closed(self) -> None:
        from services.wayline_forge.scripts.run_generation_soak import (
            GenerationSoakError,
            build_recorded_fixture,
            execute_recorded_soak,
        )

        fixture = build_recorded_fixture(item_count=3, reject_every=1)
        fallback = dict(fixture.fallback_recordings)
        first = fixture.blueprints[0]
        fallback[first.question_id] = replace(
            fallback[first.question_id],
            text='{"distractors":[]}',
        )

        with self.assertRaises(GenerationSoakError) as caught:
            await execute_recorded_soak(
                fixture.blueprints,
                live_provider=RecordedDistractorProvider(
                    fixture.live_recordings
                ),
                fallback_provider=RecordedDistractorProvider(fallback),
                verifier=fixture.verifier,
            )

        self.assertEqual(caught.exception.code, "fallback_unverified")

    async def test_report_is_canonical_and_contains_no_raw_learning_content(
        self,
    ) -> None:
        from services.wayline_forge.scripts.run_generation_soak import (
            build_recorded_fixture,
            execute_recorded_soak,
        )

        fixture = build_recorded_fixture(item_count=6, reject_every=3)
        report = await execute_recorded_soak(
            fixture.blueprints,
            live_provider=RecordedDistractorProvider(fixture.live_recordings),
            fallback_provider=RecordedDistractorProvider(
                fixture.fallback_recordings
            ),
            verifier=fixture.verifier,
        )
        serialized = report.to_json()

        self.assertEqual(
            serialized,
            json.dumps(
                json.loads(serialized),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        lowered = serialized.casefold()
        for banned in (
            "correctanswer",
            "misconception",
            "profileid",
            "prompt",
            "rawslm",
            "sessionid",
            "trustedsteps",
        ):
            self.assertNotIn(banned, lowered)


if __name__ == "__main__":
    unittest.main()
