import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from twikit.errors import InvalidSession, TooManyRequests
from app import diagnostics, queue


NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


def row(ident, hours=12):
    return {"tweet_id": str(ident), "posted_at": (NOW - timedelta(hours=hours)).isoformat(),
            "text": "A useful release detail.", "source_id": "900", "author": "toolmaker",
            "source_views": 200000}


def tweet(ident, **metrics):
    return SimpleNamespace(id=str(ident), **metrics)


class MetricsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {"history": [], "pending": {"some": "unconfirmed send"}}
        self.connection = SimpleNamespace(get_tweet_by_id=AsyncMock(), create_tweet=AsyncMock())
        for target in (patch.object(queue, "load", return_value=self.state),
                       patch.object(queue, "utcnow", return_value=NOW),
                       patch.object(queue, "client", return_value=self.connection),
                       patch.object(queue, "summary"),
                       patch.object(queue, "save_and_push"),
                       patch.object(diagnostics.writer, "complete")):
            target.start()
            self.addCleanup(target.stop)

    async def test_real_own_counts_and_missing_values_remain_distinct_from_source_views(self):
        self.state["history"] = [row("101"), row("102", 13)]
        before = copy.deepcopy(self.state)
        self.connection.get_tweet_by_id.side_effect = [
            tweet("101", view_count="7", favorite_count=1, reply_count=0,
                  retweet_count=0, quote_count=None),
            tweet("102", view_count=None, favorite_count=0, reply_count=2,
                  retweet_count=0, quote_count=0),
        ]
        report = await diagnostics.metrics()
        self.assertEqual(report["posts"][0]["metrics"]["views"], 7)
        self.assertEqual(report["posts"][0]["source_views_at_publication"], 200000)
        self.assertIsNone(report["posts"][1]["metrics"]["views"])
        self.assertEqual(report["summary"]["views"], {"available_posts": 1, "median": 7, "total": 7})
        self.assertEqual(report["summary"]["replies"]["median"], 1)
        self.assertEqual(self.state, before)
        queue.save_and_push.assert_not_called()
        diagnostics.writer.complete.assert_not_called()
        self.connection.create_tweet.assert_not_awaited()

    async def test_bounded_unique_recent_selection_excludes_young_future_and_invalid_posts(self):
        self.state["history"] = [row(100 + i, 7 + i) for i in range(20)]
        self.state["history"] += [row(100, 7), row(901, 1), row(902, -1),
                                  {"tweet_id": "903", "posted_at": "2026-11-10T00:00:00"},
                                  {"tweet_id": "904", "posted_at": "bad"}, row("invalid", 30)]
        self.connection.get_tweet_by_id.side_effect = lambda ident: tweet(ident)
        report = await diagnostics.metrics()
        self.assertEqual(self.connection.get_tweet_by_id.await_count, 12)
        self.assertEqual([post["tweet_id"] for post in report["posts"]],
                         [str(100 + i) for i in range(12)])

    async def test_lookup_failure_is_redacted_and_does_not_hide_next_post(self):
        self.state["history"] = [row(101, 8), row(102, 9)]
        self.connection.get_tweet_by_id.side_effect = [
            RuntimeError("secret request headers"), tweet(102, view_count=3),
        ]
        report = await diagnostics.metrics()
        self.assertEqual(report["errors"], [{"tweet_id": "101", "error": "RuntimeError"}])
        self.assertEqual(report["posts"][0]["tweet_id"], "102")
        self.assertNotIn("secret request headers", str(queue.summary.call_args_list))

    async def test_rate_limit_stops_without_retrying_or_reading_more_posts(self):
        self.state["history"] = [row(101, 8), row(102, 9)]
        self.connection.get_tweet_by_id.side_effect = TooManyRequests("do not log")
        report = await diagnostics.metrics()
        self.connection.get_tweet_by_id.assert_awaited_once_with("101")
        self.assertEqual(report["errors"], [{"tweet_id": "101", "error": "TooManyRequests"}])
        self.assertIn("stopped", report)
        self.assertIsNone(report["summary"]["views"]["total"])

    async def test_invalid_session_uses_existing_cli_session_error(self):
        self.state["history"] = [row(101), row(102)]
        self.connection.get_tweet_by_id.side_effect = InvalidSession("private detail")
        with self.assertRaises(InvalidSession):
            await diagnostics.metrics()
        self.assertEqual(self.connection.get_tweet_by_id.await_count, 1)

    async def test_unexpected_post_identity_is_not_attributed_to_our_post(self):
        self.state["history"] = [row(101)]
        self.connection.get_tweet_by_id.return_value = tweet(999, view_count=90000)
        report = await diagnostics.metrics()
        self.assertEqual(report["posts"], [])
        self.assertEqual(report["errors"][0]["error"], "ValueError")

    async def test_no_eligible_history_needs_no_session_or_requests(self):
        self.state["history"] = [row(101, 2)]
        report = await diagnostics.metrics()
        queue.client.assert_not_called()
        self.assertEqual(report["selected_posts"], 0)
        self.connection.get_tweet_by_id.assert_not_awaited()

    def test_count_parsing_never_converts_unknown_or_invalid_values_to_zero(self):
        for value in (None, True, -1, "1.5", "7K", "unknown", 4.2):
            with self.subTest(value=value):
                self.assertIsNone(diagnostics.public_count(tweet(101, view_count=value), "view_count"))

        class IncompleteTweet:
            @property
            def favorite_count(self):
                raise KeyError("favorite_count")

        self.assertIsNone(diagnostics.public_count(IncompleteTweet(), "favorite_count"))
        self.assertEqual(diagnostics.public_count(tweet(101, view_count="0"), "view_count"), 0)


if __name__ == "__main__":
    unittest.main()
