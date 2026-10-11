import unittest

from app.gemini import (
    GeminiError,
    QuoteDraft,
    _normalize_quote_emojis,
    _parse_experiment,
    _prompt,
    _sanitize_quote_comment,
    _validate_quote_against_source,
    _found_emojis,
    _has_unapproved_emoji,
    EMOJI_TOKENS,
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
    def test_accepts_a_standalone_meme_comment_without_source_quotes(self):
        draft = quote("the release notes need a boss fight 😂 👀")
        result = _validate_quote_against_source(draft, source(), 2, 3)
        self.assertTrue(result.should_quote)
        self.assertFalse(any(mark in result.quote_text for mark in ('"', "“", "”", "‘", "’")))

    def test_sanitizer_removes_quote_punctuation_without_adding_source_text(self):
        draft = quote('release notes need a boss fight 😂 👀 "invented fake words"')
        repaired = _sanitize_quote_comment(draft, source())
        self.assertNotIn('"', repaired.quote_text)
        self.assertNotIn("AI software feature", repaired.quote_text)

    def test_emoji_normalizer_removes_unapproved_and_duplicate_tokens(self):
        draft = quote("the release notes need a boss fight 😎 😂 😂")
        normalized = _normalize_quote_emojis(draft, 3)
        found = _found_emojis(normalized.quote_text)
        self.assertEqual(len(found), 3)
        self.assertEqual(len(found), len(set(found)))
        self.assertFalse(_has_unapproved_emoji(normalized.quote_text, EMOJI_TOKENS))

    def test_parser_does_not_trust_model_quote_fragment_or_post_type(self):
        import json
        from app.gemini import _parse_experiment

        raw = json.dumps({
            "should_post": True,
            "content_type": "original_post",
            "post": "the release notes need a boss fight 😂 👀",
            "quote_fragment": "invented fake words",
            "format_name": "fresh format",
            "comedy_mechanism": "unexpected escalation",
            "structure_signature": "distinct signature",
        })
        draft = _parse_experiment(raw)
        self.assertEqual(draft.content_type, "quote_post")
        self.assertNotIn("invented fake words", draft.quote_text)

    def test_rejects_mentions_in_the_authored_comment(self):
        draft = quote("@OpenAI the release notes need a boss fight 😂 👀")
        with self.assertRaisesRegex(GeminiError, "@mention"):
            _validate_quote_against_source(draft, source(), 2, 3)

    def test_rejects_unapproved_emojis(self):
        draft = quote("the release notes need a boss fight 😎 👀")
        with self.assertRaisesRegex(GeminiError, "outside the approved palette"):
            _validate_quote_against_source(draft, source(), 2, 3)

    def test_rejects_repeated_emojis(self):
        draft = quote("the release notes need a boss fight 😂 😂")
        with self.assertRaisesRegex(GeminiError, "repeated an emoji"):
            _validate_quote_against_source(draft, source(), 2, 3)

    def test_rejects_hashtags_and_urls(self):
        for text in (
            "the release notes need a boss fight 😂 👀 #OpenAI",
            "the release notes need a boss fight 😂 👀 https://example.com",
        ):
            with self.subTest(text=text):
                with self.assertRaises(GeminiError):
                    _validate_quote_against_source(quote(text), source(), 2, 3)



    def test_rejects_any_quote_marks_in_final_comment(self):
        draft = quote('release notes need a boss fight 😂 👀 "not a quote"')
        with self.assertRaisesRegex(GeminiError, "quotation marks"):
            _validate_quote_against_source(draft, source(), 2, 3)

    def test_rejects_verbatim_source_wording_even_without_quote_marks(self):
        draft = quote("we shipped the new AI software feature 😂 👀")
        with self.assertRaisesRegex(GeminiError, "repeated wording from the source"):
            _validate_quote_against_source(draft, source(), 2, 3)

    def test_rejects_quote_and_explain_style(self):
        draft = quote("bro really said i want this to end like saying i want this to end 😂 👀")
        with self.assertRaisesRegex(GeminiError, "source|style|standalone|generic"):
            _validate_quote_against_source(draft, source(), 2, 3)

    def test_rejects_reused_format_name(self):
        draft = quote("the release notes need a boss fight 😂 👀")
        with self.assertRaisesRegex(GeminiError, "repeats a recent format name"):
            _validate_quote_against_source(
                draft,
                source(),
                2,
                3,
                [{"format_name": "fresh format", "post_text": "older joke"}],
            )

    def test_ai_tech_prompt_restricts_new_quote_lane_to_tech_and_ai(self):
        prompt = _prompt(source(), [], emoji_count=3, scope="ai_tech")
        self.assertIn("SCOPE: AI AND TECHNOLOGY ONLY", prompt)
        self.assertIn("artificial intelligence", prompt)
        self.assertNotIn("sports moment", prompt)
        self.assertIn("Never repeat an emoji token", prompt)
        self.assertIn("Do not add @mentions, hashtags, or URLs", prompt)
        self.assertIn("Do not put any phrase in quotation marks", prompt)
        self.assertNotIn("QUOTE OPTIONS", prompt)

    def test_rejects_invalid_quote_scope(self):
        from app.gemini import generate_quote
        with self.assertRaisesRegex(GeminiError, "Unsupported quote-post scope"):
            generate_quote(source(), [], scope="everything")

    def test_prompt_includes_prior_text_and_strict_no_tag_rules(self):
        prompt = _prompt(
            source(),
            [{
                "format_name": "past format",
                "comedy_mechanism": "a previous joke",
                "structure_signature": "one old structure",
                "post_text": "the old joke 😂 👀",
            }],
            emoji_count=3,
        )
        self.assertIn("prior_post=", prompt)
        self.assertIn("Do not add @mentions, hashtags, or URLs", prompt)
        self.assertIn("Do not just swap nouns or paraphrase an old joke", prompt)
        self.assertIn("Use at least 3 and at most 4 distinct emojis", prompt)


if __name__ == "__main__":
    unittest.main()
