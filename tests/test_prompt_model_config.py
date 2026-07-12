import unittest

from src import prompt_model


class PromptModelConfigTests(unittest.TestCase):
    def test_defaults_point_to_final_v71_model(self):
        self.assertEqual(prompt_model.BASE, "unsloth/Qwen3-4B-bnb-4bit")
        self.assertEqual(
            prompt_model.ADAPTER,
            "j2ampn/qwen3-4b-distractor-lora-v7",
        )


if __name__ == "__main__":
    unittest.main()
