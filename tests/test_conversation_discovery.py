from datetime import datetime, timedelta, timezone
import unittest

from app.sources import shortlist


NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)


def source(ident, author_id, group, *, views=10_000, age=1):
    author = f"writer_{author_id}"
    return {"id": ident, "author_id": author_id, "author": author,
            "url": f"https://x.com/{author}/status/{ident}",
            "text": "My build tool now records skipped tasks in the test output.",
            "urls": [], "source_ids": [ident], "views": views,
            "created_at": (NOW - timedelta(hours=age)).isoformat(),
            "discovery_query": group}


class ConversationDiscoveryTests(unittest.TestCase):
    def test_recent_reach_within_topic_preserves_turn_for_independent_search(self):
        older = source("101", "10", 2, views=150_000, age=20)
        rising = source("102", "11", 2, views=45_000, age=2)
        very_new = source("103", "12", 2, views=10_000, age=.01)
        firsthand = source("104", "13", 4)
        ranked = shortlist([older, firsthand, very_new, rising], NOW, limit=3)
        self.assertEqual([item["id"] for item in ranked], ["102", "104", "103"])

    def test_other_authors_get_considered_even_after_cross_query_duplicate_author(self):
        candidates = [source("101", "10", 1), source("102", "10", 4, views=700_000),
                      source("103", "11", 4), source("104", "12", 1)]
        ranked = shortlist(candidates, NOW)
        self.assertEqual([item["id"] for item in ranked], ["101", "103", "104"])
        self.assertEqual(shortlist(candidates, NOW, limit=0), [])

    def test_invalid_or_expired_sources_cannot_reenter_by_ranking_highly(self):
        expired = source("101", "10", 1, views=1_000_000, age=25)
        excluded = dict(source("102", "11", 1), text="This article discusses political elections and national parties.")
        unread = source("103", "12", 3, views=9_999)
        eligible = source("104", "13", 4)
        self.assertEqual([item["id"] for item in shortlist([expired, excluded, unread, eligible], NOW)], ["104"])
