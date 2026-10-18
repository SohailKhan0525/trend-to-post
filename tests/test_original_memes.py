import unittest

from app.gemini import (
    GeminiError, OriginalDraft, QuoteDraft, _has_banned_debug_label,
    _original_text_prompt, _prompt, _validate_original, _validate_quote_against_source,
)


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
    def test_bans_fake_error_and_machine_log_openers(self) -> None:
        examples = (
            "index out of bounds: attempting to use a mind in the context of a calculator 💀🔥",
            "symptom_eligibility verification.exe: user requires higher api tier 😭🥀",
            "error_status: retry failed 💀🔥",
            "attempting to use a mind in the context of a calculator: 💀🔥",
            "Traceback: the developer has feelings 😭🔥",
            "Be honest...\nindex out of bounds: attempting to use a mind in a calculator 💀🔥",
        )
        for post in examples:
            with self.subTest(post=post):
                self.assertTrue(_has_banned_debug_label(post))
                with self.assertRaises(GeminiError):
                    _validate_original(draft(post), {}, include_handle=False)

    def test_developer_prompt_mode_has_natural_two_line_examples_and_optional_single_tag(self) -> None:
        prompt = _original_text_prompt(
            {"trend": "Tibo", "text": "Real photo of Tibo", "mention_handle": "@thsottiaux"},
            content_mode="developer_prompt",
        )
        self.assertIn("Be honest...", prompt)
        self.assertIn("exactly TWO text sections separated by exactly one blank line", prompt)
        self.assertIn("Grok Bot or ChatGPT Dots?", prompt)
        self.assertIn("no third line", prompt)
        self.assertIn("Grok Bot or ChatGPT Dots?", prompt)
        self.assertIn("Never include an @mention.", prompt)
        self.assertIn("no third line", prompt)
        self.assertIn("Never include an @mention", prompt)
        self.assertIn("This is a text-only comparison post", prompt)
        self.assertIn("Grok Bot or ChatGPT Dots?", prompt)
        self.assertIn("no other characters before or after", prompt)
        self.assertIn("two concrete choices that developers can meaningfully debate", prompt)
        self.assertIn("No fake diagnostics", prompt)

    def test_developer_prompt_enforces_exact_two_sections(self) -> None:
        valid = draft("Grok Bot or ChatGPT Dots?\n\nBe honest...")
        self.assertIs(
            _validate_original(
                valid,
                {},
                include_handle=False,
                minimum_emoji_count=0,
                maximum_emoji_count=1,
                require_blank_line_format=True,
            ),
            valid,
        )

    def test_developer_prompt_rejects_extra_text_quotes_and_bad_line_breaks(self) -> None:
        invalid_posts = (
            '"Grok Bot or ChatGPT Dots?\n\nBe honest..."',
            "Grok Bot or ChatGPT Dots?\nBe honest...",
            "Grok Bot or ChatGPT Dots?\n\nBe honest...\n\nWhich one?",
            "Grok Bot or ChatGPT Dots?\n\nRandom opener....",
            "Grok Bot or ChatGPT Dots?\n\nBe honest... 👀",
        )
        for post in invalid_posts:
            with self.subTest(post=post):
                with self.assertRaises(GeminiError):
                    _validate_original(
                        draft(post),
                        {},
                        include_handle=False,
                        minimum_emoji_count=0,
                        maximum_emoji_count=1,
                        require_blank_line_format=True,
                    )

    def test_developer_prompt_without_handle_forbids_mentions(self) -> None:
        prompt = _original_text_prompt(
            {"trend": "Sam Altman", "text": "licensed portrait"},
            content_mode="developer_prompt",
        )
        self.assertIn("Never include an @mention", prompt)

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
        self.assertIn("existing, real photo", prompt)
        self.assertIn("3 words with Tibo", prompt)
        self.assertIn("never over 16 words or 120 characters", prompt)
        self.assertIn("Do not claim they said, did, endorsed, or believe anything", prompt)
        self.assertIn("Never repeat an emoji token", prompt)

    def test_person_prompt_rejects_long_caption(self) -> None:
        long_caption = "This is an unnecessarily long photo caption with a lot of extra words that continues explaining the setup instead of getting to the funny question and keeps going even after the point is already clear to every reader 👀💀"
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



