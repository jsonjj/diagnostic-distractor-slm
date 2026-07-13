import json
import unittest

from src.config import (
    OPUS_MODEL_ID,
    TFY_BASE_URL,
    TFY_EXTRA_HEADERS,
    resolve_project_environment,
    resolve_tfy_models,
)
from src.eval import judge_consistency
from src.prompts import build_assistant, build_user
from src.real_computations import _process
from src.run_frontier import _predict, validate_call_budget
from src.v8_teacher import DETERMINISTIC_TEACHER_ROUTE


class TrueFoundryModelRoleTests(unittest.TestCase):
    def test_gateway_url_and_owner_headers_are_exact(self):
        self.assertEqual(
            TFY_BASE_URL,
            "https://tfy-eu.promptlens.trilogy.com",
        )
        self.assertEqual(
            TFY_EXTRA_HEADERS,
            {
                "X-TFY-METADATA": "{}",
                "X-TFY-LOGGING-CONFIG": '{"enabled": true}',
            },
        )

    def test_project_dotenv_overrides_stale_inherited_gateway_values(self):
        resolved = resolve_project_environment(
            {
                "TFY_API_KEY": "stale-process-token",
                "TFY_MODEL": "claude-sonnet-5",
            },
            {
                "TFY_API_KEY": "rotated-dotenv-token",
                "TFY_TEACHER_MODEL": OPUS_MODEL_ID,
            },
        )

        self.assertEqual(resolved["TFY_API_KEY"], "rotated-dotenv-token")
        self.assertEqual(resolved["TFY_TEACHER_MODEL"], OPUS_MODEL_ID)
        self.assertEqual(resolved["TFY_MODEL"], "claude-sonnet-5")

    def test_empty_dotenv_values_do_not_erase_process_configuration(self):
        resolved = resolve_project_environment(
            {"TFY_API_KEY": "process-token"},
            {"TFY_API_KEY": "", "TFY_MODEL": None},
        )

        self.assertEqual(resolved["TFY_API_KEY"], "process-token")
        self.assertNotIn("TFY_MODEL", resolved)

    def test_legacy_model_remains_the_fallback_for_all_roles(self):
        models = resolve_tfy_models({"TFY_MODEL": "claude-sonnet-5"})

        self.assertEqual(models["legacy"], "claude-sonnet-5")
        self.assertEqual(models["teacher"], "claude-sonnet-5")
        self.assertEqual(models["judge"], "claude-sonnet-5")
        self.assertEqual(models["frontier"], "claude-sonnet-5")
        self.assertEqual(OPUS_MODEL_ID, "anthropic-primary/claude-opus-4-8")

    def test_role_specific_models_override_without_destroying_legacy_baseline(self):
        models = resolve_tfy_models(
            {
                "TFY_MODEL": "claude-sonnet-5",
                "TFY_TEACHER_MODEL": OPUS_MODEL_ID,
                "TFY_JUDGE_MODEL": "independent-judge",
                "TFY_FRONTIER_MODEL": OPUS_MODEL_ID,
            }
        )

        self.assertEqual(models["legacy"], "claude-sonnet-5")
        self.assertEqual(models["teacher"], OPUS_MODEL_ID)
        self.assertEqual(models["judge"], "independent-judge")
        self.assertEqual(models["frontier"], OPUS_MODEL_ID)

    def test_frontier_prediction_passes_and_records_explicit_model(self):
        calls = []

        def fake_chat(messages, **kwargs):
            calls.append(kwargs)
            return json.dumps(
                {
                    "distractors": [
                        {
                            "misconception": "Adds instead",
                            "computation": "6 + 2 = 8",
                            "answer": "8",
                        },
                        {
                            "misconception": "Subtracts instead",
                            "computation": "6 - 2 = 4",
                            "answer": "4",
                        },
                        {
                            "misconception": "Reverses division",
                            "computation": "2 / 6 = 1/3",
                            "answer": "1/3",
                        },
                    ]
                }
            )

        row, error = _predict(
            {
                "id": "q1",
                "question": "What is 6 / 2?",
                "correct": "3",
                "topic": "Division",
            },
            model=OPUS_MODEL_ID,
            chat_fn=fake_chat,
        )

        self.assertIsNone(error)
        self.assertEqual(calls[0]["model"], OPUS_MODEL_ID)
        self.assertEqual(row["generator_model"], OPUS_MODEL_ID)
        self.assertEqual(
            row["question_confidence"]["level"], "not_calibrated"
        )

    def test_deterministic_teacher_mode_constrains_registered_labels_and_records_route(self):
        calls = []

        def fake_chat(messages, **kwargs):
            calls.append((messages, kwargs))
            return json.dumps(
                {
                    "distractors": [
                        {
                            "misconception": "Adds the numerators and the denominators",
                            "computation": "(1 + 1) / (2 + 3) = 2/5",
                            "answer": "2/5",
                        },
                        {
                            "misconception": "Adds numerators and keeps the first denominator",
                            "computation": "(1 + 1) / 2 = 1",
                            "answer": "1",
                        },
                        {
                            "misconception": "Multiplies the fractions instead of adding them",
                            "computation": "1/2 * 1/3 = 1/6",
                            "answer": "1/6",
                        },
                    ]
                }
            )

        row, error = _predict(
            {
                "id": "q1",
                "question": "What is 1/2 + 1/3?",
                "correct": "5/6",
                "topic": "Adding and Subtracting Fractions",
            },
            model=OPUS_MODEL_ID,
            chat_fn=fake_chat,
            deterministic_teacher=True,
        )

        self.assertIsNone(error)
        self.assertEqual(
            row["generation_route"],
            DETERMINISTIC_TEACHER_ROUTE,
        )
        teacher_prompt = calls[0][0][1]["content"]
        self.assertIn("Use only these exact registered labels", teacher_prompt)
        self.assertIn(
            "Adds the numerators and the denominators",
            teacher_prompt,
        )

    def test_judge_uses_injected_judge_model(self):
        calls = []

        def fake_chat(messages, **kwargs):
            calls.append(kwargs)
            return "YES"

        verdict = judge_consistency(
            "What is 6 / 2?",
            "Reverses division",
            "1/3",
            "3",
            model="independent-judge",
            chat_fn=fake_chat,
        )

        self.assertTrue(verdict)
        self.assertEqual(calls[0]["model"], "independent-judge")

    def test_real_teacher_uses_injected_teacher_model(self):
        calls = []
        distractors = [
            {
                "misconception": "Adds instead",
                "answer": "8",
            },
            {
                "misconception": "Subtracts instead",
                "answer": "4",
            },
            {
                "misconception": "Reverses division",
                "answer": "1/3",
            },
        ]
        record = {
            "user": build_user("What is 6 / 2?", "3", "Division"),
            "assistant": build_assistant(distractors),
            "meta": {"id": "q1"},
        }

        def fake_chat(messages, **kwargs):
            calls.append(kwargs)
            return json.dumps(
                {"computations": ["6 + 2 = 8", "6 - 2 = 4", "2 / 6 = 1/3"]}
            )

        enriched, count, detail = _process(
            record,
            fake_chat,
            harden=True,
            model=OPUS_MODEL_ID,
        )

        self.assertEqual(count, 1)
        self.assertTrue(detail["kept"])
        self.assertIsNotNone(enriched)
        self.assertEqual(calls[0]["model"], OPUS_MODEL_ID)

    def test_frontier_runs_require_an_explicit_paid_call_cap(self):
        with self.assertRaisesRegex(ValueError, "max-calls"):
            validate_call_budget(requested=140, max_calls=0)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            validate_call_budget(requested=140, max_calls=30)
        validate_call_budget(requested=140, max_calls=140)


if __name__ == "__main__":
    unittest.main()
