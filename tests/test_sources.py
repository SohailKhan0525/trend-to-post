import copy
from datetime import datetime, timedelta, timezone
import io
from contextlib import redirect_stdout
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from twikit.tweet import Tweet

from app.sources import (
    ANNOUNCEMENT_TERMS, MAX_AGE, SEARCH_COUNT, SourceError,
    canonical_url, discover, search_plan, snapshot, validate,
)


NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
TEXT = "We released a faster coding model with a larger context window today."


def tweet(source_id="1001", *, views="100000", age=timedelta(hours=1),
          text=TEXT, author="OpenAI", urls=None, aliases=None, **legacy_fields):
    """Actual pinned-client properties backed by a small GraphQL fixture."""
    data = {
        "rest_id": source_id,
        "legacy": {
            "full_text": text,
            "created_at": (NOW - age).strftime("%a %b %d %H:%M:%S %z %Y"),
            "is_quote_status": False,
            "entities": {"urls": urls or []},
            **legacy_fields,
        },
        "views": {"count": views, "state": "EnabledWithCount"},
    }
    if aliases is not None:
        data["edit_control"] = {"edit_tweet_ids": aliases}
    user = SimpleNamespace(id="2001", screen_name=author, protected=False,
                           verified=False, is_blue_verified=False)
    return Tweet(None, data, user)


