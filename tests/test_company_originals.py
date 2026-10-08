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
            "tiny_challenge",
        )

    def test_valid_company_original(self) -> None:
        draft = self._draft("@BMW the M4 needs a button that deletes my group chat.")
        self.assertIs(_validate_original(draft, self.target), draft)

    def test_rejects_extra_mention(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW ask @NVIDIA to co-sign this."),
                self.target,
            )

    def test_rejects_country_reference(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW I need the M4 with zero India references."),
                self.target,
            )

    def test_rejects_engagement_bait(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW repost this and I will buy an M4."),
                self.target,
            )


if __name__ == "__main__":
    unittest.main()
