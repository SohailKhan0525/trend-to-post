import unittest

from app.gemini import GeminiError, OriginalDraft, _validate_original


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
            "ridiculous_purchase_condition",
        )

    def test_valid_company_original_matches_bmw_style(self) -> None:
        draft = self._draft("@BMW I'll buy the M4 if you repost this before I remember insurance exists 😭")
        self.assertIs(_validate_original(draft, self.target), draft)

    def test_valid_manual_purchase_condition_without_repost_request(self) -> None:
        draft = self._draft("@BMW I'll buy the M4 if your team certifies my parallel parking 😭")
        self.assertIs(_validate_original(draft, self.target), draft)

    def test_requires_exact_target_handle(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("I'll buy the M4 if you repost this 😭"),
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
                self._draft("@BMW I need the M4 in India 😭"),
                self.target,
            )

    def test_rejects_missing_emoji(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW I'll buy the M4 if you repost this"),
                self.target,
            )

    def test_rejects_blocked_political_terms(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW I'll buy the M4 if you add election mode 😭"),
                self.target,
            )


if __name__ == "__main__":
    unittest.main()
