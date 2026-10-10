import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.trend_source import find_ai_tech_source, find_official_brand_source, find_trending_source


class FakeXClient:
    def __init__(self, trend_results, tweet):
        self.trend_results = trend_results
        self.tweet = tweet
        self.searches = []

    async def get_trends(self, category, count=20, retry=True):
        return self.trend_results

    async def search_tweet(self, query, search_type, count=20):
        self.searches.append(query)
        return [self.tweet]


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


class FakeAIClient:
    def __init__(self, tweet):
        self.tweet = tweet
        self.searches = []
        self.trend_calls = 0

    async def get_trends(self, category, count=20, retry=True):
        self.trend_calls += 1
        raise AssertionError("The AI/tech quote lane must not read X Trends.")

    async def search_tweet(self, query, search_type, count=20):
        self.searches.append((query, search_type))
        return [self.tweet]


class FakeOfficialBrandClient:
    def __init__(self, tweet):
        self.tweet = tweet
        self.searches = []
        self.trend_calls = 0

    async def get_trends(self, category, count=20, retry=True):
        self.trend_calls += 1
        raise AssertionError("The official-brand quote lane must not read X Trends.")

    async def search_tweet(self, query, search_type, count=20):
        self.searches.append((query, search_type))
        handle = query.split(":", 1)[1]
        self.tweet.user.screen_name = handle
        self.tweet.text = "We shipped a new AI software feature today."
        self.tweet.view_count = 0  # official posts may not expose view counts yet
        return [self.tweet]



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


    async def test_ai_tech_source_uses_latest_keyword_searches_not_trends(self):
        tweet = make_tweet()
        client = FakeAIClient(tweet)

        source = await find_ai_tech_source(client, [])

        self.assertEqual(source.source_type, "ai_tech_search")
        self.assertTrue(source.url.startswith("https://x.com/"))
        self.assertTrue(client.searches)
        self.assertTrue(all(kind == "Latest" for _, kind in client.searches))
        self.assertEqual(client.trend_calls, 0)
        self.assertIn("NVIDIA", source.text)

    async def test_uses_real_official_brand_posts_without_querying_trends(self):
        tweet = make_tweet()
        client = FakeOfficialBrandClient(tweet)

        source = await find_official_brand_source(client, [])

        self.assertEqual(source.source_type, "official_brand_post")
        self.assertIn(source.username, {
            "OpenAI", "AnthropicAI", "Google", "Microsoft", "Apple", "NVIDIA",
            "AMD", "Intel", "Meta", "Sony", "NintendoAmerica", "Xbox",
            "PlayStation", "Steam", "EpicGames", "Adobe", "Canva", "Figma",
            "NotionHQ", "GitHub", "vercel", "Cloudflare", "SlackHQ", "Discord",
            "Spotify", "Netflix", "Nike", "adidas",
        })
        self.assertTrue(source.url.startswith("https://x.com/"))
        self.assertGreater(len(client.searches), 0)
        self.assertEqual(client.trend_calls, 0)
        self.assertTrue(all(kind == "Latest" for _, kind in client.searches))


if __name__ == "__main__":
    unittest.main()
