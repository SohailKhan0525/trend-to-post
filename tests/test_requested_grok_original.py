import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import question_posts, reply_writer


NOW = datetime(2026, 11, 25, 11, tzinfo=timezone.utc)
EXPECTED = (
    'Hey @grok, if AI can solve almost any INTELLECTUAL problem\n\n'
    'what becomes the biggest BOTTLENECK?\n\n'
    '1) Compute    2) Energy\n'
    '3) Data    4) Humans\n\n'
    'Or something NOBODY is talking about\n\n'
    'Be BRUTALLY HONEST.'
)


class RequestedGrokOriginalTests(unittest.TestCase):
    def test_first_requested_copy_matches_the_supplied_post_and_passes_send_time_checks(self):
        old = {'kind': 'original', 'text': 'A startup has one year of runway. Which path is more survivable?',
               'verified_at': NOW.isoformat()}
        state = {'history': [old]}
        with patch.object(question_posts, 'generate') as generate:
            draft = question_posts.scheduled_draft(state, NOW)
        self.assertEqual(draft['text'], EXPECTED)
        self.assertEqual(question_posts.validate(draft, state), draft)
        generate.assert_not_called()
        self.assertEqual(state, {'history': [old]})

    def test_reserved_published_or_legacy_copy_falls_through_to_a_fresh_question(self):
        first = question_posts.scheduled_draft({}, NOW)
        fingerprint = reply_writer.fingerprint
        for state in ({'used_texts': [fingerprint(first['text'])]},
                      {'used_events': [fingerprint(first['event_key'])]},
                      {'history': [{'kind': 'original', 'text': first['text'], 'verified_at': NOW.isoformat()}]}):
            with self.subTest(state=state), patch.object(question_posts, 'generate') as generate:
                self.assertIs(question_posts.scheduled_draft(state, NOW), generate.return_value)
                generate.assert_called_once_with(state, NOW)
