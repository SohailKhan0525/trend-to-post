import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from app import post_queue
from app.gemini import OriginalDraft, QuoteDraft, _validate_original


class FakeClient:
    async def is_logged_in(self):
        return True


def _long_builder_post():
    return (
        "Design an AI research workspace that feels intentionally art-directed.\n\n"
            "PASTE THIS INTO YOUR AI BUILDER:\n"
            "Build an original AI research workspace for analysts who collect sources, compare findings, "
            "and turn verified notes into clear briefs. Establish a distinctive visual direction: restrained "
            "neutrals, one purposeful accent, an 8-point spacing scale, deliberate typography, semantic color "
            "tokens, consistent borders, and subtle elevation. Compose a navigation rail, searchable source list, "
            "synthesis canvas, and evidence panel with clear hierarchy. Implement reusable UI components and "
            "realistic example data; every control must work. Define responsive behavior for desktop, tablet, "
            "and mobile: collapse navigation, stack panels, preserve reading order, and prevent horizontal "
            "overflow. Implement loading, empty, error, success, disabled, hover, pressed, and keyboard-focus "
            "states with recovery actions. Meet accessibility requirements with semantic landmarks, labeled "
            "controls, keyboard navigation, visible focus, and sufficient text contrast. Handle long titles, "
            "missing evidence, and slow AI responses gracefully. Review screenshots at 375px, 768px, and 1440px; "
            "fix spacing, typography, contrast, alignment, overflow, and dead interactions. Acceptance criteria: "
            "no placeholder copy, default purple gradient, random glass cards, fake buttons, or unresolved console errors."
    )
