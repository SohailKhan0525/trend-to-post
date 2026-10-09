import unittest

from app.gemini import GeminiError, OriginalDraft, _company_prompt, _validate_original


class CompanyOriginalValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = {"handle": "@BMW", "name": "BMW"}

    def _draft(self, post: str) -> OriginalDraft:
        return OriginalDraft(
            True,
            post,
            "test angle",
            "@BMW",
            "BMW",
            "test format",
            "test mechanism",
            "test structure",
            "truth_or_dare",
        )

    def test_company_prompt_encourages_varied_challenges(self) -> None:
        prompt = _company_prompt({
            "handle": "@BMW",
            "name": "BMW",
            "keywords": ["bmw", "m4", "m3"],
        })
        self.assertIn("truth-or-dare", prompt)
        self.assertIn("Do NOT default to", prompt)
        self.assertIn("Never ask for a repost", prompt)
        self.assertNotIn("I'll buy the M4 if you repost this", prompt)
        self.assertNotIn("image_prompt", prompt.lower())

    def test_valid_truth_or_dare_company_caption(self) -> None:
        draft = self._draft(
            "@BMW truth or dare: truth—admit the M4 is a side quest; "
            "dare—make its lights blink in morse code 😭"
        )
        self.assertIs(_validate_original(draft, self.target), draft)

    def test_valid_product_specific_dare(self) -> None:
        draft = self._draft("@BMW I dare you to give the M4 a dramatic boss-fight intro 💀")
        self.assertIs(_validate_original(draft, self.target), draft)

    def test_requires_exact_target_handle(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("truth or dare: let the M4 judge my parking 😭"),
                self.target,
            )

    def test_rejects_extra_mention(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW ask @NVIDIA to co-sign this 😭"),
                self.target,
            )

    def test_rejects_country_reference(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW bring the M4 to India 😭"),
                self.target,
            )

    def test_rejects_missing_emoji(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW I dare you to give the M4 a boss-fight intro"),
                self.target,
            )

    def test_rejects_repeated_emoji(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW give the M4 a dramatic intro 😭😭"),
                self.target,
            )

    def test_rejects_blocked_political_terms(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW add election mode 😭"),
                self.target,
            )


if __name__ == "__main__":
    unittest.main()
