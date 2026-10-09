import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.trend_source import find_trending_source


class FakeXClient:
    def __init__(self, trend_results, tweet):
        self.trend_results = trend_results
        self.tweet = tweet
        self.searches = []

    async def get_trends(self, category, count=20, retry=True):
        return self.trend_results

    async def search_tweet(self, query, search_type, count=20):
        self.searches.append(query)
        return [self.tweet] if query in {"NVIDIA RTX", "NVIDIA"} else []


def make_tweet():
    user = SimpleNamespace(
        screen_name="builder_account",
        followers_count=12000,
        verified=True,
        is_verified=True,
        description="engineer and developer",
    )
    return SimpleNamespace(
        id="123456789",
        text="NVIDIA RTX drivers have entered their side quest era",
        user=user,
        created_at_datetime=datetime.now(timezone.utc),
        in_reply_to=None,
        retweeted_tweet=None,
        is_quote_status=False,
        possibly_sensitive=False,
        lang="en",
        view_count=25000,
        favorite_count=200,
        retweet_count=70,
        reply_count=30,
    )


class TrendSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_allowed_real_x_trend_for_manual_source(self):
        trend = SimpleNamespace(name="NVIDIA RTX", tweets_count=12000)
        client = FakeXClient([trend], make_tweet())

        source = await find_trending_source(client, [])

        self.assertEqual(source.trend, "NVIDIA RTX")
        self.assertEqual(source.source_type, "x_trend")
        self.assertIn("NVIDIA RTX", client.searches)

    async def test_rejects_unrelated_or_political_trend_names(self):
        # Political trends are ignored; the finder then falls back to topic searches.
        trends = [
            SimpleNamespace(name="election debate", tweets_count=50000),
            SimpleNamespace(name="celebrity gossip", tweets_count=30000),
        ]
        client = FakeXClient(trends, make_tweet())

        source = await find_trending_source(client, [])

        self.assertEqual(source.source_type, "x_top_search")
        self.assertNotIn("election debate", client.searches)


if __name__ == "__main__":
    unittest.main()
