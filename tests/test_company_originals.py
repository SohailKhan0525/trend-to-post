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
            [
                {"speaker": "USER", "text": "Please add a tiny panic button."},
                {"speaker": "AI", "text": "The button is now nervous."},
                {"speaker": "SYSTEM", "text": "It has submitted its resignation."},
            ],
            "a surreal dashboard with a tiny glowing panic button, no text or logos",
        )

    def test_valid_company_original(self) -> None:
        draft = self._draft("@BMW the M4 needs a button that deletes my group chat.")
        self.assertIs(_validate_original(draft, self.target), draft)

    def test_valid_automated_standalone_company_post(self) -> None:
        draft = self._draft("BMW needs one ridiculous button on the dashboard.")
        self.assertIs(_validate_original(draft, self.target, include_handle=False), draft)

    def test_rejects_automated_company_mention(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW needs one ridiculous button on the dashboard."),
                self.target,
                include_handle=False,
            )

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
