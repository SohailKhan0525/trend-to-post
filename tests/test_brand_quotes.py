import unittest

from app.gemini import (
    GeminiError,
    QuoteDraft,
    _prompt,
    _validate_quote_against_source,
)


def source():
    return {
        "text": "We shipped the new AI software feature today.",
        "trend": "OpenAI AI software",
        "view_count": 1000,
        "favorite_count": 20,
        "retweet_count": 5,
        "reply_count": 4,
        "author_followers": 100000,
        "author_verified": True,
        "author_professional": True,
    }


def quote(text):
    return QuoteDraft(
        True,
        text,
        "specific and original angle",
        "fresh format",
        "unexpected escalation",
        "",
        False,
        "quote_post",
        "invented_rule",
        "new structure signature",
    )


class BrandQuoteTests(unittest.TestCase):
    def test_accepts_a_valid_attached_quote_comment(self):
        draft = quote('the release notes need a boss fight 😂 👀 "AI software feature"')
        result = _validate_quote_against_source(draft, source(), 2, 3)
        self.assertTrue(result.should_quote)

    def test_rejects_mentions_in_the_authored_comment(self):
        draft = quote('@OpenAI the release notes need a boss fight 😂 👀 "AI software feature"')
        with self.assertRaisesRegex(GeminiError, "@mention"):
            _validate_quote_against_source(draft, source(), 2, 3)

    def test_rejects_unapproved_emojis(self):
        draft = quote('the release notes need a boss fight 😎 👀 "AI software feature"')
        with self.assertRaisesRegex(GeminiError, "outside the approved palette"):
            _validate_quote_against_source(draft, source(), 2, 3)

    def test_rejects_repeated_emojis(self):
        draft = quote('the release notes need a boss fight 😂 😂 "AI software feature"')
        with self.assertRaisesRegex(GeminiError, "repeated an emoji"):
            _validate_quote_against_source(draft, source(), 2, 3)

    def test_rejects_hashtags_and_urls(self):
        for text in (
            'the release notes need a boss fight 😂 👀 #OpenAI "AI software feature"',
            'the release notes need a boss fight 😂 👀 https://example.com "AI software feature"',
        ):
            with self.subTest(text=text):
                with self.assertRaises(GeminiError):
                    _validate_quote_against_source(quote(text), source(), 2, 3)


    def test_rejects_reused_format_name(self):
        draft = quote('the release notes need a boss fight 😂 👀 "AI software feature"')
        with self.assertRaisesRegex(GeminiError, "repeats a recent format name"):
            _validate_quote_against_source(
                draft,
                source(),
                2,
                3,
                [{"format_name": "fresh format", "post_text": "older joke"}],
            )

    def test_prompt_includes_prior_text_and_strict_no_tag_rules(self):
        prompt = _prompt(
            source(),
            [{
                "format_name": "past format",
                "comedy_mechanism": "a previous joke",
                "structure_signature": "one old structure",
                "post_text": 'the old joke 😂 👀 "AI software feature"',
            }],
            emoji_count=3,
        )
        self.assertIn("prior_post=", prompt)
        self.assertIn("Do not add @mentions, hashtags, or URLs", prompt)
        self.assertIn("Do not just swap nouns or paraphrase an old joke", prompt)
        self.assertIn("Use at least 3 and at most 4 distinct emojis", prompt)


if __name__ == "__main__":
    unittest.main()