class BuilderPromptTipTests(unittest.TestCase):
    PROMPT_BODY = (
        "Build an original AI research workspace for analysts who collect sources and turn verified notes into briefs. "
        "Use restrained neutrals, one purposeful accent, an 8-point spacing scale, deliberate typography, semantic color "
        "tokens, consistent borders, and clear hierarchy. Create a navigation rail, searchable source list, synthesis "
        "canvas, and evidence panel with reusable UI components and realistic data. Every control must work. Define "
        "responsive behavior for desktop, tablet, and mobile; collapse navigation, stack panels, preserve reading order, "
        "and prevent horizontal overflow. Implement loading, empty, error, success, disabled, hover, pressed, and "
        "keyboard-focus states. Meet accessibility requirements with semantic landmarks, labeled controls, keyboard "
        "navigation, visible focus, and sufficient text contrast. Handle missing evidence and slow AI responses. Review "
        "screenshots at 375px, 768px, and 1440px; fix spacing, typography, contrast, alignment, overflow, and dead "
        "interactions. Acceptance criteria: no placeholder copy, default purple gradient, random glass cards, fake "
        "buttons, or unresolved console errors."
    )
    VALID_LONG_BUILDER_POST = (
        "DID YOU KNOW?\nThis prompt helps you build an AI research workspace with clearer evidence trails and accessible, "
        "responsive panels.\n\nREPLY PROMPT:\n"
        + PROMPT_BODY
        + "\n\nFOLLOW FOR MORE PROMPTS."
    )

    def test_builder_prompt_requires_long_reusable_premium_ai_ui_instruction(self) -> None:
        prompt = _original_text_prompt(
            {"trend": "AI workspaces, premium UI and responsive web applications", "text": "Build an original AI research tool."},
            content_mode="builder_tip",
            emoji_count=0,
        )
        self.assertIn("EDUCATIONAL BUILDER PROMPT WITH A REPLY", prompt)
        self.assertIn("600–1,400 characters", prompt)
        self.assertIn("REPLY PROMPT:", prompt)
        self.assertIn("visual-QA loop", prompt)
        self.assertIn("Treat Stripe and Vercel only as quality benchmarks", prompt)
        self.assertIn("No hashtags, URLs, @mentions, emojis", prompt)
        self.assertIn("Main post maximum 220 characters", prompt)

    def test_builder_tip_validation_accepts_substantive_long_prompt(self) -> None:
        value = draft(self.VALID_LONG_BUILDER_POST)
        self.assertGreaterEqual(len(value.post_text), 600)
        self.assertLessEqual(len(value.post_text), 1400)
        self.assertIs(
            _validate_original(
                value,
                {},
                include_handle=False,
                minimum_emoji_count=0,
                maximum_emoji_count=0,
                content_mode="builder_tip",
            ),
            value,
        )

    def test_builder_tip_validation_rejects_short_prompt(self) -> None:
        value = draft(
            "DID YOU KNOW?\nThis prompt helps you improve a website.\n\n"
            "REPLY PROMPT:\nMake the website better and responsive.\n\nFOLLOW FOR MORE PROMPTS."
        )
        with self.assertRaisesRegex(GeminiError, "600–1,400 characters"):
            _validate_original(
                value,
                {},
                include_handle=False,
                minimum_emoji_count=0,
                maximum_emoji_count=0,
                content_mode="builder_tip",
            )

    def test_builder_tip_validation_rejects_long_prompt_without_accessibility(self) -> None:
        low_quality = (
            "DID YOU KNOW?\nThis prompt helps create a polished interface.\n\nREPLY PROMPT:\n"
            + ("Create a beautiful responsive UI with components and layouts. " * 12)
            + "\n\nFOLLOW FOR MORE PROMPTS."
        )
        with self.assertRaisesRegex(GeminiError, "accessibility"):
            _validate_original(
                draft(low_quality),
                {},
                include_handle=False,
                minimum_emoji_count=0,
                maximum_emoji_count=0,
                content_mode="builder_tip",
            )

if __name__ == "__main__":
    unittest.main()


class BannedDebugLabelFormatTests(unittest.TestCase):
    BAD_POST = (
        "symptom_eligibility verification.exe: user requires higher api tier "
        "to process own trauma 💀🥀🔥❤️‍🩹"
    )

    def test_rejects_the_exact_banned_diagnostic_executable_opener(self) -> None:
        self.assertTrue(_has_banned_debug_label(self.BAD_POST))
        draft = QuoteDraft(
            True, self.BAD_POST, "test angle", "diagnostic label",
            "fake system-message format", "", False, "quote_post",
            "invented_rule", "diagnostic label followed by punchline",
        )
        with self.assertRaisesRegex(GeminiError, "banned fake diagnostic"):
            _validate_quote_against_source(
                draft,
                {"text": "A developer shipped a new AI coding feature today."},
                minimum_emoji_count=4,
                maximum_emoji_count=4,
            )

    def test_rejects_variants_of_fake_system_headers(self) -> None:
        for post in (
            "error_status: build is emotionally unavailable 💀🔥",
            "verification.exe: the deploy has entered witness protection 😭🥀",
            "diagnostic report: my GPU needs a vacation 😂🫠",
            "eligibility_check: access denied by the vibes 💀👀",
        ):
            with self.subTest(post=post):
                self.assertTrue(_has_banned_debug_label(post))

    def test_does_not_ban_normal_human_tech_jokes(self) -> None:
        for post in (
            "my GPU has the work ethic of a sleepy cat 💀🔥",
            "the build passed but the vibes failed 😭😂",
            "system design is just choosing which problem gets a chair 👀🥀",
        ):
            with self.subTest(post=post):
                self.assertFalse(_has_banned_debug_label(post))

    def test_quote_prompt_explicitly_bans_fake_diagnostic_formats(self) -> None:
        prompt = _prompt(
            {"trend": "AI tools", "text": "A developer shared an AI coding tool update."},
            scope="ai_tech",
        )
        self.assertIn("HARD-BANNED FORMAT — NEVER USE", prompt)
        self.assertIn("symptom_eligibility verification.exe:", prompt)

