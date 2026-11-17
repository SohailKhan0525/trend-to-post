import copy
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from twikit.errors import CouldNotTweet, DuplicateTweet

from app import editorial
from app.reply_schedule import daily_plan, timestamp


class NativePostingRefusalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.slot = next(row for row in daily_plan('2026-11-16') if row['kind'] == 'reply')
        self.now = timestamp(self.slot['due_at']) + timedelta(seconds=5)
        self.user = SimpleNamespace(screen_name=editorial.ACCOUNT, id='123')
        self.source = {'id': '456', 'source_ids': ['456'], 'author_id': '789', 'author': 'Dev',
                       'url': 'https://x.com/Dev/status/456', 'urls': ['https://example.com/runner'],
                       'views': 14, 'text': '@qofenoai how should my test runner retry failed steps?',
                       'origin': 'mention', 'intent': 'invite', 'parent_id': None, 'parent_author': None,
                       'created_at': self.now.isoformat()}
        self.draft = {'event_key': 'isolated retries for failing tests', 'text': 'Keep the first log beside the retry.'}

    async def attempt(self, error, previous_pause=None):
        state = {key: [] for key in ('history', 'editorial_history', 'used_source_ids',
                                     'used_urls', 'used_events', 'used_texts')}
        if previous_pause:
            state['x_pause_until'] = previous_pause.isoformat()
        self.saves = []
        connection = SimpleNamespace(create_tweet=AsyncMock(side_effect=error))
        with patch.object(editorial, 'utcnow', return_value=self.now), \
                patch.object(editorial, 'save_and_push', side_effect=lambda s: self.saves.append(copy.deepcopy(s))), \
                patch.object(editorial, 'summary'), patch.object(editorial, 'stage'):
            try:
                await editorial.send_once(state, self.slot, self.draft, connection, self.user, self.source)
            finally:
                connection.create_tweet.assert_awaited_once_with(text=self.draft['text'], reply_to=self.source['id'])

    async def test_missing_tweet_result_records_refusal_without_releasing_duplicate_protection(self):
        for message, delay in [('Native posting refusal', timedelta(minutes=30)),
                               ("You've hit the daily limit. (501)", timedelta(days=1))]:
            with self.subTest(delay=delay):
                await self.attempt(CouldNotTweet(message))
                before, after = self.saves
                self.assertNotIn('send_error', before['outbox'])
                self.assertEqual(after['outbox'], {**before['outbox'], 'send_error': 'CouldNotTweet'})
                self.assertNotIn('tweet_id', after['outbox'])
                self.assertEqual(after['history'], [])
                self.assertEqual(after['used_source_ids'], self.source['source_ids'])
                self.assertEqual(after['used_urls'], self.source['urls'])
                self.assertIn(editorial.reply_writer.fingerprint(self.draft['text']), after['used_texts'])
                self.assertIn(editorial.reply_writer.fingerprint(self.draft['event_key']), after['used_events'])
                self.assertEqual(timestamp(after['x_pause_until']), self.now + delay)
                self.assertNotIn(message, str(after))

    async def test_existing_longer_pause_survives_refusal(self):
        until = self.now + timedelta(hours=3)
        await self.attempt(CouldNotTweet('Refused'), previous_pause=until)
        self.assertEqual(timestamp(self.saves[-1]['x_pause_until']), until)

    async def test_duplicates_and_transport_errors_remain_unknown_outcomes(self):
        for error in (DuplicateTweet('duplicate'), TimeoutError('unknown')):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                await self.attempt(error)
            self.assertEqual(len(self.saves), 1)
            self.assertNotIn('send_error', self.saves[0]['outbox'])
