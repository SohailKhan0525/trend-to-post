import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import editorial, question_posts
from app.reply_schedule import half_hour_start, verified_originals_today
from test_editorial import empty


NOW = datetime(2026, 11, 25, 12, 7, tzinfo=timezone.utc)
DRAFT = {
    'text': 'Your database fits in memory but takes an hour to restore. Would you buy more RAM or test a smaller backup first?',
    'premise': 'Recovery time versus extra hardware for a small database',
    'event_key': 'database restore testing versus extra memory',
}


class ScheduledQuestionPublishingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = NOW
        self.state = empty()
        self.state['actions_inbox_since'] = (NOW - timedelta(days=1)).isoformat()
        self.user = SimpleNamespace(id='999', screen_name='qofenoai')
        self.post = SimpleNamespace(id='998', user=self.user, full_text=DRAFT['text'], in_reply_to=None,
                                    created_at_datetime=NOW, _data={'legacy': {}})
        self.saved = []
        self.connection = SimpleNamespace(
            get_user_by_screen_name=AsyncMock(return_value=self.user), get_tweet_by_id=AsyncMock(return_value=self.post),
            create_tweet=AsyncMock(return_value=self.post))
        self.enterContext(patch.object(editorial, 'utcnow', side_effect=lambda: self.now))
        self.enterContext(patch.object(editorial, 'load', return_value=self.state))
        self.enterContext(patch.object(editorial, 'client', return_value=self.connection))
        self.enterContext(patch.object(editorial, 'save_and_push',
                                       side_effect=lambda state: self.saved.append(copy.deepcopy(state))))
        self.enterContext(patch.object(editorial, 'summary'))
        self.priority = self.enterContext(patch.object(editorial, 'process_priority', new_callable=AsyncMock))
        self.inbox = self.enterContext(patch.object(editorial, 'process_inbox', new_callable=AsyncMock))
        self.writer = self.enterContext(patch.object(question_posts, 'generate', return_value=DRAFT))
        self.discovery = self.enterContext(patch.object(editorial, 'decide', new_callable=AsyncMock))
        self.discovery.side_effect = lambda state, slot, *a, **kw: editorial.record_silence(state, slot, self.now, {})

    async def test_release_wake_reserves_parentless_question_then_reads_back_and_keeps_reply_routing(self):
        order = Mock()
        order.attach_mock(self.priority, 'priority')
        order.attach_mock(self.inbox, 'inbox')
        order.attach_mock(self.writer, 'writer')
        order.attach_mock(self.discovery, 'discovery')
        await editorial.run('push')
        self.assertEqual([call[0] for call in order.mock_calls], ['priority', 'inbox', 'writer', 'discovery'])
        self.assertEqual(self.discovery.call_args.args[1]['allowed_actions'], ['reply'])
        self.connection.create_tweet.assert_awaited_once_with(text=DRAFT['text'])
        self.connection.get_tweet_by_id.assert_awaited_once_with(self.post.id)
        reservation = next(saved for saved in self.saved if saved.get('outbox'))
        self.assertEqual(reservation['history'], [])
        self.assertNotIn('tweet_id', reservation['outbox'])
        self.assertNotIn('source', reservation['outbox'])
        self.assertEqual(reservation['outbox']['post_style'], 'open_question')
        self.assertIn(editorial.reply_writer.fingerprint(DRAFT['text']), reservation['used_texts'])
        self.assertIn(editorial.reply_writer.fingerprint(DRAFT['event_key']), reservation['used_events'])
        self.assertEqual(self.state['used_source_ids'], [])
        self.assertEqual(self.state['history'][0]['kind'], 'original')
        self.assertEqual(verified_originals_today(self.state, self.now), 1)
        await editorial.run('scheduled')
        self.assertEqual(self.writer.call_count, 1)
        self.connection.create_tweet.assert_awaited_once()

    async def test_original_draft_failure_keeps_existing_reply_opportunity_and_paces_retries(self):
        self.writer.side_effect = editorial.DraftError('Question repeats a previous dilemma')
        await editorial.run('scheduled')
        self.discovery.assert_awaited_once()
        self.assertTrue(self.discovery.call_args.args[1]['discovery_reply'])
        self.assertFalse(self.state.get('outbox'))
        self.assertEqual(verified_originals_today(self.state, self.now), 0)
        await editorial.run('scheduled')
        self.writer.assert_called_once()
        self.now += timedelta(minutes=10)
        await editorial.run('scheduled')
        self.assertEqual(self.writer.call_count, 2)
        self.connection.create_tweet.assert_not_awaited()

    async def test_readback_delay_resumes_known_original_without_another_send(self):
        self.connection.get_tweet_by_id.side_effect = [TimeoutError(), self.post]
        await editorial.run('scheduled')
        self.assertEqual(self.state['outbox']['tweet_id'], self.post.id)
        self.assertEqual(verified_originals_today(self.state, self.now), 0)
        self.discovery.assert_not_awaited()
        await editorial.run('workflow_dispatch')
        self.assertIsNone(self.state['outbox'])
        self.assertEqual(verified_originals_today(self.state, self.now), 1)
        self.writer.assert_called_once()
        self.connection.create_tweet.assert_awaited_once()
        self.discovery.assert_awaited_once()

    async def test_last_minute_review_and_duplicate_recheck_before_reservation(self):
        self.state['used_events'].append(editorial.reply_writer.fingerprint(DRAFT['event_key']))
        await editorial.run('scheduled')
        self.writer.assert_called_once()
        self.connection.create_tweet.assert_not_awaited()
        self.assertFalse(self.state.get('outbox'))
        self.assertEqual(self.state['history'], [])
        self.assertEqual(verified_originals_today(self.state, self.now), 0)
        self.discovery.assert_awaited_once()

    async def test_only_current_canonical_question_opportunity_allows_standalone_send(self):
        current = editorial.question_slot(half_hour_start(self.now))
        fresh = self.now
        for changes, source in (({'kind': 'reply'}, None), ({'slot': 'older-policy'}, None),
                                ({'expires_at': (fresh + timedelta(days=1)).isoformat()}, None),
                                ({}, {'id': '123'})):
            with self.subTest(changes=changes, source=source):
                await editorial.send_once(self.state, {**current, **changes}, DRAFT,
                                          self.connection, self.user, source)
        self.now += timedelta(minutes=30)
        await editorial.send_once(self.state, current, DRAFT, self.connection, self.user)
        self.connection.create_tweet.assert_not_awaited()
        self.assertEqual(self.saved, [])

    async def test_fifth_verified_original_closes_originals_for_day_but_keeps_reply_capacity(self):
        self.now = NOW.replace(hour=23)
        self.post.created_at_datetime = self.now
        for i in range(4):
            posted = self.now.replace(hour=i).isoformat()
            self.state['history'].append({'kind': 'original', 'posted_at': posted, 'verified_at': posted,
                                          'tweet_id': str(1200 + i)})
        await editorial.run('scheduled')
        self.assertEqual(verified_originals_today(self.state, self.now), 5)
        self.now += timedelta(minutes=30)
        await editorial.run('scheduled')
        self.writer.assert_called_once()
        self.connection.create_tweet.assert_awaited_once()
        self.assertEqual(self.discovery.await_count, 2)
