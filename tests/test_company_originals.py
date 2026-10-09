import unittest

from app.gemini import GeminiError, OriginalDraft, _company_prompt, _validate_original


class CompanyOriginalValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = {"handle": "@BMW", "name": "BMW"}

    def _draft(self, post: str, hook_type: str = "if_condition") -> OriginalDraft:
        return OriginalDraft(
            True,
            post,
            "test angle",
            "@BMW",
            "BMW",
            "test format",
            "test mechanism",
            "test structure",
            hook_type,
        )

    def test_company_prompt_encourages_varied_challenges(self) -> None:
        prompt = _company_prompt({
            "handle": "@BMW",
            "name": "BMW",
            "keywords": ["bmw", "m4", "m3"],
        })
        self.assertIn("Do not write truth-or-dare prompts", prompt)
        self.assertIn("Do NOT default to", prompt)
        self.assertIn("Never ask for a repost", prompt)
        self.assertIn("😂 👀 🥳 🫠", prompt)
        self.assertNotIn("truth_or_dare", prompt)
        self.assertNotIn("I'll buy the M4 if you repost this", prompt)
        self.assertNotIn("image_prompt", prompt.lower())

    def test_valid_if_condition_company_caption(self) -> None:
        draft = self._draft(
            "@BMW if the M4 can parallel park by itself, I will forgive my driving 😂"
        )
        self.assertIs(_validate_original(draft, self.target), draft)

    def test_valid_imagine_scenario_company_caption(self) -> None:
        draft = self._draft("@BMW imagine the M4 has a setting called “I know a shortcut” 👀", "imagine_scenario")
        self.assertIs(_validate_original(draft, self.target), draft)

    def test_company_caption_accepts_only_company_emoji_palette(self) -> None:
        for emoji in ("😂", "👀", "🥳", "🫠"):
            with self.subTest(emoji=emoji):
                draft = self._draft(f"@BMW make the M4 announce every pothole {emoji}")
                self.assertIs(_validate_original(draft, self.target), draft)

    def test_company_caption_rejects_non_company_emoji(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(self._draft("@BMW make the M4 announce every pothole 😭"), self.target)

    def test_company_caption_rejects_repeated_emoji(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(self._draft("@BMW make the M4 announce every pothole 😂😂"), self.target)

    def test_requires_exact_target_handle(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("if the M4 parks itself, I will forgive my driving 😂"),
                self.target,
            )

    def test_rejects_extra_mention(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW ask @NVIDIA to co-sign this 😂"),
                self.target,
            )

    def test_rejects_country_reference(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW bring the M4 to India 😂"),
                self.target,
            )

    def test_rejects_missing_emoji(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW make the M4 announce every pothole"),
                self.target,
            )

    def test_rejects_repeated_emoji(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW give the M4 a dramatic intro 😂😂"),
                self.target,
            )

    def test_rejects_blocked_political_terms(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                self._draft("@BMW add election mode 😂"),
                self.target,
            )


if __name__ == "__main__":
    unittest.main()
