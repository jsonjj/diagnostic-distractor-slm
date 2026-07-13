import tempfile
from pathlib import Path
import unittest

from src.api_cache import CachedRunError, run_cached


class ApiCacheTests(unittest.TestCase):
    def test_completed_calls_are_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.jsonl"
            calls = []

            def worker(value):
                calls.append(value)
                return {"value": value * 10}

            first, first_stats = run_cached(
                [1, 2, 3],
                key_fn=str,
                worker=worker,
                cache_path=path,
                workers=2,
            )
            second, second_stats = run_cached(
                [1, 2, 3],
                key_fn=str,
                worker=worker,
                cache_path=path,
                workers=2,
            )

            self.assertEqual([row["value"] for row in first], [10, 20, 30])
            self.assertEqual(first, second)
            self.assertEqual(first_stats["api_calls"], 3)
            self.assertEqual(second_stats["api_calls"], 0)
            self.assertEqual(second_stats["resumed"], 3)
            self.assertCountEqual(calls, [1, 2, 3])

    def test_successes_survive_a_partial_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.jsonl"

            def failing(value):
                if value == 3:
                    raise RuntimeError("transient")
                return {"value": value}

            with self.assertRaises(CachedRunError):
                run_cached(
                    [1, 2, 3],
                    key_fn=str,
                    worker=failing,
                    cache_path=path,
                    workers=1,
                )

            calls = []

            def recovered(value):
                calls.append(value)
                return {"value": value}

            rows, stats = run_cached(
                [1, 2, 3],
                key_fn=str,
                worker=recovered,
                cache_path=path,
                workers=1,
            )

            self.assertEqual(rows, [{"value": 1}, {"value": 2}, {"value": 3}])
            self.assertEqual(calls, [3])
            self.assertEqual(stats["api_calls"], 1)
            self.assertEqual(stats["resumed"], 2)


if __name__ == "__main__":
    unittest.main()
