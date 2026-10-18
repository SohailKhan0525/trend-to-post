import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from pathlib import Path

from app import community_posts


class CommunityPostTests(unittest.IsolatedAsyncioTestCase):
    def test_fixed_posts_are_exact_and_non_ai(self):
        self.assertEqual(
            community_posts.COMMUNITY_POSTS["connect"],
            "Let's connect if you are building in tech, ai, vibe coding 👋🤝",
        )
        self.assertEqual(
            community_posts.COMMUNITY_POSTS["building"],
            "What are you building today 👀.\nShare it with a link 👇",
        )

    async def test_community_post_publishes_static_text_and_records_idempotency(self):
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "community_posts.json"
            client = SimpleNamespace(
                is_logged_in=AsyncMock(return_value=True),
                create_tweet=AsyncMock(return_value=SimpleNamespace(id="community-123")),
            )
            with (
                patch.object(community_posts, "COMMUNITY_STATE_PATH", state_path),
                patch.object(community_posts, "_client", return_value=client),
            ):
                published = await community_posts.post_community_post("auth", "ct0", "connect")
                self.assertTrue(published)
                client.create_tweet.assert_awaited_once_with(
                    text="Let's connect if you are building in tech, ai, vibe coding 👋🤝"
                )
                saved = community_posts._load_community_state()
                self.assertEqual(saved["posted"]["connect"]["tweet_id"], "community-123")
                duplicate = await community_posts.post_community_post("auth", "ct0", "connect")
                self.assertFalse(duplicate)
                client.create_tweet.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