class SnapshotTests(unittest.TestCase):
    def test_exact_threshold_numeric_string_without_badge_is_eligible(self):
        source = snapshot(tweet(), NOW)
        self.assertEqual(source["views"], 100000)
        self.assertEqual(source["url"], "https://x.com/OpenAI/status/1001")
        self.assertEqual(source["created_at"], "2026-11-05T11:00:00+00:00")
        self.assertEqual(source["source_ids"], ["1001"])

    def test_missing_below_threshold_and_inferred_view_counts_fail(self):
        for value in (None, 99999, True, False, -100000, 100000.0, "100K", "100,000", "1e6", "-100000", " 100000"):
            with self.subTest(value=value), self.assertRaises(SourceError):
                snapshot(tweet(views=value), NOW)

    def test_freshness_boundaries_and_future_sources(self):
        for age in (timedelta(0), MAX_AGE):
            self.assertEqual(snapshot(tweet(age=age), NOW)["views"], 100000)
        for age in (MAX_AGE + timedelta(seconds=1), timedelta(seconds=-1)):
            with self.subTest(age=age), self.assertRaises(SourceError):
                snapshot(tweet(age=age), NOW)

    def test_any_public_author_is_eligible_without_a_badge_or_follower_requirement(self):
        for author in ("openai", "indie_builder", "car_notes", "coder42"):
            with self.subTest(author=author):
                item=tweet(author=author)
                item.user.followers_count=0
                source=snapshot(item,NOW)
                self.assertEqual(source["author"],author)
                self.assertEqual(source["url"],f"https://x.com/{author}/status/1001")

    def test_author_identity_must_still_be_a_valid_handle(self):
        for author in (None,"","bad handle","x/status/9","a"*16):
            with self.subTest(author=author),self.assertRaises(SourceError):
                snapshot(tweet(author=author),NOW)

    def test_popular_author_or_badge_does_not_substitute_for_source_views(self):
        for views in (None,"99999"):
            item=tweet(author="indie_builder",views=views)
            item.user.followers_count=10_000_000
            item.user.verified=item.user.is_blue_verified=True
            with self.subTest(views=views),self.assertRaises(SourceError):
                snapshot(item,NOW)

    def test_protected_or_missing_author_fails(self):
        source = tweet()
        source.user.protected = True
        with self.assertRaises(SourceError):
            snapshot(source, NOW)
        source.user = None
        with self.assertRaises(SourceError):
            snapshot(source, NOW)

    def test_reply_quote_and_repost_fail_even_when_nested_object_is_missing(self):
        for fields in (
            {"in_reply_to_status_id_str": "9"},
            {"in_reply_to_user_id_str": "9"},
            {"is_quote_status": True},
            {"quoted_status_id_str": "9"},
            {"retweeted_status_result": {"result": {"__typename": "TweetTombstone"}}},
        ):
            with self.subTest(fields=fields), self.assertRaises(SourceError):
                snapshot(tweet(**fields), NOW)
        quote = tweet()
        quote._data["quoted_status_result"] = {"result": {"__typename": "TweetTombstone"}}
        with self.assertRaises(SourceError):
            snapshot(quote, NOW)

    def test_missing_media_only_and_truncated_text_fail(self):
        for text in (None, "", "   ", "https://t.co/abc", "Watch this! https://t.co/abc", TEXT + "...", TEXT + "…"):
            with self.subTest(text=text), self.assertRaises(SourceError):
                snapshot(tweet(text=text), NOW)
        with self.assertRaises(SourceError):
            snapshot(tweet(truncated=True), NOW)

    def test_complete_note_text_replaces_a_truncated_legacy_preview(self):
        source = tweet(text="We released a faster coding…", truncated=True)
        source._data["note_tweet"] = {"note_tweet_results": {"result": {"text": TEXT}}}
        self.assertEqual(snapshot(source, NOW)["text"], TEXT)

    def test_entire_text_is_checked_not_just_the_beginning(self):
        for suffix in (" We opened a new office in Canada.", " It was discussed by the government.", " 🇯🇵"):
            with self.subTest(suffix=suffix), self.assertRaises(SourceError):
                snapshot(tweet(text=TEXT + suffix), NOW)

    def test_linked_title_is_checked_and_never_added_as_factual_text(self):
        source = tweet()
        source._data["card"] = {"legacy": {"binding_values": [{
            "key": "title", "value": {"string_value": "Our new model launches in Japan"},
        }]}}
        with self.assertRaises(SourceError):
            snapshot(source, NOW)
        source._data["card"]["legacy"]["binding_values"][0]["value"]["string_value"] = "Faster coding tools"
        self.assertEqual(snapshot(source, NOW)["text"], TEXT)

    def test_article_title_preview_and_entity_metadata_are_checked(self):
        source = tweet()
        source._data["article"] = {"article_results": {"result": {
            "rest_id": "11", "title": "Model update", "preview_text": "Election tools are here",
        }}}
        with self.assertRaises(SourceError):
            snapshot(source, NOW)
        source = tweet(urls=[{"expanded_url": "https://example.com/news/model", "title": "AI in China"}])
        with self.assertRaises(SourceError):
            snapshot(source, NOW)

    def test_article_preview_does_not_rescue_a_bare_link(self):
        source = tweet(text="https://t.co/article")
        source._data["article"] = {"article_results": {"result": {
            "rest_id": "11", "title": TEXT, "preview_text": TEXT,
        }}}
        with self.assertRaises(SourceError):
            snapshot(source, NOW)

    def test_edit_aliases_are_normalized_and_deduplicated(self):
        source = snapshot(tweet(aliases=[1000, "1001", "1002", "1000"]), NOW)
        self.assertEqual(source["source_ids"], ["1001", "1000", "1002"])

    def test_entity_urls_are_dicts_and_tracking_is_removed(self):
        source = snapshot(tweet(urls=[
            {"url": "https://t.co/abc", "expanded_url": "https://www.example.com/news/model/?utm_source=x#intro"},
            {"expanded_url": "https://example.com/news/model"},
            {"expanded_url": "https://example.com/"},
            {"expanded_url": "https://x.com/OpenAI/status/9"},
        ]), NOW)
        self.assertEqual(source["urls"], ["https://example.com/news/model"])

    def test_note_entity_urls_are_used(self):
        source = tweet()
        source._data["note_tweet"] = {"note_tweet_results": {"result": {
            "text": TEXT, "entity_set": {"urls": [{"expanded_url": "https://example.com/news/note"}]},
        }}}
        self.assertEqual(snapshot(source, NOW)["urls"], ["https://example.com/news/note"])

    def test_invalid_snapshot_identity_timestamp_and_aliases_fail(self):
        original = snapshot(tweet(), NOW)
        for change in (
            {"id": "invalid"}, {"author_id": "0"},
            {"url": "https://x.com/another/status/1001"},
            {"created_at": "2026-11-05T11:00:00"}, {"created_at": None},
            {"source_ids": []}, {"source_ids": [True]}, {"urls": ["https://x.com/any/status/1"]},
        ):
            with self.subTest(change=change), self.assertRaises(SourceError):
                validate({**original, **change}, NOW)

    def test_malformed_client_metadata_rejects_the_item_as_source_error(self):
        for field, value in (("note_tweet", []), ("note_tweet", "invalid"),
                             ("edit_control", "invalid"), ("views", [])):
            source = tweet()
            source._data[field] = value
            if value == [] and field == "note_tweet":
                # Empty optional blocks are equivalent to absent blocks.
                self.assertEqual(snapshot(source, NOW)["text"], TEXT)
                continue
            with self.subTest(field=field, value=value), self.assertRaises(SourceError):
                snapshot(source, NOW)
        source = tweet()
        del source._data["rest_id"]
        with self.assertRaises(SourceError):
            snapshot(source, NOW)


