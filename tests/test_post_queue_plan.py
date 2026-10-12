import os
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from app import post_queue
from app.gemini import QuoteDraft


class FakeClient:
    async def is_logged_in(self):
        return True


class PostQueuePlanTests(unittest.IsolatedAsyncioTestCase):
    def test_daily_plan_is_twenty_ai_tech_x_trends_quote_posts_only(self):
        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, 20)
        self.assertEqual(post_queue.PERSON_PHOTO_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.AI_TECH_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(
            post_queue.TREND_QUOTE_POSTS_PER_DAY
            + post_queue.PERSON_PHOTO_POSTS_PER_DAY
            + post_queue.AI_TECH_QUOTE_POSTS_PER_DAY,
            post_queue.POSTS_PER_DAY,
        )

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
        self.assertIn("AI/technology X Trends quote posts", workflow)
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

    async def test_ai_technology_x_trends_lane_publishes_a_quote_post_with_original_commentary(self):
        from datetime import datetime, timezone

        state = post_queue._new_state()
        source = post_queue.SourceTweet(
            tweet_id="1111111111111111111",
            text="We shipped a new AI software feature.",
            username="OpenAI",
            created_at=datetime.now(timezone.utc),
            trend="OpenAI AI software",
            trend_volume=0,
            view_count=1000,
            favorite_count=20,
            retweet_count=5,
            reply_count=4,
            author_followers=100000,
            author_verified=True,
            author_professional=True,
            source_type="x_trend",
        )
        draft = QuoteDraft(
            True, "release day just spawned a final boss 😂 👀",
            "meme-style observation", "release boss fight", "absurd escalation",
            "", False, "quote_post", "invented_rule", "new structure signature",
        )
        client = SimpleNamespace(
            create_tweet=AsyncMock(return_value=SimpleNamespace(id="3333333333333333333")),
            retweet=AsyncMock(),
        )
        source_finder = AsyncMock(return_value=source)
        with (
            patch.object(post_queue, "find_trending_source", new=source_finder),
            patch.object(post_queue, "generate_quote", return_value=draft),
            patch.object(post_queue, "_save_state"),
        ):
            result = await post_queue._post_trend_quote(state, client)

        source_finder.assert_awaited_once_with(client, [], [], ai_tech_only=True)
        self.assertTrue(result)
        client.create_tweet.assert_awaited_once_with(
            text=draft.quote_text, attachment_url=source.url
        )
        client.retweet.assert_not_awaited()
        self.assertEqual(state["trend_quote_posts"][-1]["status"], "published")
        self.assertEqual(state["last_post_type"], "x_trends_quote")

    async def test_post_queue_disables_non_trend_lanes(self):
        self.assertEqual(post_queue.PERSON_PHOTO_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.AI_TECH_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, post_queue.POSTS_PER_DAY)

    async def test_queue_stops_when_twenty_main_posts_are_complete(self):
        state = post_queue._new_state()
        state["daily_count"] = post_queue.POSTS_PER_DAY
        with (
            patch.dict(os.environ, {
                "CLOUDFLARE_ACCOUNT_ID": "test-account",
                "CLOUDFLARE_API_TOKEN": "test-token",
            }),
            patch.object(post_queue, "_load_state", return_value=state),
            patch.object(post_queue, "_client") as client,
        ):
            result = await post_queue.post_next("auth", "ct0")
        self.assertFalse(result)
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
