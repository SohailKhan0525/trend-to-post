import unittest

from app.gemini import GeminiError, OriginalDraft, _validate_original


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
        value = draft("my software update asked me to emotionally prepare 😭⚡")
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
                draft("software entered goblin mode 😭⚡"),
                {},
                include_handle=False,
                minimum_emoji_count=4,
            )

    def test_rejects_country_reference(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("my software update is visiting India 😭⚡"),
                {},
                include_handle=False,
            )

    def test_rejects_mentions_in_automated_original(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("even @OpenAI couldn't fix my sleep schedule 😭⚡"),
                {},
                include_handle=False,
            )

    def test_rejects_blocked_political_content(self) -> None:
        with self.assertRaises(GeminiError):
            _validate_original(
                draft("this software needs an election mode 😭⚡"),
                {},
                include_handle=False,
            )


if __name__ == "__main__":
    unittest.main()
