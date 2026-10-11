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
    def test_daily_plan_is_four_company_quotes_eight_photo_prompts_eight_ai_tech_quotes(self):
        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, 4)
        self.assertEqual(post_queue.PERSON_PHOTO_POSTS_PER_DAY, 8)
        self.assertEqual(post_queue.AI_TECH_QUOTE_POSTS_PER_DAY, 8)
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

    async def test_official_company_lane_publishes_a_quote_post_with_original_commentary(self):
        from datetime import datetime, timezone

        state = post_queue._new_state()
        source = post_queue.SourceTweet(
            tweet_id="1111111111111111111",
            text="We shipped a new feature.",
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
            source_type="official_brand_post",
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
        with (
            patch.object(post_queue, "find_trending_source", new=AsyncMock(return_value=source)),
            patch.object(post_queue, "generate_quote", return_value=draft),
            patch.object(post_queue, "_save_state"),
        ):
            result = await post_queue._post_trend_quote(state, client)

        self.assertTrue(result)
        client.create_tweet.assert_awaited_once_with(
            text=draft.quote_text, attachment_url=source.url
        )
        client.retweet.assert_not_awaited()
        self.assertEqual(state["trend_quote_posts"][-1]["status"], "published")
        self.assertEqual(state["last_post_type"], "x_trends_quote")

    async def test_ai_tech_lane_publishes_a_quote_post_with_original_commentary(self):
        from datetime import datetime, timezone

        state = post_queue._new_state()
        source = post_queue.SourceTweet(
            tweet_id="2222222222222222222",
            text="A new AI model just shipped.",
            username="exampledev",
            created_at=datetime.now(timezone.utc),
            trend="AI/technology: OpenAI AI",
            trend_volume=0,
            view_count=1000,
            favorite_count=20,
            retweet_count=5,
            reply_count=4,
            author_followers=10000,
            author_verified=False,
            author_professional=True,
            source_type="ai_tech_search",
        )
        draft = QuoteDraft(
            True, "the launch notes need a training montage 😂 👀",
            "meme-style observation", "training montage", "absurd escalation",
            "", False, "quote_post", "invented_rule", "new tech structure",
        )
        client = SimpleNamespace(
            create_tweet=AsyncMock(return_value=SimpleNamespace(id="4444444444444444444")),
            retweet=AsyncMock(),
        )
        with (
            patch.object(post_queue, "find_trending_source", new=AsyncMock(return_value=source)) as find_source,
            patch.object(post_queue, "_ai_tech_topic_allowed", return_value=True),
            patch.object(post_queue, "generate_quote", return_value=draft),
            patch.object(post_queue, "_save_state"),
        ):
            result = await post_queue._post_ai_tech_quote(state, client)

        self.assertTrue(result)
        find_source.assert_awaited_once()
        self.assertIs(find_source.await_args.kwargs["ai_tech_only"], True)
        client.create_tweet.assert_awaited_once_with(
            text=draft.quote_text, attachment_url=source.url
        )
        client.retweet.assert_not_awaited()
        self.assertEqual(state["ai_tech_quote_posts"][-1]["status"], "published")
        self.assertEqual(state["last_post_type"], "ai_tech_quote")

    async def test_failed_lane_does_not_block_next_eligible_lane(self):
        state = post_queue._new_state()
        with (
            patch.dict(os.environ, {
                "CLOUDFLARE_ACCOUNT_ID": "test-account",
                "CLOUDFLARE_API_TOKEN": "test-token",
            }),
            patch.object(post_queue, "_load_state", return_value=state),
            patch.object(post_queue, "_save_state"),
            patch.object(post_queue, "_client", return_value=FakeClient()),
            patch.object(post_queue, "_interval_ok", return_value=True),
            patch.object(post_queue, "_post_trend_quote", new=AsyncMock(return_value=False)) as brand,
            patch.object(post_queue, "_post_person_photo", new=AsyncMock(return_value=True)) as photo,
            patch.object(post_queue, "_post_ai_tech_quote", new=AsyncMock(return_value=False)) as ai_tech,
        ):
            result = await post_queue.post_next("auth", "ct0")
        self.assertTrue(result)
        brand.assert_awaited_once()
        photo.assert_awaited_once()
        ai_tech.assert_not_awaited()
        self.assertEqual(state["next_lane_index"], 2)

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
