import unittest

from app.gemini import GeminiError, OriginalDraft, _validate_original


def sample_draft(
    post: str,
    conversation: list[dict] | None = None,
    image_prompt: str = "surreal glowing keyboard with a tiny confused robot, no text or logos",
) -> OriginalDraft:
    return OriginalDraft(
        True,
        post,
        "a small absurd escalation",
        "",
        "technology culture",
        "tiny escalation",
        "unexpected literalism",
        "request escalation final system punchline",
        "fake_support_chat",
        conversation or [
            {"speaker": "USER", "text": "Can you make my software less dramatic?"},
            {"speaker": "AI", "text": "I lowered the drama setting to 98%."},
            {"speaker": "SYSTEM", "text": "The settings menu has requested a sabbatical."},
        ],
        image_prompt,
    )


class OriginalMemeValidationTests(unittest.TestCase):
    def test_valid_standalone_original_has_no_mentions(self) -> None:
        draft = sample_draft("My software update just asked me to emotionally prepare.")
        self.assertIs(_validate_original(draft, {}, include_handle=False), draft)

    def test_rejects_country_reference_in_dialogue(self) -> None:
        draft = sample_draft(
            "My software update has become sentient.",
            [
                {"speaker": "USER", "text": "Open settings."},
                {"speaker": "AI", "text": "Okay."},
                {"speaker": "SYSTEM", "text": "Greetings from India."},
            ],
        )
        with self.assertRaises(GeminiError):
            _validate_original(draft, {}, include_handle=False)

    def test_rejects_missing_punchline_bubbles(self) -> None:
        draft = sample_draft(
            "My software update has become sentient.",
            [{"speaker": "USER", "text": "Open settings."}],
        )
        with self.assertRaises(GeminiError):
            _validate_original(draft, {}, include_handle=False)

    def test_rejects_mention_in_image_dialogue(self) -> None:
        draft = sample_draft(
            "My software update has become sentient.",
            [
                {"speaker": "USER", "text": "Open settings."},
                {"speaker": "AI", "text": "Ask @OpenAI."},
                {"speaker": "SYSTEM", "text": "The menu is now on leave."},
            ],
        )
        with self.assertRaises(GeminiError):
            _validate_original(draft, {}, include_handle=False)

    def test_rejects_country_in_art_prompt(self) -> None:
        draft = sample_draft(
            "My software update has become sentient.",
            image_prompt="a robot with the skyline of India, no text or logos",
        )
        with self.assertRaises(GeminiError):
            _validate_original(draft, {}, include_handle=False)


if __name__ == "__main__":
    unittest.main()
