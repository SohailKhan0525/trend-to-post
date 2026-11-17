import unittest
from datetime import datetime, timezone

from scripts.azure_status import report


class AzureStatusTests(unittest.TestCase):
    def test_latest_decision_is_sanitized_and_distinct_from_last_send(self):
        now = datetime(2026, 11, 17, 12, 10, tzinfo=timezone.utc)
        state = {'history': [{'tweet_id': '456', 'slot': '2026-11-17:reply-v1:20',
                              'verified_at': '2026-11-17T10:05:00Z', 'text': 'PRIVATE'}],
                 'last_attempts': {'2026-11-17:reply-v1:23': {'at': '2026-11-17T11:40:00Z',
                                                              'reason': 'No approved decision or wording: PRIVATE'}},
                 'decision_slots': {'PRIVATE': {'at':'2026-11-17T12:09:00Z','action':'silent'}}}
        result = '\n'.join(report(state, now))
        self.assertIn('https://x.com/qofenoai/status/456', result)
        self.assertIn('23 at 2026-11-17T11:40:00+00:00 (wording or decision rejected)', result)
        self.assertIn('Pending delivery: False', result)
        self.assertIn('Next eligible', result)
        self.assertNotIn('PRIVATE', result)

    def test_silence_and_pending_are_reported_without_exposing_draft(self):
        now = datetime(2026, 11, 17, 12, 10, tzinfo=timezone.utc)
        state = {'history': [], 'outbox': {'text': 'PRIVATE'}, 'decision_slots': {
            '2026-11-17:reply-v1:23': {'action':'silent','at':'2026-11-17T11:40:00Z'}}}
        result = '\n'.join(report(state, now))
        self.assertIn('(silence)', result)
        self.assertIn('Pending delivery: True', result)
        self.assertNotIn('PRIVATE', result)
