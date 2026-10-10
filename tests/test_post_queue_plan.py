import os
import unittest
from unittest.mock import AsyncMock, patch

from app import post_queue


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
