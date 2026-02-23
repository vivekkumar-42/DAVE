import unittest

import main


class MainBootstrapTests(unittest.TestCase):
    def test_sanitize_config_scrubs_inline_secrets(self) -> None:
        source = {
            "groq_api_key": "gsk_live_top",
            "gemini_api_key": "AIza_live_top",
            "llm": {
                "groq": {"api_key": "gsk_live_nested"},
                "gemini": {"api_key": "AIza_live_nested"},
            },
        }

        sanitized = main._sanitize_config(source)

        self.assertEqual("", sanitized.get("groq_api_key"))
        self.assertEqual("", sanitized.get("gemini_api_key"))
        self.assertEqual("", sanitized["llm"]["groq"]["api_key"])
        self.assertEqual("", sanitized["llm"]["gemini"]["api_key"])

    def test_placeholder_keys_not_treated_as_live_secrets(self) -> None:
        self.assertFalse(main._looks_like_secret("YOUR_GROQ_KEY"))
        self.assertFalse(main._looks_like_secret("<YOUR_GEMINI_KEY>"))
        self.assertFalse(main._looks_like_secret("REPLACE_ME"))
        self.assertFalse(main._looks_like_secret(""))
        self.assertTrue(main._looks_like_secret("gsk_live_value"))


if __name__ == "__main__":
    unittest.main()
