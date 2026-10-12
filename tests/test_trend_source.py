import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.trend_source import TrendSourceError, _ai_tech_topic_allowed, find_ai_tech_source, find_official_brand_source, find_trending_source


class FakeXClient:
    def __init__(self, trend_results, tweet, trends_by_category=None, trends_by_place=None, explore_trends=None):
        self.trend_results = trend_results
        self.trends_by_category = trends_by_category or {}
        self.trends_by_place = trends_by_place or {}
        self.explore_trends = explore_trends
        self.tweet = tweet
        self.searches = []
        self.trend_categories = []
        self.explore_calls = 0
        self.place_trend_calls = []

    async def get_explore_page(self):
        self.explore_calls += 1
        return self.explore_trends or []

    async def get_trends(self, category, count=20, retry=True):
        self.trend_categories.append(category)
        return self.trends_by_category.get(category, self.trend_results)

    async def get_place_trends(self, woeid):
        self.place_trend_calls.append(woeid)
        return SimpleNamespace(trends=self.trends_by_place.get(woeid, []))

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

    async def test_ai_tech_trend_lane_filters_topics_before_ranking(self):
        trends = [
            SimpleNamespace(name="NBA finals", tweets_count=500000),
            SimpleNamespace(name="NVIDIA RTX", tweets_count=12000),
        ]
        client = FakeXClient(trends, make_tweet())

        source = await find_trending_source(client, [], ai_tech_only=True)

        self.assertEqual(source.trend, "NVIDIA RTX")
        self.assertEqual(client.searches, ["NVIDIA RTX"])

    async def test_discovers_tech_topics_from_for_you_and_news_trends_tabs(self):
        client = FakeXClient(
            [SimpleNamespace(name="NBA finals", tweets_count=500000)],
            make_tweet(),
            trends_by_category={
                "trending": [SimpleNamespace(name="NBA finals", tweets_count=500000)],
                "for-you": [SimpleNamespace(name="Software development", tweets_count=22000)],
                "news": [SimpleNamespace(name="Election debate", tweets_count=40000)],
            },
        )

        tweet = make_tweet()
        tweet.text = "Software development has entered its side quest era"
        client.tweet = tweet

        source = await find_trending_source(client, [], ai_tech_only=True)

        self.assertEqual(source.trend, "Software development")
        self.assertEqual(client.trend_categories, ["trending", "for-you", "news"])
        self.assertEqual(client.searches, ["Software development"])

    async def test_discovers_tech_trend_from_modern_explore_page(self):
        tweet = make_tweet()
        tweet.text = "Microsoft Copilot added a new developer workflow"
        client = FakeXClient(
            [SimpleNamespace(name="celebrity gossip", tweets_count=30000)],
            tweet,
            explore_trends=[SimpleNamespace(name="Microsoft Copilot", tweets_count=42000)],
        )

        source = await find_trending_source(client, [], ai_tech_only=True)

        self.assertEqual(source.trend, "Microsoft Copilot")
        self.assertEqual(source.source_type, "x_trend")
        self.assertEqual(client.explore_calls, 1)
        self.assertIn("Microsoft Copilot", client.searches)

    async def test_uses_x_native_trend_domain_context_and_grouped_topics(self):
        tweet = make_tweet()
        tweet.text = "Microsoft Copilot added a new developer workflow today"
        contextual_trend = SimpleNamespace(
            name="New feature",
            tweets_count=12000,
            domain_context="Technology · Trending",
            grouped_trends=["Microsoft Copilot", "AI tools"],
        )
        client = FakeXClient(
            [SimpleNamespace(name="celebrity gossip", tweets_count=30000)],
            tweet,
            explore_trends=[contextual_trend],
        )

        source = await find_trending_source(client, [], ai_tech_only=True)

        self.assertEqual(source.trend, "Microsoft Copilot")
        self.assertEqual(source.source_type, "x_trend")
        self.assertIn("Microsoft Copilot", client.searches)
        self.assertNotIn("New feature", client.searches)

    async def test_finds_tech_trend_from_regional_x_trends_when_explore_has_none(self):
        tweet = make_tweet()
        tweet.text = "Cursor AI shipped a coding workflow update"
        regional_trend = SimpleNamespace(name="Cursor AI", tweet_volume=18000)
        client = FakeXClient(
            [SimpleNamespace(name="celebrity gossip", tweets_count=30000)],
            tweet,
            trends_by_place={1: [regional_trend]},
        )

        source = await find_trending_source(client, [], ai_tech_only=True)

        self.assertEqual(source.trend, "Cursor AI")
        self.assertEqual(source.source_type, "x_trend")
        self.assertIn(1, client.place_trend_calls)
        self.assertIn("Cursor AI", client.searches)

    async def test_context_only_generic_trend_requires_headline_relevance(self):
        trend = SimpleNamespace(
            name="New feature",
            tweets_count=12000,
            domain_context="Technology · Trending",
            grouped_trends=[],
        )
        tweet = make_tweet()
        tweet.text = "OpenAI shipped a new feature for developers today"
        client = FakeXClient([trend], tweet, explore_trends=[trend])

        source = await find_trending_source(client, [], ai_tech_only=True)

        self.assertEqual(source.trend, "New feature — Technology · Trending")
        self.assertIn("New feature", client.searches)

    async def test_context_only_generic_trend_rejects_unrelated_ai_post(self):
        trend = SimpleNamespace(
            name="New feature",
            tweets_count=12000,
            domain_context="Technology · Trending",
            grouped_trends=[],
        )
        tweet = make_tweet()
        tweet.text = "OpenAI discussed a different release unrelated to the headline"
        client = FakeXClient([trend], tweet, explore_trends=[trend])

        with self.assertRaisesRegex(TrendSourceError, "No eligible recent AI/technology source"):
            await find_trending_source(client, [], ai_tech_only=True)

    async def test_reads_place_trends_from_twifork_mapping_response(self):
        tweet = make_tweet()
        tweet.text = "Cursor AI shipped a coding workflow update"
        regional_trend = SimpleNamespace(name="Cursor AI", tweet_volume=18000)

        class MappingPlaceClient(FakeXClient):
            async def get_place_trends(self, woeid):
                self.place_trend_calls.append(woeid)
                return {"trends": [regional_trend]} if woeid == 1 else {"trends": []}

        client = MappingPlaceClient(
            [SimpleNamespace(name="celebrity gossip", tweets_count=30000)],
            tweet,
        )

        source = await find_trending_source(client, [], ai_tech_only=True)

        self.assertEqual(source.trend, "Cursor AI")
        self.assertIn(1, client.place_trend_calls)
        self.assertIn("Cursor AI", client.searches)

    async def test_rejects_non_tech_x_trends_without_generic_search_fallback(self):
        trend = SimpleNamespace(name="celebrity gossip", tweets_count=30000)
        client = FakeXClient([trend], make_tweet())

        with self.assertRaisesRegex(TrendSourceError, "no eligible topics"):
            await find_trending_source(client, [])

        self.assertEqual(client.searches, [])

    async def test_rejects_non_tech_source_post_even_under_tech_trend(self):
        trend = SimpleNamespace(name="AI agents", tweets_count=30000)
        tweet = make_tweet()
        tweet.text = "The more you grow, the more you need wisdom for family and relationships."
        client = FakeXClient([trend], tweet)

        with self.assertRaisesRegex(TrendSourceError, "No eligible recent AI/technology source"):
            await find_trending_source(client, [])

        self.assertEqual(client.searches, ["AI agents"])

    async def test_rejects_political_trends_without_generic_search_fallback(self):
        trends = [
            SimpleNamespace(name="election debate", tweets_count=50000),
            SimpleNamespace(name="parliament", tweets_count=30000),
        ]
        client = FakeXClient(trends, make_tweet())

        with self.assertRaisesRegex(TrendSourceError, "no eligible topics"):
            await find_trending_source(client, [])

        self.assertEqual(client.searches, [])

    async def test_accepts_trend_relevant_product_post_without_generic_ai_keyword(self):
        trend = SimpleNamespace(name="Cursor AI", tweets_count=22000)
        tweet = make_tweet()
        tweet.text = "Cursor just fixed the bug that ruined my whole afternoon."
        client = FakeXClient([trend], tweet)

        source = await find_trending_source(client, [], ai_tech_only=True)

        self.assertEqual(source.trend, "Cursor AI")
        self.assertIn("Cursor", source.text)

    async def test_checks_more_than_five_eligible_topics_before_giving_up(self):
        trends = [
            SimpleNamespace(name=name, tweets_count=10000)
            for name in (
                "OpenAI", "Claude AI", "Gemini AI", "NVIDIA", "GitHub",
                "Cursor", "RTX", "Kubernetes", "DeepSeek", "Software development",
            )
        ]
        tweet = make_tweet()
        tweet.text = "Cursor just fixed the bug that ruined my whole afternoon."
        client = FakeXClient(trends, tweet)

        source = await find_trending_source(client, [], ai_tech_only=True)

        self.assertEqual(source.trend, "Cursor")
        self.assertEqual(len(client.searches), 10)

    def test_ai_tech_topic_filter_requires_a_real_tech_signal(self):
        self.assertTrue(_ai_tech_topic_allowed("NVIDIA RTX drivers need a software update"))
        self.assertTrue(_ai_tech_topic_allowed("Our new AI model writes Python code"))
        self.assertTrue(_ai_tech_topic_allowed("Software development"))
        self.assertTrue(_ai_tech_topic_allowed("Software engineering trends"))
        self.assertFalse(_ai_tech_topic_allowed("What a brilliant football match"))
        self.assertFalse(_ai_tech_topic_allowed("Election debate about technology"))
        self.assertFalse(_ai_tech_topic_allowed("NVIDIA launch tied to Diwali festivities"))
        self.assertFalse(_ai_tech_topic_allowed("A software update for Christmas holidays"))

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