class PostQueuePlanTests(unittest.IsolatedAsyncioTestCase):
    def test_daily_plan_has_22_builder_prompts_22_comparison_memes_and_45_hard_cap(self):
        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.BUILDER_TIP_POSTS_PER_DAY, 22)
        self.assertEqual(post_queue.PERSON_PHOTO_POSTS_PER_DAY, 22)
        self.assertEqual(post_queue.AI_TECH_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(
            post_queue.BUILDER_TIP_POSTS_PER_DAY
            + post_queue.PERSON_PHOTO_POSTS_PER_DAY,
            post_queue.POSTS_PER_DAY - 1,
        )
        self.assertEqual(post_queue.POSTS_PER_DAY, 45)

    def test_mention_posts_are_limited_to_two_in_rolling_48_hours(self):
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        state = {"mention_post_timestamps": [
            {"timestamp": (now - timedelta(hours=49)).isoformat(), "handle": "@old"},
            {"timestamp": (now - timedelta(hours=24)).isoformat(), "handle": "@one"},
            {"timestamp": (now - timedelta(hours=1)).isoformat(), "handle": "@two"},
        ]}
        active = post_queue._active_mention_posts(state, now)
        self.assertEqual([item["handle"] for item in active], ["@one", "@two"])
        self.assertEqual(len(active), 2)

    def test_builder_prompt_validation_accepts_long_accessible_ai_ui_prompt(self):
        previous = {
            "format_name": "premium builder prompt",
            "structure_signature": "hook_prompt_marker_long_implementation",
            "post_text": "A previously used brief with a different UI scenario.",
            "content_type": "builder_tip",
        }
        draft = OriginalDraft(
            True,
            _long_builder_post(),
            "premium builder prompt",
            "",
            "builder prompt",
            "AI research workspace",
            "responsive accessibility visual QA",
            "hook_prompt_marker_long_implementation",
            "builder_tip",
        )
        validated = _validate_original(
            draft,
            {},
            include_handle=False,
            minimum_emoji_count=0,
            maximum_emoji_count=0,
            format_memory=[previous],
            content_mode="builder_tip",
        )
        self.assertIs(validated, draft)


    def test_content_deduplication_catches_exact_and_near_duplicates(self):
        original = (
            "Build an AI dashboard with a responsive component layout, accessible keyboard focus, "
            "loading and error states, a clear typography hierarchy, and screenshot-based visual QA."
        )
        fingerprint = post_queue._content_fingerprint(original)
        self.assertTrue(post_queue._is_duplicate_content(original, [fingerprint], []))
        self.assertEqual(
            post_queue._comparison_pair_fingerprint("Grok Bot or ChatGPT Dots?"),
            post_queue._comparison_pair_fingerprint("ChatGPT Dots vs Grok Bot?"),
        )
        self.assertTrue(
            post_queue._is_duplicate_content(
                original,
                [],
                [original.replace("Build an AI dashboard", "Build an AI workspace")],
            )
        )
        self.assertFalse(
            post_queue._is_duplicate_content(
                "Design a music discovery app with playlist controls and audio waveform previews.",
                [],
                [original],
            )
        )
    def test_generation_budget_allows_retries_beyond_daily_post_cap(self):
        self.assertGreaterEqual(post_queue.AI_GENERATIONS_PER_DAY, post_queue.POSTS_PER_DAY + 30)

    def test_post_interval_allows_twenty_hourly_slots(self):
        self.assertLessEqual(post_queue.MIN_POST_INTERVAL_MINUTES, 60)
        self.assertGreater(post_queue.MIN_POST_INTERVAL_MINUTES, 0)

    def test_workflow_has_a_scheduled_fallback(self):
        from pathlib import Path

        workflow = Path(".github/workflows/post-queue.yml").read_text(encoding="utf-8")
        self.assertIn('schedule:', workflow)
        self.assertIn('cron: "*/30 * * * *"', workflow)
        self.assertIn('Report daily queue progress', workflow)
        self.assertIn('Fail visibly when no post was published', workflow)
        self.assertIn("steps.post_bot.outputs.skip_reason != 'interval_guard'", workflow)
        self.assertIn("steps.post_bot.outputs.skip_reason != 'no_eligible_source'", workflow)
        self.assertIn("POST_SKIP_REASON", workflow)
        self.assertIn("Builder prompt tips", workflow)
        self.assertIn("Comparison memes", workflow)
        self.assertIn("X Trends quote posts (retired)", workflow)
        self.assertIn('test -n "$CLOUDFLARE_ACCOUNT_ID"', workflow)
        self.assertIn('test -n "$CLOUDFLARE_API_TOKEN"', workflow)
        self.assertIn('test -n "$GEMINI_API_KEY"', workflow)
        self.assertNotIn("Publish one queued photo prompt", workflow)

    async def test_person_photo_lane_only_uses_a_matching_active_x_profile(self):
        person = ("Thibault Sottiaux", "Tibo", "Codex / AI engineering", "thsottiaux", ("Tibo",))

        class PersonSearchClient:
            async def search_tweet(self, query, search_type, count=5):
                self.query = query
                user = SimpleNamespace(screen_name="thsottiaux", name="Tibo")
                return [
                    SimpleNamespace(
                        user=user,
                        in_reply_to=None,
                        retweeted_tweet=None,
                        text="Codex is shipping.",
                    )
                ]

        with patch.object(post_queue, "PERSON_SUBJECTS", (person,)):
            result = await post_queue._find_active_x_person_handles(PersonSearchClient(), [])

        self.assertEqual(result, ["thsottiaux"])

    async def test_person_photo_lane_rejects_handle_with_wrong_profile_name(self):
        person = ("Thibault Sottiaux", "Tibo", "Codex / AI engineering", "thsottiaux", ("Tibo",))

        class WrongProfileClient:
            async def search_tweet(self, query, search_type, count=5):
                user = SimpleNamespace(screen_name="thsottiaux", name="Someone Else")
                return [
                    SimpleNamespace(
                        user=user,
                        in_reply_to=None,
                        retweeted_tweet=None,
                        text="Unrelated account content.",
                    )
                ]

        with patch.object(post_queue, "PERSON_SUBJECTS", (person,)):
            result = await post_queue._find_active_x_person_handles(WrongProfileClient(), [])

        self.assertEqual(result, [])

    async def test_x_trends_quote_lane_is_retired(self):
        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.BUILDER_TIP_POSTS_PER_DAY, 22)

    async def test_builder_tip_publishes(self):
        state = post_queue._new_state()
        draft = OriginalDraft(
            True,
            _long_builder_post(),
            "premium builder prompt",
            "",
            "builder prompt",
            "AI research workspace",
            "responsive accessibility visual QA",
            "hook_prompt_marker_long_implementation",
            "builder_tip",
        )
        client = SimpleNamespace(
            create_tweet=AsyncMock(return_value=SimpleNamespace(id="4444444444444444444"))
        )
        with (
            patch.object(post_queue, "generate_original_text", return_value=draft),
            patch.object(post_queue, "_save_state"),
        ):
            result = await post_queue._post_builder_tip(state, client)

        self.assertTrue(result)
        client.create_tweet.assert_awaited_once_with(text=draft.post_text)
        self.assertEqual(state["builder_tip_post_count"], 1)
        self.assertEqual(state["daily_count"], 1)
        self.assertEqual(state["last_post_type"], "builder_tip")
        self.assertEqual(state["builder_tip_posts"][-1]["status"], "published_builder_tip")

    async def test_builder_long_post_falls_back_to_thread_when_x_rejects_length(self):
        state = post_queue._new_state()
        draft = OriginalDraft(
            True,
            _long_builder_post(),
            "premium builder prompt",
            "",
            "builder prompt",
            "AI research workspace",
            "responsive accessibility visual QA",
            "hook_prompt_marker_long_implementation",
            "builder_tip",
        )
        client = SimpleNamespace(
            create_tweet=AsyncMock(side_effect=[
                Exception("Authorization: Tweet needs to be a bit shorter. (186)"),
                SimpleNamespace(id="thread-root"),
                SimpleNamespace(id="thread-reply-1"),
                SimpleNamespace(id="thread-reply-2"),
                SimpleNamespace(id="thread-reply-3"),
                SimpleNamespace(id="thread-reply-4"),
                SimpleNamespace(id="thread-reply-5"),
                SimpleNamespace(id="thread-reply-6"),
            ])
        )
        with (
            patch.object(post_queue, "generate_original_text", return_value=draft),
            patch.object(post_queue, "_save_state"),
        ):
            result = await post_queue._post_builder_tip(state, client)

        self.assertTrue(result)
        self.assertGreater(client.create_tweet.await_count, 1)
        chunks = [call.kwargs["text"] for call in client.create_tweet.await_args_list[1:]]
        self.assertTrue(all(len(chunk) <= 280 for chunk in chunks))
        self.assertTrue(all("reply_to" in call.kwargs for call in client.create_tweet.await_args_list[2:]))
        self.assertEqual(state["builder_tip_posts"][-1]["thread_tweet_ids"][0], "thread-root")

    async def test_post_queue_enables_original_comparison_and_builder_lanes(self):
        self.assertEqual(post_queue.PERSON_PHOTO_POSTS_PER_DAY, 22)
        self.assertEqual(post_queue.BUILDER_TIP_POSTS_PER_DAY, 22)
        self.assertEqual(post_queue.AI_TECH_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.POSTS_PER_DAY, 45)

    async def test_queue_stops_when_forty_five_main_posts_are_complete(self):
        state = post_queue._new_state()
        state["daily_count"] = post_queue.POSTS_PER_DAY
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(post_queue, "_load_state", return_value=state),
            patch.object(post_queue, "_client") as client,
        ):
            result = await post_queue.post_next("auth", "ct0")
        self.assertFalse(result)
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
