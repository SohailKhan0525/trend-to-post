import unittest
from datetime import datetime, timezone

from scripts.publisher_status import report


class PublisherStatusTests(unittest.TestCase):
    def test_question_discovery_versions_are_reported_without_slot_details(self):
        now = datetime(2026, 11, 17, 12, 10, tzinfo=timezone.utc)
        for version in (1, 2, 3):
            with self.subTest(version=version):
                slot = f'2026-11-17:actions-discovery-reasoned-v{version}:24:0'
                state = {'history': [], 'decision_slots': {slot: {
                    'at': '2026-11-17T12:08:00Z', 'action': 'silent'}}}
                result = '\n'.join(report(state, now))
                self.assertIn('question discovery at 2026-11-17T12:08:00+00:00 (silence)', result)
                self.assertNotIn(slot, result)

    def test_observed_reply_time_uses_source_and_publication_times(self):
        now = datetime(2026, 11, 17, 12, 10, tzinfo=timezone.utc)
        row = {'tweet_id': '456', 'kind': 'reply', 'posted_at': '2026-11-17T12:09:23Z',
               'verified_at': '2026-11-17T12:09:45Z', 'source': {'created_at': '2026-11-17T12:09:02Z'}}
        self.assertIn('21s from incoming post to published reply', report({'history': [row]}, now)[0])

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
        self.assertIn('every 30 minutes. Reply eligibility in D1: open', result)
        self.assertIn('Each person can receive up to 2 replies per original conversation', result)
        self.assertIn('Next scheduled GitHub wake: 2026-11-17T12:37:00+00:00', result)
        self.assertNotIn('PRIVATE', result)

    def test_inbound_decision_supersedes_legacy_slot_without_logging_source_id(self):
        now = datetime(2026, 11, 17, 12, 10, tzinfo=timezone.utc)
        state = {'history': [], 'decision_slots': {
            '2026-11-17:reply-v1:23': {'at': '2026-11-17T12:00:00Z', 'action': 'silent'},
            '2026-11-17-inbox-891234567-fedcba9876543210': {
                'at': '2026-11-17T12:08:00Z', 'action': 'silent'}}}
        result = '\n'.join(report(state, now))
        self.assertIn('inbound reply at 2026-11-17T12:08:00+00:00 (silence)', result)
        self.assertNotIn('891234567', result)

    def test_silence_and_pending_are_reported_without_exposing_draft(self):
        now = datetime(2026, 11, 17, 12, 10, tzinfo=timezone.utc)
        state = {'history': [], 'outbox': {'text': 'PRIVATE'}, 'decision_slots': {
            '2026-11-17:reply-v1:23': {'action':'silent','at':'2026-11-17T11:40:00Z'}}}
        result = '\n'.join(report(state, now))
        self.assertIn('(silence)', result)
        self.assertIn('Pending delivery: True', result)
        self.assertIn('no X ID', result)
        self.assertIn('Reply eligibility in D1: on hold', result)
        self.assertNotIn('PRIVATE', result)
