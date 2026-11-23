from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import editorial, personality, reply_writer, sources
from app.reply_schedule import half_hour_start
from test_editorial import empty
from test_sources import tweet


NOW = datetime(2026, 11, 18, 12, 7, tzinfo=timezone.utc)


class ScheduledDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(editorial, 'process_priority', new_callable=AsyncMock))
        self.state = empty()
        self.state['actions_inbox_since'] = (NOW - timedelta(days=1)).isoformat()
        self.user = SimpleNamespace(id='999', screen_name=editorial.ACCOUNT)
        self.native = tweet('101', author='builder', text='The progress bar reached the end before the compiler started.',
                            created_at=(NOW - timedelta(hours=2)).strftime('%a %b %d %H:%M:%S %z %Y'))
        self.source = sources.snapshot(self.native, NOW)
        self.answer = {'text': 'The progress bar filed its work early.', 'event_key': 'early progress bar'}
        self.published = SimpleNamespace(id='501', user=self.user, full_text=self.answer['text'], in_reply_to='101')
        self.connection = SimpleNamespace(get_user_by_screen_name=AsyncMock(return_value=self.user),
            create_tweet=AsyncMock(return_value=self.published),
            get_tweet_by_id=AsyncMock(side_effect=lambda ident: self.published if ident == '501' else self.native),
            search_tweet=AsyncMock(return_value=[]))
        start = half_hour_start(NOW)
        self.slot = {'slot': '2026-11-18:actions-discovery:24:0', 'kind': 'decision', 'allowed_actions': ['reply'],
                     'discovery_reply': True, 'scheduled_at': start.isoformat(),
                     'expires_at': (start + timedelta(minutes=30)).isoformat()}
        self.enterContext(patch.object(editorial, 'utcnow', return_value=NOW))
        self.enterContext(patch.object(editorial, 'save_and_push'))
        self.enterContext(patch.object(editorial.Memory, 'connect'))
        self.discover = self.enterContext(patch.object(editorial.sources, 'discover', new=AsyncMock(return_value=[self.source])))
        self.enterContext(patch.object(editorial, 'summary'))
        self.enterContext(patch.object(editorial, 'stage'))

    async def run_reply(self, state=None):
        plan = {'action': 'reply', 'source_id': '101', 'angle': 'Carry the premature progress bar joke forward.'}
        with patch.object(personality, 'complete', return_value=plan), \
                patch.object(reply_writer, 'generate', return_value=self.answer) as writer:
            await editorial.decide(state or self.state, self.slot, self.connection, self.user, incoming=[])
        return writer

    async def test_discovered_original_reaches_writer_and_verified_delivery_without_invitation(self):
        writer = await self.run_reply()
        self.assertEqual(writer.call_args.args[0]['origin'], 'discovery')
        self.assertEqual(writer.call_args.args[0]['text'], self.native.full_text)
        self.connection.create_tweet.assert_awaited_once_with(text=self.answer['text'], reply_to='101')
        self.assertEqual([call.args[0] for call in self.connection.get_tweet_by_id.await_args_list], ['101', '101', '501'])
        self.assertEqual(self.state['history'][0]['source']['id'], '101')
        self.assertIn('verified_at', self.state['history'][0])
        self.assertIn('101', self.state['used_source_ids'])
        self.assertIn(reply_writer.fingerprint(self.answer['text']), self.state['used_texts'])
        self.assertIsNone(self.state['outbox'])
        with patch.object(personality, 'complete') as model:
            await editorial.decide(self.state, self.slot, self.connection, self.user, incoming=[])
            model.assert_not_called()
        self.connection.create_tweet.assert_awaited_once()

    async def test_opt_out_prior_contact_used_alias_and_rejection_are_not_reoffered(self):
        for changes in (
            {'mention_preferences': {'2001': {'intent': 'stop'}}},
            {'history': [{'kind': 'reply', 'tweet_id': '500', 'source': self.source, 'posted_at': NOW.isoformat()}]},
            {'used_source_ids': ['101']},
            {'reply_rejections': {editorial.rejection_key(self.source): {'until': (NOW + timedelta(hours=1)).isoformat()}}},
        ):
            with self.subTest(changes=changes), patch.object(personality, 'complete') as model:
                state = {**empty(), **changes}
                await editorial.decide(state, self.slot, self.connection, self.user, incoming=[])
                model.assert_not_called()
        self.connection.create_tweet.assert_not_awaited()

    async def test_refreshed_source_below_reach_or_changed_text_cannot_publish(self):
        for native in (tweet('101', views='9999', author='builder', created_at=self.native._data['legacy']['created_at']),
                       tweet('101', text='The compiler is still waiting for the actual source files.', author='builder',
                             created_at=self.native._data['legacy']['created_at'])):
            with self.subTest(text=native.full_text):
                self.connection.get_tweet_by_id.side_effect = None
                self.connection.get_tweet_by_id.return_value = native
                writer = await self.run_reply(empty())
                writer.assert_not_called()
        self.connection.create_tweet.assert_not_awaited()

    async def test_shared_reply_cap_and_unresolved_outbox_stop_discovery(self):
        for changes in ({'outbox': {'kind': 'reply'}}, {'history': [
                {'kind': 'reply', 'tweet_id': str(i + 700), 'source': {'author_id': str(i)},
                 'posted_at': NOW.isoformat(), 'verified_at': NOW.isoformat()} for i in range(2)]}):
            with self.subTest(changes=changes), patch.object(editorial, 'process_inbox', new_callable=AsyncMock), \
                    patch.object(editorial, 'decide', new_callable=AsyncMock) as decide:
                state = {**empty(), 'actions_inbox_since': NOW.isoformat(), **changes}
                await editorial.scheduled_wake(state, self.connection, NOW)
                self.assertFalse(any(call.args[1].get('discovery_reply') for call in decide.await_args_list))
        self.connection.create_tweet.assert_not_awaited()

    async def test_model_silence_closes_discovery_window_without_filling_quota(self):
        with patch.object(editorial, 'process_inbox', new_callable=AsyncMock), \
                patch.object(personality, 'complete', return_value={'action': 'silent', 'source_id': '', 'angle': ''}):
            await editorial.scheduled_wake(self.state, self.connection, NOW)
            first = self.discover.await_count
            await editorial.scheduled_wake(self.state, self.connection, NOW)
            self.assertEqual(self.discover.await_count, first)
        self.connection.create_tweet.assert_not_awaited()

    async def test_one_scheduled_batch_publishes_only_two_distinct_replies_once(self):
        other = tweet('102', author='other_dev', text='The compiler warning arrived after the build success notification.',
                      created_at=self.native._data['legacy']['created_at'])
        other.user.id = '2002'
        self.discover.return_value = [self.source, sources.snapshot(other, NOW)]
        posts = {'101': self.native, '102': other}
        async def send(**kwargs):
            ident = str(500 + self.connection.create_tweet.await_count)
            post = SimpleNamespace(id=ident, user=self.user, full_text=kwargs['text'], in_reply_to=kwargs.get('reply_to'))
            posts[ident] = post
            return post
        def choose(prompt, data, **kwargs):
            if 'reply' in data['allowed_actions']:
                return {'action': 'reply', 'source_id': data['allowed_reply_ids'][0], 'angle': 'Follow the timing mismatch.'}
            return {'action': 'original', 'source_id': '', 'angle': 'A quiet status light deserves credit.'}
        self.connection.get_tweet_by_id.side_effect = posts.__getitem__
        self.connection.create_tweet.side_effect = send
        with patch.object(editorial, 'process_inbox', new_callable=AsyncMock), \
                patch.object(personality, 'complete', side_effect=choose), \
                patch.object(reply_writer, 'generate', side_effect=lambda source, *args, **kwargs:
                             {'text': 'A different timing joke ' + source['id'], 'event_key': 'timing ' + source['id']}), \
                patch.object(personality, 'original', return_value={'text': 'A quiet status light earned its evening off.',
                                                                   'event_key': 'quiet status light'}) as original:
            await editorial.scheduled_wake(self.state, self.connection, NOW)
            await editorial.scheduled_wake(self.state, self.connection, NOW)
        original.assert_not_called()
        self.assertEqual([row['kind'] for row in self.state['history']], ['reply', 'reply'])
        self.assertEqual([row['source']['id'] for row in self.state['history']], ['101', '102'])
        self.assertEqual(self.connection.create_tweet.await_count, 2)

    async def test_final_refresh_preserves_native_thread_reply_ceiling(self):
        with patch.object(editorial.mentions, 'conversation_reply_ids', new=AsyncMock(return_value={'601', '602'})):
            writer = await self.run_reply()
        writer.assert_not_called()
        self.connection.create_tweet.assert_not_awaited()


class DiscoveryPlannerTests(unittest.TestCase):
    def test_public_source_is_optional_and_fresh_invitation_keeps_priority(self):
        raw = tweet('101', author='builder', created_at=(NOW - timedelta(hours=2)).strftime('%a %b %d %H:%M:%S %z %Y'))
        source = editorial.discovery_source(sources.snapshot(raw, NOW), NOW)
        choice = {'action': 'reply', 'source_id': '101', 'angle': 'Ask about the compiler output.'}
        with patch.object(personality, 'complete', return_value=choice) as model:
            self.assertEqual(personality.choose([source], {}, {}, NOW, ['reply'], allow_discovery_replies=True), choice)
            self.assertEqual(model.call_args.args[1]['allowed_reply_ids'], ['101'])
        invite = {**source, 'id': '102', 'source_ids': ['102'], 'url': 'https://x.com/builder/status/102',
                  'origin': 'mention', 'intent': 'invite', 'text': '@qofenoai hello there'}
        with patch.object(personality, 'complete', return_value=choice), self.assertRaises(editorial.DraftError):
            personality.choose([source, invite], {}, {}, NOW, ['reply'], allow_discovery_replies=True)
