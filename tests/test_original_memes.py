import unittest

from app.gemini import GeminiError, OriginalDraft, _original_text_prompt, _validate_original


def draft(post: str) -> OriginalDraft:
    return OriginalDraft(
        True,
        post,
        "test angle",
        "",
        "technology culture",
        "tiny escalation",
        "unexpected literalism",
        "one-line deadpan escalation",
        "tiny_error_post",
    )


class OriginalTextValidationTests(unittest.TestCase):
    def test_valid_text_only_original_has_two_distinct_emojis(self) -> None:
        value = draft("my software update asked me to emotionally prepare 😭🔥")
        self.assertIs(_validate_original(value, {}, include_handle=False), value)

    def test_accepts_every_emoji_in_requested_palette(self) -> None:
        allowed = ("👀", "🔥", "😭", "❤️‍🩹", "😂", "😙", "🥀", "🤣", "🥳", "🫠", "😤", "💀")
        for emoji in allowed:
            with self.subTest(emoji=emoji):
                other = "🔥" if emoji != "🔥" else "😂"
                value = draft(f"this build has feelings {emoji}{other}")
                self.assertIs(_validate_original(value, {}, include_handle=False), value)

    def test_rejects_a_single_emoji_for_automated_posts(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("my software update asked me to emotionally prepare 😭"),
                {},
                include_handle=False,
            )

    def test_rejects_repeated_emoji(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("my software update asked me to emotionally prepare 😭😭"),
                {},
                include_handle=False,
            )

    def test_enforces_higher_randomized_emoji_target(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("software entered goblin mode 😭🔥"),
                {},
                include_handle=False,
                minimum_emoji_count=4,
            )

    def test_enforces_maximum_for_one_extra_emoji(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("software entered goblin mode 😭🔥👀💀🫠🥳"),
                {},
                include_handle=False,
                minimum_emoji_count=4,
                maximum_emoji_count=5,
            )

    def test_accepts_mending_heart_from_allowlist(self) -> None:
        value = draft("this build broke my heart 😂❤️‍🩹")
        self.assertIs(_validate_original(value, {}, include_handle=False), value)

    def test_rejects_emoji_outside_allowlist(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("software update is here 😭🔥🙂"),
                {},
                include_handle=False,
            )

    def test_trend_prompt_requires_a_real_source_and_manual_review(self) -> None:
        prompt = _original_text_prompt(
            {"trend": "NVIDIA RTX", "text": "NVIDIA posted a new RTX driver update."},
            content_mode="trend_manual",
        )
        self.assertIn("REAL X CONVERSATION — MANUAL DRAFT ONLY", prompt)
        self.assertIn("You MUST build the joke around the supplied recent X post", prompt)
        self.assertIn("source URL is for human review only", prompt)

    def test_person_photo_prompt_uses_real_photo_and_screenshot_style(self) -> None:
        prompt = _original_text_prompt(
            {"trend": "Sam Altman", "text": "Sam Altman speaking at an event"},
            content_mode="person_prompt",
        )
        self.assertIn("REAL-PERSON PHOTO PROMPT", prompt)
        self.assertIn("existing, real photograph", prompt)
        self.assertIn("3 words with Tibo", prompt)
        self.assertIn("never over 16 words or 120 characters", prompt)
        self.assertIn("Do not claim they actually said, did, endorsed, or believe anything", prompt)
        self.assertIn("Never repeat an emoji token", prompt)

    def test_person_prompt_rejects_long_caption(self) -> None:
        long_caption = "This is a very long description of a person and the entire story goes on and on without getting to the point 👀💀"
        with self.assertRaisesRegex(GeminiError, "no longer than 120 characters"):
            _validate_original(
                draft(long_caption),
                {},
                include_handle=False,
                maximum_char_count=120,
                maximum_word_count=16,
            )

    def test_person_prompt_rejects_too_many_words(self) -> None:
        with self.assertRaisesRegex(GeminiError, "no more than 16 words"):
            _validate_original(
                draft("You are sitting right next to Tibo today and have an entire paragraph to tell him about Codex 👀💀"),
                {},
                include_handle=False,
                maximum_char_count=120,
                maximum_word_count=16,
            )

    def test_invented_prompt_is_not_trend_based(self) -> None:
        prompt = _original_text_prompt(
            {"trend": "AI and strange software behaviour", "text": ""},
            content_mode="invented",
        )
        self.assertIn("INVENTED ORIGINAL — NOT TREND-BASED", prompt)
        self.assertIn("Invent a genuinely fresh premise", prompt)

    def test_trend_validation_requires_a_source_anchor(self) -> None:
        source = {
            "trend": "NVIDIA RTX",
            "text": "NVIDIA posted a new RTX driver update.",
        }
        value = draft("NVIDIA drivers have entered their side quest era 😭🔥")
        self.assertIs(
            _validate_original(
                value,
                {},
                include_handle=False,
                source=source,
                require_source_link=True,
            ),
            value,
        )

    def test_trend_validation_rejects_an_unrelated_joke(self) -> None:
        source = {
            "trend": "NVIDIA RTX",
            "text": "NVIDIA posted a new RTX driver update.",
        }
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("my keyboard has developed a retirement plan 😭🔥"),
                {},
                include_handle=False,
                source=source,
                require_source_link=True,
            )

    def test_rejects_reused_recent_format(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("my keyboard has developed a retirement plan 😭🔥"),
                {},
                include_handle=False,
                format_memory=[{
                    "format_name": "tiny escalation",
                    "structure_signature": "one-line deadpan escalation",
                }],
            )

    def test_rejects_country_reference(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("my software update is visiting India 😭🔥"),
                {},
                include_handle=False,
            )

    def test_rejects_mentions_in_automated_original(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("even @OpenAI couldn't fix my sleep schedule 😭🔥"),
                {},
                include_handle=False,
            )

    def test_rejects_blocked_political_content(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("this software needs an election mode 😭🔥"),
                {},
                include_handle=False,
            )


if __name__ == "__main__":
    unittest.main()