class CanonicalUrlTests(unittest.TestCase):
    def test_tracking_scheme_host_case_and_fragments_do_not_change_identity(self):
        self.assertEqual(canonical_url("http://WWW.Example.COM/news/model/?utm_source=x&ref=feed#part"),
                         "https://example.com/news/model")

    def test_meaningful_parameters_identify_different_articles(self):
        self.assertEqual(canonical_url("https://example.com/?p=12&utm_campaign=a"), "https://example.com/?p=12")
        self.assertNotEqual(canonical_url("https://example.com/article?id=1"),
                            canonical_url("https://example.com/article?id=2"))
        self.assertEqual(canonical_url("https://example.com/article?b=2&a=1"),
                         canonical_url("https://example.com/article?a=1&b=2"))

    def test_homepages_shorteners_social_and_unsafe_urls_are_not_article_keys(self):
        for url in (
            "https://example.com", "https://example.com/?utm_source=x", "https://example.com/blog/",
            "https://x.com/OpenAI/status/100", "https://twitter.com/OpenAI", "https://t.co/x",
            "https://bit.ly/example", "file:///tmp/article", "javascript:alert(1)",
            "https://name:password@example.com/story", "https://127.0.0.1/story",
            "https://localhost/story", "https://example.local/story", "https://example.com:1234/story",
            "https://example.com\\@evil.test/story", "https://example.com/a b", None,
        ):
            with self.subTest(url=url):
                self.assertEqual(canonical_url(url), "")


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_bounded_news_queries_use_top_without_author_limits(self):
        client = SimpleNamespace(search_tweet=AsyncMock(side_effect=[[tweet(author="indie_builder")], [], [], []]))
        output = io.StringIO()
        with redirect_stdout(output):
            results = await discover(client, {}, NOW)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["author"],"indie_builder")
        self.assertEqual(client.search_tweet.await_count, 4)
        self.assertEqual([call.args[1] for call in client.search_tweet.call_args_list],["Top"]*4)
        for call in client.search_tweet.call_args_list:
            self.assertEqual(call.kwargs["count"], SEARCH_COUNT)
            self.assertNotIn("from:",call.args[0])
            self.assertIn("since:2026-11-04",call.args[0])
            self.assertIn("-filter:replies",call.args[0])
            self.assertIn("-filter:retweets",call.args[0])
            self.assertLess(len(call.args[0]),220)
        self.assertIn("examined=1 eligible=1", output.getvalue())
        self.assertIn("Source query 1 (Top): examined=1 eligible=1",output.getvalue())
        self.assertIn("Source query 4 (Top): examined=0 eligible=0",output.getvalue())
        self.assertNotIn(TEXT, output.getvalue())

    def test_query_rotation_expands_coverage_without_extra_requests(self):
        first=search_plan(NOW)
        second=search_plan(NOW+timedelta(hours=1))
        self.assertEqual(first,search_plan(NOW))
        self.assertNotEqual(first,second)
        self.assertEqual(len(first),4)
        self.assertEqual(len(second),4)
        self.assertEqual([product for _,product in second],["Top"]*4)
        for plan in (first,second):
            self.assertTrue(any('Claude Code' in query or 'GitHub Copilot' in query for query,_ in plan))
            self.assertTrue(any('coding' in query for query,_ in plan))
            self.assertTrue(any('agent SDK' in query or 'code review' in query for query,_ in plan))
            for query,_ in plan[:3]:
                self.assertIn(ANNOUNCEMENT_TERMS,query)
                self.assertIn('announced',query)
                self.assertLess(len(query),220)
            self.assertNotIn(ANNOUNCEMENT_TERMS,plan[3][0])
            self.assertFalse(any(term in query for query,_ in plan
                                 for term in ('battery','startup','Tesla','Porsche','BMW','cars','automotive')))

    async def test_used_source_ids_include_older_edit_aliases(self):
        client = SimpleNamespace(search_tweet=AsyncMock(side_effect=[[tweet(author="indie_builder",aliases=["900", "1001"])], [], [], []]))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(await discover(client, {"used_source_ids": ["900"]}, NOW), [])

    async def test_persistent_article_tracking_variants_are_duplicates(self):
        item = tweet(urls=[{"expanded_url": "https://example.com/news/model?utm_source=x"}])
        state = {"used_urls": ["https://www.example.com/news/model/?ref=share"]}
        before = copy.deepcopy(state)
        client = SimpleNamespace(search_tweet=AsyncMock(side_effect=[[item], [], [], []]))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(await discover(client, state, NOW), [])
        self.assertEqual(state, before)

    async def test_same_discovery_alias_and_article_duplicates_are_removed(self):
        items = [
            tweet("1001", aliases=["900", "1001"]),
            tweet("1002", aliases=["900", "1002"]),
            tweet("1003", urls=[{"expanded_url": "https://example.com/news/model?utm_source=x"}]),
            tweet("1004", urls=[{"expanded_url": "https://example.com/news/model"}]),
        ]
        client = SimpleNamespace(search_tweet=AsyncMock(side_effect=[items, [items[0]], [], []]))
        with redirect_stdout(io.StringIO()):
            result = await discover(client, {}, NOW)
        self.assertEqual({item["id"] for item in result}, {"1001", "1003"})

    async def test_bad_source_is_skipped_without_hiding_a_valid_source(self):
        client = SimpleNamespace(search_tweet=AsyncMock(side_effect=[[tweet(views=None), tweet("1002")], [], [], []]))
        with redirect_stdout(io.StringIO()):
            result = await discover(client, {}, NOW)
        self.assertEqual([item["id"] for item in result], ["1002"])

    async def test_diagnostic_counts_explain_rejection_without_printing_source_text(self):
        items=[tweet("1",views=None),tweet("2",views="99999"),
               tweet("3",text=TEXT+" The government is involved."),
               tweet("4",age=MAX_AGE+timedelta(seconds=1)),
               tweet("5",text=TEXT+"…"),tweet("6",in_reply_to_status_id_str="7"),
               tweet("8",author="car_notes")]
        client=SimpleNamespace(search_tweet=AsyncMock(side_effect=[items,[],[],[]]))
        output=io.StringIO()
        with redirect_stdout(output):
            result=await discover(client,{},NOW)
        self.assertEqual([item['id'] for item in result],['8'])
        for reason in ('unknown_views','below_100k','excluded_topic','outside_24h','truncated_text','reply'):
            self.assertIn(reason+'=1',output.getvalue())
        self.assertIn('examined=7 eligible=1 ineligible=6 duplicates=0',output.getvalue())
        self.assertNotIn(TEXT,output.getvalue())

    async def test_cross_author_duplicates_remain_blocked_in_broader_search(self):
        first=tweet("101",author="indie_builder",urls=[{'expanded_url':'https://example.com/blog/editor'}])
        second=tweet("102",author="coder42",urls=[{'expanded_url':'https://example.com/blog/editor?utm_source=x'}])
        client=SimpleNamespace(search_tweet=AsyncMock(side_effect=[[first],[second],[],[first]]))
        with redirect_stdout(io.StringIO()):
            result=await discover(client,{},NOW)
        self.assertEqual([item['id'] for item in result],['101'])

    async def test_transport_and_session_failures_propagate_without_retry(self):
        class SessionFailure(Exception):
            pass
        for failure in (TimeoutError("network"), SessionFailure("invalid session")):
            client = SimpleNamespace(search_tweet=AsyncMock(side_effect=failure))
            with self.subTest(failure=type(failure).__name__), self.assertRaises(type(failure)):
                await discover(client, {}, NOW)
            self.assertEqual(client.search_tweet.await_count, 1)


if __name__ == "__main__":
    unittest.main()
