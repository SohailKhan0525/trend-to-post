import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from twikit.errors import InvalidSession, TooManyRequests
from app import metrics, store


NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


def row(ident, hours=12, **extra):
    return {"tweet_id": str(ident), "posted_at": (NOW - timedelta(hours=hours)).isoformat(),
            "text": "A useful release detail.", "source_id": "900", "author": "toolmaker",
            "source_views": 200000, **extra}


def tweet(ident, **counts):
    return SimpleNamespace(id=str(ident), **counts)


class MetricsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {"history": [], "pending": {"some": "unconfirmed send"}}
        self.connection = SimpleNamespace(get_tweet_by_id=AsyncMock(), create_tweet=AsyncMock())
        for target in (patch.object(store, "load", return_value=self.state),
                       patch.object(store, "utcnow", return_value=NOW),
                       patch.object(store, "client", return_value=self.connection),
                       patch.object(store, "summary")):
            target.start()
            self.addCleanup(target.stop)

    async def test_own_counts_and_unknown_values_are_distinct_from_source_views(self):
        self.state["history"] = [row("101"), row("102", 13)]
        before = copy.deepcopy(self.state)
        self.connection.get_tweet_by_id.side_effect = [
            tweet("101", view_count="7", favorite_count=1, reply_count=0,
                  retweet_count=0, quote_count=None),
            tweet("102", view_count=None, favorite_count=0, reply_count=2,
                  retweet_count=0, quote_count=0),
        ]
        report = await metrics.metrics()
        self.assertEqual(report["posts"][0]["metrics"]["views"], 7)
        self.assertEqual(report["posts"][0]["source_views_at_publication"], 200000)
        self.assertIsNone(report["posts"][1]["metrics"]["views"])
        self.assertEqual(report["summary"]["views"], {"available_posts": 1, "median": 7, "total": 7})
        self.assertEqual(report["summary"]["replies"]["median"], 1)
        self.assertEqual(self.state, before)
        self.connection.create_tweet.assert_not_awaited()

    async def test_published_follow_posts_and_legacy_posts_count_but_drafts_do_not(self):
        follow = row("103", kind="follow", status="confirmed")
        for field in ("source_id", "source_views", "author"):
            follow.pop(field)
        self.state["history"] = [row("101"), follow,
                                 row("102", kind="draft", status="published"),
                                 row("104", kind="reply_draft"),
                                 row("105", published=False)]
        self.state["drafts"] = [row("106")]
        self.state["pending"] = row("107", kind="follow")
        before = copy.deepcopy(self.state)
        self.connection.get_tweet_by_id.side_effect = lambda ident: tweet(ident, view_count=4)
        report = await metrics.metrics()
        self.assertEqual(report["selected_posts"], 2)
        self.assertEqual({post["tweet_id"] for post in report["posts"]}, {"101", "103"})
        self.assertEqual({post["kind"] for post in report["posts"]}, {"quote", "follow"})
        self.assertEqual(report["summary"]["views"]["total"], 8)
        own = next(post for post in report["posts"] if post["tweet_id"] == "103")
        self.assertIsNone(own["source_views_at_publication"])
        self.assertEqual(self.state, before)

    async def test_unconfirmed_statuses_never_count_as_publications(self):
        self.state["history"] = [row(100 + index, status=status) for index, status in enumerate(
            ("draft", "planned", "pending", "unconfirmed", "expired_unsent", "rejected", [], False))]
        report = await metrics.metrics()
        self.assertEqual(report["selected_posts"], 0)
        store.client.assert_not_called()

    async def test_published_reply_uses_nested_source_metadata_not_source_metrics(self):
        reply = row("110", kind="reply", status="confirmed",
                    source={"id": "910", "author": "original_author", "views": 300000})
        for field in ("source_id", "source_views", "author"):
            reply.pop(field)
        self.state["history"] = [reply]
        self.connection.get_tweet_by_id.return_value = tweet(
            "110", view_count="25", favorite_count=2, reply_count=1)
        before = copy.deepcopy(self.state)
        report = await metrics.metrics()
        post = report["posts"][0]
        self.assertEqual(post["kind"], "reply")
        self.assertEqual(post["source_id"], "910")
        self.assertEqual(post["source_author"], "original_author")
        self.assertEqual(post["source_views_at_publication"], 300000)
        self.assertEqual(post["metrics"]["views"], 25)
        self.assertEqual(report["summary"]["views"]["total"], 25)
        self.assertEqual(report["summary"]["other_replies_estimate"]["total"], 1)
        self.assertEqual(self.state, before)
        self.connection.get_tweet_by_id.assert_awaited_once_with("110")

    async def test_legacy_source_metadata_remains_preferred_when_both_shapes_exist(self):
        self.state["history"] = [row("110", source={"id": "910", "author": "new", "views": 10})]
        self.connection.get_tweet_by_id.return_value = tweet("110", view_count=3)
        report = await metrics.metrics()
        post = report["posts"][0]
        self.assertEqual(post["source_id"], "900")
        self.assertEqual(post["source_author"], "toolmaker")
        self.assertEqual(post["source_views_at_publication"], 200000)

    async def test_bounded_unique_recent_selection_excludes_young_future_and_invalid_posts(self):
        self.state["history"] = [row(100 + i, 7 + i) for i in range(20)]
        self.state["history"] += [row(100, 7), row(901, 1), row(902, -1),
                                  {"tweet_id": "903", "posted_at": "2026-11-10T00:00:00"},
                                  {"tweet_id": "904", "posted_at": "bad"}, row("invalid", 30)]
        self.connection.get_tweet_by_id.side_effect = lambda ident: tweet(ident)
        report = await metrics.metrics()
        self.assertEqual(self.connection.get_tweet_by_id.await_count, 12)
        self.assertEqual([post["tweet_id"] for post in report["posts"]],
                         [str(100 + i) for i in range(12)])

    async def test_six_hour_boundary_and_timezone_offset_are_eligible(self):
        self.state["history"] = [row("101", 6), row("102", 6 - 1 / 3600),
                                 row("103", posted_at="2026-11-13T10:00:00+05:00")]
        self.connection.get_tweet_by_id.side_effect = lambda ident: tweet(ident)
        report = await metrics.metrics()
        self.assertEqual([post["tweet_id"] for post in report["posts"]], ["101", "103"])

    async def test_known_self_reply_is_not_reported_as_audience_response(self):
        self.state["history"] = [row("101", reply_id="201", reply_status="verified"), row("102", 13)]
        self.connection.get_tweet_by_id.side_effect = [tweet("101", reply_count=1), tweet("102", reply_count=None)]
        report = await metrics.metrics()
        self.assertEqual(report["summary"]["replies"]["total"], 1)
        self.assertEqual(report["summary"]["known_self_replies"], 1)
        self.assertEqual(report["summary"]["other_replies_estimate"]["total"], 0)
        self.assertIsNone(report["posts"][1]["other_replies_estimate"])

    async def test_unknown_self_reply_outcome_does_not_appear_as_audience_engagement(self):
        self.state["history"] = [row("101", reply_status="unconfirmed")]
        self.connection.get_tweet_by_id.return_value = tweet("101", reply_count=1)
        report = await metrics.metrics()
        self.assertTrue(report["posts"][0]["self_reply_uncertain"])
        self.assertIsNone(report["summary"]["other_replies_estimate"]["total"])

    async def test_invalid_self_reply_id_does_not_remove_a_real_audience_reply(self):
        self.state["history"] = [row("101", reply_id=True)]
        self.connection.get_tweet_by_id.return_value = tweet("101", reply_count=1)
        report = await metrics.metrics()
        self.assertEqual(report["summary"]["known_self_replies"], 0)
        self.assertEqual(report["summary"]["other_replies_estimate"]["total"], 1)

    async def test_lookup_failure_is_redacted_and_does_not_hide_next_post(self):
        self.state["history"] = [row(101, 8), row(102, 9)]
        self.connection.get_tweet_by_id.side_effect = [
            RuntimeError("secret request headers"), tweet(102, view_count=3),
        ]
        report = await metrics.metrics()
        self.assertEqual(report["errors"], [{"tweet_id": "101", "error": "RuntimeError"}])
        self.assertEqual(report["posts"][0]["tweet_id"], "102")
        self.assertNotIn("secret request headers", str(store.summary.call_args_list))

    async def test_rate_limit_stops_without_retrying_or_reading_more_posts(self):
        self.state["history"] = [row(101, 8), row(102, 9)]
        self.connection.get_tweet_by_id.side_effect = TooManyRequests("do not log")
        report = await metrics.metrics()
        self.connection.get_tweet_by_id.assert_awaited_once_with("101")
        self.assertEqual(report["errors"], [{"tweet_id": "101", "error": "TooManyRequests"}])
        self.assertIn("stopped", report)
        self.assertIsNone(report["summary"]["views"]["total"])

    async def test_invalid_session_propagates_for_cli_handling(self):
        self.state["history"] = [row(101), row(102)]
        self.connection.get_tweet_by_id.side_effect = InvalidSession("private detail")
        with self.assertRaises(InvalidSession):
            await metrics.metrics()
        self.assertEqual(self.connection.get_tweet_by_id.await_count, 1)

    async def test_unavailable_session_propagates_without_reading(self):
        self.state["history"] = [row(101)]
        store.client.side_effect = store.SessionUnavailable()
        with self.assertRaises(store.SessionUnavailable):
            await metrics.metrics()
        self.connection.get_tweet_by_id.assert_not_awaited()

    async def test_unexpected_identity_is_not_attributed_to_our_post(self):
        self.state["history"] = [row(101)]
        self.connection.get_tweet_by_id.return_value = tweet(999, view_count=90000)
        report = await metrics.metrics()
        self.assertEqual(report["posts"], [])
        self.assertEqual(report["errors"][0]["error"], "ValueError")

    async def test_no_eligible_history_needs_no_session_or_requests(self):
        self.state["history"] = [row(101, 2)]
        report = await metrics.metrics()
        store.client.assert_not_called()
        self.assertEqual(report["selected_posts"], 0)
        self.connection.get_tweet_by_id.assert_not_awaited()

    def test_invalid_identifiers_are_not_publications(self):
        for value in (True, False, None, -1, 0, "0", "001a", "１２３", 4.2, []):
            with self.subTest(value=value):
                self.assertIsNone(metrics.post_id(value))

    def test_unknown_or_invalid_counts_never_become_zero(self):
        for value in (None, True, -1, "1.5", "7K", "unknown", "１２３", 4.2):
            with self.subTest(value=value):
                self.assertIsNone(metrics.public_count(tweet(101, view_count=value), "view_count"))

        class IncompleteTweet:
            @property
            def favorite_count(self):
                raise KeyError("favorite_count")

        self.assertIsNone(metrics.public_count(IncompleteTweet(), "favorite_count"))
        self.assertEqual(metrics.public_count(tweet(101, view_count="0"), "view_count"), 0)


if __name__ == "__main__":
    unittest.main()
