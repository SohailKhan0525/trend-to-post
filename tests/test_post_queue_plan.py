import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from app import post_queue
from app.gemini import OriginalDraft, QuoteDraft


class FakeClient:
    async def is_logged_in(self):
        return True


class PostQueuePlanTests(unittest.IsolatedAsyncioTestCase):
    def test_daily_plan_is_ten_builder_tips_plus_ten_comparison_memes(self):
        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.BUILDER_TIP_POSTS_PER_DAY, 10)
        self.assertEqual(post_queue.PERSON_PHOTO_POSTS_PER_DAY, 10)
        self.assertEqual(post_queue.AI_TECH_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(
            post_queue.BUILDER_TIP_POSTS_PER_DAY
            + post_queue.PERSON_PHOTO_POSTS_PER_DAY,
            post_queue.POSTS_PER_DAY,
        )

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
        self.assertEqual(post_queue.BUILDER_TIP_POSTS_PER_DAY, 10)

    async def test_builder_tip_publishes(self):
        state = post_queue._new_state()
        draft = OriginalDraft(
            True,
            'DID YOU KNOW?\n\nTry adding this to your prompt:\n"Audit mobile, tablet, and desktop states plus loading, empty, error, and focus states before shipping."\n\nFollow for more builder prompts.',
            "expert builder prompt tip",
            "",
            "builder tip",
            "practical UI QA",
            "expert reusable prompt",
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

    async def test_post_queue_enables_original_developer_prompt_lane(self):
        self.assertEqual(post_queue.PERSON_PHOTO_POSTS_PER_DAY, 10)
        self.assertEqual(post_queue.BUILDER_TIP_POSTS_PER_DAY, 10)
        self.assertEqual(post_queue.AI_TECH_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.POSTS_PER_DAY, 20)

    async def test_queue_stops_when_twenty_main_posts_are_complete(self):
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
