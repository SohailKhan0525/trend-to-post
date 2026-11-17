from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import editorial, mentions, sources
from app.reply_freshness import INBOX_START_AT, MAX_REPLY_AGE, X_EPOCH, fresh_invitation, invitation_hold
from test_editorial import DRAFT, SOURCE, empty


def snowflake(at):
    return str(((at - X_EPOCH) // timedelta(milliseconds=1)) << 22)


def invitation(at):
    ident = snowflake(at)
    return {**SOURCE, 'id': ident, 'source_ids': [ident], 'created_at': at.isoformat(),
            'conversation_id': ident, 'url': 'https://x.com/Acme/status/' + ident}


class ReplyFreshnessTests(unittest.TestCase):
    def test_inclusive_cutoff_and_rolling_window_remain_fixed_across_restarts(self):
        start = INBOX_START_AT
        self.assertTrue(fresh_invitation(invitation(start), start))
        self.assertFalse(fresh_invitation(invitation(start - timedelta(milliseconds=1)), start))
        self.assertFalse(fresh_invitation(invitation(start - timedelta(days=1)), start))
        self.assertTrue(fresh_invitation(invitation(start), start + MAX_REPLY_AGE))
        self.assertFalse(fresh_invitation(invitation(start), start + MAX_REPLY_AGE + timedelta(microseconds=1)))
        tomorrow = start + timedelta(days=1)
        self.assertTrue(fresh_invitation(invitation(tomorrow), tomorrow))
        self.assertFalse(fresh_invitation(invitation(start), tomorrow))

    def test_new_child_of_old_post_keeps_its_own_age(self):
        new = invitation(INBOX_START_AT)
        old_root = snowflake(INBOX_START_AT - timedelta(days=90))
        new.update(parent_id=old_root, conversation_id=old_root, parent_context={'id': old_root})
        self.assertTrue(fresh_invitation(new, INBOX_START_AT))

    def test_oldest_native_edit_governs_even_when_x_returns_latest_created_at(self):
        new = invitation(INBOX_START_AT + timedelta(minutes=1))
        oldest = snowflake(INBOX_START_AT - timedelta(seconds=1))
        for raw in (
                {'edit_control': {'edit_tweet_ids': [oldest, new['id']]}},
                {'edit_control': {'initial_tweet_id': oldest}},
                {'edit_control': {'edit_control_initial': {'edit_tweet_ids': [oldest, new['id']]}}},
                {'edit_history_tweet_ids': [oldest, new['id']]}):
            with self.subTest(shape=raw):
                new['source_ids'] = sources.tweet_ids(SimpleNamespace(id=new['id'], _data=raw))
                self.assertEqual(new['source_ids'], [new['id'], oldest])
                self.assertEqual(invitation_hold(new, INBOX_START_AT + timedelta(minutes=2)),
                                 'source predates reply activation')

    def test_reported_or_native_future_dates_fail_closed(self):
        current = invitation(INBOX_START_AT)
        future = invitation(INBOX_START_AT + timedelta(seconds=1))
        for key in ('id', 'created_at', 'source_ids'):
            self.assertFalse(fresh_invitation({**current, key: future[key]}, INBOX_START_AT))
        for invalid in ({'created_at': ''}, {'created_at': '2026-11-17T14:05:11'},
                        {'source_ids': None}, {'source_ids': []}, {'source_ids': ['malformed']}, {'id': True}):
            self.assertFalse(fresh_invitation({**current, **invalid}, INBOX_START_AT))

    def test_native_milliseconds_resolve_xs_whole_second_timestamp(self):
        created = INBOX_START_AT + timedelta(milliseconds=255)
        incoming = {**invitation(created), 'created_at': INBOX_START_AT.isoformat()}
        self.assertTrue(fresh_invitation(incoming, created + MAX_REPLY_AGE))
        self.assertFalse(fresh_invitation(incoming, created - timedelta(milliseconds=1)))


class ReplySendClockTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = empty()
        self.user = SimpleNamespace(id='999', screen_name=editorial.ACCOUNT)
        self.now = INBOX_START_AT + timedelta(minutes=4, seconds=59)
        self.source = invitation(INBOX_START_AT)
        self.slot = {**editorial.inbox_opportunity(self.source), 'kind': 'reply'}
        self.connection = SimpleNamespace(get_tweet_by_id=AsyncMock(), create_tweet=AsyncMock())
        self.enterContext(patch.object(editorial, 'utcnow', side_effect=lambda: self.now))
        self.enterContext(patch.object(editorial, 'summary'))
        self.enterContext(patch.object(editorial, 'stage'))

    async def test_model_finishes_after_five_minutes_without_sending_or_reserving(self):
        def slow_draft(*_args, **_kwargs):
            self.now += timedelta(seconds=2)
            return dict(DRAFT)
        with patch.object(mentions, 'snapshot', return_value=self.source), \
                patch.object(editorial, 'live_thread_open', new=AsyncMock(return_value=True)), \
                patch.object(editorial, 'save_and_push'), \
                patch.object(editorial.reply_writer, 'generate', side_effect=slow_draft) as writer:
            await editorial.publish_reply(self.state, self.slot, self.connection, self.user, selected=self.source)
        writer.assert_called_once()
        self.connection.create_tweet.assert_not_awaited()
        self.assertFalse(self.state.get('outbox'))

    async def test_slow_durable_reservation_expires_before_x_write(self):
        observed = []
        def save(state):
            if state.get('outbox'):
                observed.append(state['outbox']['source']['id'])
                self.now += timedelta(seconds=2)
        with patch.object(editorial, 'save_and_push', side_effect=save):
            await editorial.send_once(self.state, self.slot, DRAFT, self.connection, self.user, self.source)
        self.assertEqual(observed, [self.source['id']])
        self.connection.create_tweet.assert_not_awaited()
        self.assertIsNone(self.state['outbox'])
