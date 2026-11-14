import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import editorial
from app.reply_schedule import daily_plan, timestamp


START = datetime(2026, 11, 13, tzinfo=timezone.utc)
SOURCE = {
    'id': '111', 'author': 'Acme', 'author_id': '222',
    'text': 'Our code editor can now explain changes across a repository.',
    'created_at': START.isoformat(), 'views': 12345,
    'url': 'https://x.com/Acme/status/111',
    'urls': ['https://acme.example/blog/editor'], 'source_ids': ['111', '110'],
}
DRAFT = {'text': 'Does it keep the explanation useful when the diff touches several packages?',
         'event_key': 'acme-editor-explanations'}
FOLLOW = {'text': 'Follow for coding-tool questions that survive a second look.',
          'event_key': 'follow-coding-tool-questions'}


def empty():
    return {'version': 1, 'history': [], 'editorial_history': [],
            'used_source_ids': [], 'used_urls': [], 'used_events': [],
            'used_texts': [], 'last_attempts': {}}


class EditorialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plan = daily_plan(START.date().isoformat())
        self.slot = [slot for slot in self.plan if slot['kind'] == 'reply'][2]
        self.now = timestamp(self.slot['due_at']) + timedelta(minutes=3)
        self.user = SimpleNamespace(id='999', screen_name=editorial.ACCOUNT)
        self.saved, self.saves = empty(), []
        for mocked in (
            patch.object(editorial, 'utcnow', side_effect=lambda: self.now),
            patch.object(editorial, 'save_and_push', side_effect=self.persist),
            patch.object(editorial, 'load', side_effect=lambda: copy.deepcopy(self.saved)),
            patch.object(editorial, 'summary'), patch.object(editorial, 'stage'),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def persist(self, state):
        self.saved = copy.deepcopy(state)
        self.saves.append(copy.deepcopy(state))

    def connection(self, kind='reply'):
        post = SimpleNamespace(id='333', user=self.user,
                               full_text=(DRAFT if kind == 'reply' else FOLLOW)['text'],
                               in_reply_to='111' if kind == 'reply' else None)
        return SimpleNamespace(user=AsyncMock(return_value=self.user),
                               get_user_by_screen_name=AsyncMock(return_value=self.user),
                               http=SimpleNamespace(cookies=SimpleNamespace(jar=[])),
                               create_tweet=AsyncMock(return_value=post),
                               get_tweet_by_id=AsyncMock(return_value=post))

    def pending(self, tweet_id=None):
        row = {**DRAFT, 'slot': self.slot['slot'], 'kind': 'reply', 'source': SOURCE,
               'author_id': self.user.id, 'reserved_at': self.now.isoformat()}
        if tweet_id:
            row['tweet_id'] = tweet_id
        return row

    async def test_reply_refreshes_source_twice_and_uses_native_reply_transport(self):
        state, connection = empty(), self.connection()

        async def send(**kwargs):
            self.assertEqual(kwargs, {'text': DRAFT['text'], 'reply_to': SOURCE['id']})
            self.assertEqual(self.saved['outbox']['source'], SOURCE)
            self.assertNotIn('tweet_id', self.saved['outbox'])
            self.assertEqual(self.saved['history'], [])
            return SimpleNamespace(id='333')

        connection.create_tweet.side_effect = send
        with patch.object(editorial.sources, 'discover', AsyncMock(return_value=[SOURCE])), \
             patch.object(editorial.sources, 'snapshot', return_value=SOURCE) as snapshot, \
             patch.object(editorial.reply_writer, 'generate', return_value=DRAFT):
            await editorial.publish_reply(state, self.slot, connection, self.user)
        self.assertEqual(snapshot.call_count, 2)
        self.assertEqual([call.args[0] for call in connection.get_tweet_by_id.await_args_list], ['111', '111', '333'])
        connection.create_tweet.assert_awaited_once_with(text=DRAFT['text'], reply_to='111')
        self.assertIsNone(self.saved['outbox'])
        self.assertEqual(self.saved['history'][0]['source']['views'], 12345)
        self.assertEqual(self.saved['editorial_history'][0]['kind'], 'reply')
        self.assertEqual(self.saved['used_source_ids'], ['110', '111'])
        self.assertIn(SOURCE['urls'][0], self.saved['used_urls'])
        self.assertIn(editorial.reply_writer.fingerprint(DRAFT['text']), self.saved['used_texts'])

    async def test_source_changed_or_rejected_during_refresh_is_not_sent(self):
        for changed_at in ('first', 'second', 'under_views'):
            with self.subTest(changed_at=changed_at):
                state, connection = empty(), self.connection()
                changed = {**SOURCE, 'text': 'Edited source text'}
                results = ([changed] if changed_at == 'first' else [SOURCE, changed])
                if changed_at == 'under_views':
                    results = [editorial.sources.SourceError('below_min_views')]
                with patch.object(editorial.sources, 'discover', AsyncMock(return_value=[SOURCE])), \
                     patch.object(editorial.sources, 'snapshot', side_effect=results), \
                     patch.object(editorial.reply_writer, 'generate', return_value=DRAFT) as generate:
                    await editorial.publish_reply(state, self.slot, connection, self.user)
                connection.create_tweet.assert_not_awaited()
                self.assertEqual(self.saved['history'], [])
                self.assertEqual(self.saved['used_source_ids'], [])
                if changed_at != 'second':
                    generate.assert_not_called()

    async def test_used_source_alias_or_url_cannot_be_sent_again(self):
        for key, value in (('used_source_ids', '110'), ('used_urls', SOURCE['urls'][0])):
            state, connection = empty(), self.connection()
            state[key] = [value]
            await editorial.send_once(state, self.slot, DRAFT, connection, self.user, SOURCE)
            connection.create_tweet.assert_not_awaited()
            self.assertEqual(self.saved['history'], [])

    async def test_own_account_and_recently_replied_author_are_excluded(self):
        state, connection = empty(), self.connection()
        state['history'] = [{'kind': 'reply', 'source': SOURCE, 'posted_at': self.now.isoformat()}]
        own = {**SOURCE, 'id': '444', 'author': editorial.ACCOUNT.upper(), 'author_id': '999'}
        with patch.object(editorial.sources, 'discover', AsyncMock(return_value=[own, SOURCE])), \
             patch.object(editorial.reply_writer, 'generate') as generate:
            await editorial.publish_reply(state, self.slot, connection, self.user)
        generate.assert_not_called()
        connection.get_tweet_by_id.assert_not_awaited()
        connection.create_tweet.assert_not_awaited()

    async def test_follow_has_no_reply_parent_or_quote_attachment(self):
        state, connection = empty(), self.connection('follow')
        slot = next(row for row in self.plan if row['kind'] == 'follow')
        self.now = timestamp(slot['due_at']) + timedelta(seconds=1)
        previous = [row for row in self.plan if row['kind'] == 'reply'
                    and timestamp(row['due_at']) < self.now][-1]
        state['editorial_history'] = [{**previous, 'completed_at': previous['due_at']}]
        with patch.object(editorial.follow_writer, 'generate', return_value=FOLLOW):
            await editorial.publish_follow(state, slot, connection, self.user)
        connection.create_tweet.assert_awaited_once_with(text=FOLLOW['text'])
        self.assertEqual(self.saved['history'][0]['kind'], 'follow')
        self.assertNotIn('source', self.saved['history'][0])

    async def test_failed_reservation_persistence_prevents_send(self):
        connection = self.connection()
        with patch.object(editorial, 'save_and_push', side_effect=OSError('remote unavailable')):
            with self.assertRaises(OSError):
                await editorial.send_once(empty(), self.slot, DRAFT, connection, self.user, SOURCE)
        connection.create_tweet.assert_not_awaited()

    async def test_unknown_send_outcome_stays_reserved_and_cannot_be_retried(self):
        state, connection = empty(), self.connection()
        connection.create_tweet.side_effect = TimeoutError('unknown send outcome')
        with self.assertRaises(TimeoutError):
            await editorial.send_once(state, self.slot, DRAFT, connection, self.user, SOURCE)
        await editorial.send_once(copy.deepcopy(self.saved), self.slot, DRAFT, connection, self.user, SOURCE)
        connection.create_tweet.assert_awaited_once()
        self.assertIsNotNone(self.saved['outbox'])
        self.assertNotIn('tweet_id', self.saved['outbox'])
        self.assertEqual(self.saved['history'], [])

    async def test_invalid_send_id_retains_reservation(self):
        connection = self.connection()
        connection.create_tweet.return_value = SimpleNamespace(id='invalid')
        with self.assertRaises(ValueError):
            await editorial.send_once(empty(), self.slot, DRAFT, connection, self.user, SOURCE)
        self.assertIsNotNone(self.saved['outbox'])
        connection.get_tweet_by_id.assert_not_awaited()

    async def test_native_permission_rejections_are_skipped_without_bypass(self):
        for error in (editorial.Forbidden, editorial.NotFound, editorial.TweetNotAvailable):
            with self.subTest(error=error.__name__):
                state, connection = empty(), self.connection()
                connection.create_tweet.side_effect = error('native rejection')
                await editorial.send_once(state, self.slot, DRAFT, connection, self.user, SOURCE)
                connection.create_tweet.assert_awaited_once()
                connection.get_tweet_by_id.assert_not_awaited()
                self.assertIsNone(self.saved['outbox'])
                self.assertEqual(self.saved['history'], [])
                self.assertIn('111', self.saved['used_source_ids'])
                self.assertEqual(self.saved['delivery_rejections'][0]['error'], error.__name__)

    async def test_native_rate_limit_pauses_publication_for_thirty_minutes(self):
        connection = self.connection()
        connection.create_tweet.side_effect = editorial.TooManyRequests('native limit')
        await editorial.send_once(empty(), self.slot, DRAFT, connection, self.user, SOURCE)
        self.assertIsNone(self.saved['outbox'])
        self.assertEqual(timestamp(self.saved['x_pause_until']), self.now+timedelta(minutes=30))
        with patch.object(editorial, 'client', return_value=connection), \
             patch.object(editorial, 'publish_reply', AsyncMock()) as publish:
            await editorial.run('push')
        publish.assert_not_awaited()
        connection.create_tweet.assert_awaited_once()

    async def test_pending_guards_prevent_all_new_publication(self):
        for key in ('pending', 'pending_reply', 'verification_pending', 'pause_reason', 'outbox'):
            with self.subTest(key=key):
                state, connection = empty(), self.connection()
                state[key] = self.pending() if key == 'outbox' else {'status': 'unresolved'}
                self.persist(state)
                with patch.object(editorial, 'client', return_value=connection), \
                     patch.object(editorial, 'publish_reply', AsyncMock()) as reply, \
                     patch.object(editorial, 'publish_follow', AsyncMock()) as follow:
                    await editorial.run('push')
                reply.assert_not_awaited()
                follow.assert_not_awaited()
                connection.user.assert_not_awaited()
                connection.create_tweet.assert_not_awaited()

    async def test_wrong_configured_account_prevents_research_and_send(self):
        connection = self.connection()
        connection.get_user_by_screen_name.return_value = SimpleNamespace(id='666', screen_name='someone_else')
        with patch.object(editorial, 'client', return_value=connection), \
             patch.object(editorial, 'publish_reply', AsyncMock()) as reply, \
             patch.object(editorial, 'publish_follow', AsyncMock()) as follow:
            with self.assertRaises(editorial.SessionUnavailable):
                await editorial.run('push')
        reply.assert_not_awaited()
        follow.assert_not_awaited()
        connection.create_tweet.assert_not_awaited()

    async def test_expired_slot_never_reserves_or_sends(self):
        self.now = timestamp(self.slot['expires_at'])
        connection = self.connection()
        await editorial.send_once(empty(), self.slot, DRAFT, connection, self.user, SOURCE)
        self.assertIsNone(self.saved.get('outbox'))
        self.assertEqual(self.saved['used_source_ids'], [])
        connection.create_tweet.assert_not_awaited()

    async def test_expiry_during_reservation_push_never_sends(self):
        connection = self.connection()

        def slow_save(state):
            self.persist(state)
            self.now = timestamp(self.slot['expires_at'])

        with patch.object(editorial, 'save_and_push', side_effect=slow_save):
            await editorial.send_once(empty(), self.slot, DRAFT, connection, self.user, SOURCE)
        connection.create_tweet.assert_not_awaited()
        self.assertIsNone(self.saved.get('outbox'))
        self.assertIn('111', self.saved['used_source_ids'])

    async def test_known_post_readback_is_idempotent_and_uses_completion_time(self):
        state, connection = empty(), self.connection()
        state['outbox'] = self.pending('333')
        self.now += timedelta(minutes=5)
        self.assertTrue(await editorial.verify_delivery(state, connection))
        self.assertTrue(await editorial.verify_delivery(state, connection))
        connection.get_tweet_by_id.assert_awaited_once_with('333')
        connection.create_tweet.assert_not_awaited()
        self.assertEqual(len(self.saved['history']), 1)
        self.assertEqual(self.saved['editorial_history'][0]['completed_at'], self.now.isoformat())

    async def test_unknown_id_or_failed_readback_remains_reserved(self):
        for known_id in (None, '333'):
            state, connection = empty(), self.connection()
            state['outbox'] = self.pending(known_id)
            connection.get_tweet_by_id.side_effect = TimeoutError('readback unavailable')
            self.assertFalse(await editorial.verify_delivery(state, connection))
            self.assertIsNotNone(state['outbox'])
            self.assertEqual(state['history'], [])
            connection.create_tweet.assert_not_awaited()
            if known_id is None:
                connection.get_tweet_by_id.assert_not_awaited()

    async def test_delayed_readback_uses_verified_publication_time(self):
        state, connection = empty(), self.connection()
        state['outbox'] = self.pending('333')
        published = self.now + timedelta(seconds=2)
        connection.get_tweet_by_id.return_value.created_at_datetime = published
        self.now += timedelta(minutes=20)
        self.assertTrue(await editorial.verify_delivery(state, connection))
        self.assertEqual(self.saved['editorial_history'][0]['completed_at'], published.isoformat())
        self.assertEqual(self.saved['history'][0]['verified_at'], self.now.isoformat())
        connection.create_tweet.assert_not_awaited()

    def test_scheduler_uses_published_time_after_delayed_reconciliation(self):
        state = empty()
        published = self.now.isoformat()
        verified = (self.now + timedelta(minutes=20)).isoformat()
        state['history'] = [{'tweet_id': '333', 'posted_at': published, 'verified_at': verified}]
        state['editorial_history'] = [{'tweet_id': '333', 'kind': 'reply', 'completed_at': verified}]
        view = editorial.schedule_state(state)
        self.assertEqual(view['history'][0]['completed_at'], published)
        self.assertEqual(state['editorial_history'][0]['completed_at'], verified)

    async def test_native_parent_mention_verifies_existing_reply_without_resend(self):
        state, connection = empty(), self.connection()
        state['outbox'] = self.pending('333')
        state['pause_reason'] = 'Published post does not match the reserved author, text or reply parent'
        connection.get_tweet_by_id.return_value.full_text = '@Acme ' + DRAFT['text']
        self.assertTrue(await editorial.verify_delivery(state, connection))
        connection.create_tweet.assert_not_awaited()
        self.assertIsNone(self.saved['outbox'])
        self.assertNotIn('pause_reason', self.saved)
        self.assertEqual(self.saved['history'][0]['tweet_id'], '333')

    async def test_unrelated_mention_and_changed_punctuation_do_not_verify(self):
        for text in ('@SomeoneElse ' + DRAFT['text'], '@Acme ' + DRAFT['text'].replace('?', '!')):
            state, connection = empty(), self.connection()
            state['outbox'] = self.pending('333')
            connection.get_tweet_by_id.return_value.full_text = text
            self.assertFalse(await editorial.verify_delivery(state, connection))
            connection.create_tweet.assert_not_awaited()
            self.assertIsNotNone(self.saved['outbox'])

    async def test_existing_reply_with_native_source_participants_reconciles_without_resend(self):
        state, connection = empty(), self.connection()
        source = {**SOURCE, 'author': 'github', 'text': "🚀 @GoogleAI's model is in the CLI and @code."}
        state['outbox'] = {**self.pending('333'), 'source': source}
        state['pause_reason'] = 'Published post does not match the reserved author, text or reply parent'
        connection.get_tweet_by_id.return_value.full_text = '@github @GoogleAI @code ' + DRAFT['text']
        self.assertTrue(await editorial.verify_delivery(state, connection))
        self.assertTrue(await editorial.verify_delivery(state, connection))
        self.assertIsNone(self.saved['outbox'])
        self.assertNotIn('pause_reason', self.saved)
        self.assertEqual([row['tweet_id'] for row in self.saved['history']], ['333'])
        connection.get_tweet_by_id.assert_awaited_once_with('333')
        connection.create_tweet.assert_not_awaited()

    async def test_extra_recipients_need_real_source_mentions_and_the_exact_reply_body(self):
        source = {**SOURCE, 'text': 'Built with @code. See https://example.com/@other or hello@inbox.example.'}
        for prefix, body in (('@Acme @other', DRAFT['text']), ('@Acme @inbox', DRAFT['text']),
                             ('@code @Acme', DRAFT['text']), ('@Acme @code @code', DRAFT['text']),
                             ('@Acme @code', DRAFT['text'].replace('?', '!'))):
            with self.subTest(prefix=prefix, body=body):
                state, connection = empty(), self.connection()
                state['outbox'] = {**self.pending('333'), 'source': source}
                connection.get_tweet_by_id.return_value.full_text = prefix + ' ' + body
                self.assertFalse(await editorial.verify_delivery(state, connection))
                self.assertIsNotNone(self.saved['outbox'])
                self.assertEqual(self.saved['history'], [])
                connection.create_tweet.assert_not_awaited()

    async def test_readback_mismatch_pauses_instead_of_resending(self):
        mutations = ({'id': '444'}, {'full_text': 'Unexpected text'},
                     {'user': SimpleNamespace(id='666', screen_name=editorial.ACCOUNT)},
                     {'user': SimpleNamespace(id='999', screen_name='someone_else')},
                     {'in_reply_to': '444'}, {'in_reply_to': None},
                     {'quoted_status_id': '111'}, {'quote': object()})
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                state, connection = empty(), self.connection()
                state['outbox'] = self.pending('333')
                for key, value in mutation.items():
                    setattr(connection.get_tweet_by_id.return_value, key, value)
                self.assertFalse(await editorial.verify_delivery(state, connection))
                self.assertTrue(self.saved['pause_reason'])
                self.assertIsNotNone(self.saved['outbox'])
                self.assertEqual(self.saved['history'], [])
                connection.create_tweet.assert_not_awaited()

    async def test_push_wake_uses_current_slot_without_forcing_future_send(self):
        connection = self.connection()
        with patch.object(editorial, 'client', return_value=connection), \
             patch.object(editorial, 'publish_reply', AsyncMock()) as reply, \
             patch.object(editorial, 'publish_follow', AsyncMock()) as follow, \
             patch.object(editorial.asyncio, 'sleep', AsyncMock()) as sleep:
            await editorial.run('push')
        reply.assert_awaited_once()
        self.assertEqual(reply.await_args.args[1]['slot'], self.slot['slot'])
        follow.assert_not_awaited()
        sleep.assert_not_awaited()

    async def test_ai_pause_stops_research_and_send(self):
        state, connection = empty(), self.connection()
        state['ai_pause'] = {'until': (self.now+timedelta(hours=1)).isoformat(), 'reason': 'quota'}
        self.persist(state)
        with patch.object(editorial, 'client', return_value=connection), \
             patch.object(editorial, 'publish_reply', AsyncMock()) as reply, \
             patch.object(editorial, 'publish_follow', AsyncMock()) as follow:
            await editorial.run('push')
        reply.assert_not_awaited()
        follow.assert_not_awaited()
        connection.create_tweet.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
