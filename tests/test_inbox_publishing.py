import copy
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from twikit.errors import RequestTimeout, TooManyRequests

from app import ai_budget, editorial, mentions, personality
from app.reply_schedule import DAILY_REPLY_CAP, half_hour_start, scheduled_remaining
from test_editorial import SOURCE, START, empty


class InboxPublishingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # These historical fixtures exercise scheduling and delivery with abbreviated X IDs.
        self.enterContext(patch.object(editorial, 'fresh_invitation', return_value=True))
        self.now = START + timedelta(minutes=1)
        self.state = empty()
        self.user = SimpleNamespace(id='999', screen_name=editorial.ACCOUNT)
        self.connection = SimpleNamespace(get_user_by_screen_name=AsyncMock(return_value=self.user),
                                          search_tweet=AsyncMock(return_value=[]),
                                          get_tweet_by_id=AsyncMock(), create_tweet=AsyncMock())
        self.first = mentions.validate({**SOURCE, 'urls': []}, self.now)
        self.second = mentions.validate({**SOURCE, 'id': '112', 'source_ids': ['112'],
                                         'author': 'Builder', 'author_id': '223', 'urls': [],
                                         'url': 'https://x.com/Builder/status/112',
                                         'text': '@qofenoai why does the second build skip the cache?'}, self.now)
        self.rows = [self.first, self.second]
        for target, name, kwargs in (
            (editorial, 'utcnow', {'side_effect': lambda: self.now}),
            (editorial, 'client', {'return_value': self.connection}),
            (editorial, 'load', {'side_effect': lambda: self.state}),
            (editorial, 'summary', {}), (editorial, 'stage', {}),
            (editorial.Memory, 'connect', {}), (editorial, 'save_and_push', {}),
            (mentions, 'discover', {'new_callable': AsyncMock, 'side_effect': lambda *_a, **_k: self.rows}),
            (editorial.sources, 'discover', {'new_callable': AsyncMock}),
        ):
            mocked = patch.object(target, name, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)

    async def test_transient_pre_send_timeouts_retry_while_the_invitation_is_fresh(self):
        self.rows = [self.first]
        draft = {'text': 'One failing test tells us more.', 'event_key': 'test scope'}
        delivered = SimpleNamespace(id='505', user=self.user, full_text=draft['text'], in_reply_to=self.first['id'])
        self.connection.get_tweet_by_id.side_effect = [
            RequestTimeout('source read temporarily timed out'), self.first,
            self.first, self.first, delivered,
        ]
        self.connection.create_tweet.return_value = delivered
        with patch.object(mentions, 'snapshot', side_effect=lambda row, _now: copy.deepcopy(row)), \
                patch.object(editorial.reply_writer, 'generate', side_effect=[editorial.AIRequestTimeout(), draft]) as write:
            await editorial.run('inbox')
            self.state = copy.deepcopy(self.state)  # New process, same durable ledger.
            await editorial.run('inbox')
            write.assert_not_called()
            self.now += editorial.INBOX_RETRY_DELAY
            await editorial.run('inbox')
            write.assert_called_once()
            self.connection.create_tweet.assert_not_awaited()
            self.now += editorial.INBOX_RETRY_DELAY
            await editorial.run('inbox')
            await editorial.run('inbox')
        self.connection.create_tweet.assert_awaited_once_with(text=draft['text'], reply_to=self.first['id'])
        self.assertEqual([row['tweet_id'] for row in self.state['history']], ['505'])

    async def test_transient_failures_stop_after_a_bounded_number_of_attempts(self):
        self.rows = [self.first]
        self.connection.get_tweet_by_id.side_effect = RequestTimeout('unavailable')
        with patch.object(editorial.reply_writer, 'generate') as write:
            for _ in range(editorial.MAX_INBOX_ATTEMPTS + 2):
                await editorial.run('inbox')
                self.now += editorial.INBOX_RETRY_DELAY
        self.assertEqual(self.connection.get_tweet_by_id.await_count, editorial.MAX_INBOX_ATTEMPTS)
        write.assert_not_called()
        self.connection.create_tweet.assert_not_awaited()

    async def test_two_fresh_invitations_after_original_send_without_any_clock_advance(self):
        at = self.now.isoformat()
        self.state['editorial_history'].append({'kind': 'original', 'completed_at': at, 'slot': 'previous-original'})
        self.state['last_posted_at'] = at
        posts = {row['id']: row for row in self.rows}
        self.connection.get_tweet_by_id.side_effect = posts.__getitem__

        def publish(text, reply_to):
            ident = str(1000 + int(reply_to))
            posts[ident] = SimpleNamespace(id=ident, user=self.user, full_text=text, in_reply_to=reply_to)
            return posts[ident]

        self.connection.create_tweet.side_effect = publish

        def write(row, *_args, **_kwargs):
            return {'text': 'A cache miss for ' + row['author'], 'event_key': 'caching ' + row['id']}

        with patch.object(mentions, 'snapshot', side_effect=lambda row, _now: copy.deepcopy(row)), \
                patch.object(personality, 'choose') as planner, \
                patch.object(editorial.reply_writer, 'generate', side_effect=write):
            await editorial.run('inbox')
            self.assertEqual({row['source']['id'] for row in self.state['history']}, {'111', '112'})
            self.assertTrue(all(row['verified_at'] == at for row in self.state['history']))
            self.rows.append({**self.first, 'id': '113', 'source_ids': ['113', '111'],
                              'text': '@qofenoai any follow-up on this edited post?'})
            await editorial.run('inbox')
        planner.assert_not_called()
        self.assertEqual(self.connection.create_tweet.await_count, 2)
        self.connection.get_user_by_screen_name.assert_awaited_once()
        editorial.sources.discover.assert_not_awaited()
        self.assertTrue(mentions.discover.await_args.kwargs['poll'])

    async def test_silence_survives_poll_restarts_new_content_still_reaches_direct_review(self):
        self.rows = [self.first]
        with patch.object(personality, 'direct_reply', return_value={'action': 'silent', 'source_id': '', 'angle': ''}) as review:
            await editorial.run('inbox')
            self.state = copy.deepcopy(self.state)
            self.rows = [{**self.first, 'views': self.first['views'] + 1000}, self.second]
            await editorial.run('inbox')
            await editorial.run('inbox')
            self.rows = [{**self.first, 'text': self.first['text'] + ' Especially with a shared cache.'}]
            await editorial.run('inbox')
        self.assertEqual(review.call_count, 3)
        self.assertEqual(len(self.state['decision_slots']), 3)
        self.connection.create_tweet.assert_not_awaited()

    async def test_quiet_or_held_inbox_never_touches_ai_or_writes_ledger(self):
        self.rows = []
        await editorial.run('inbox')
        mentions.discover.assert_awaited_once()
        self.state['editorial_history'] = [{'kind': 'reply', 'slot': str(number), 'completed_at': self.now.isoformat()}
                                           for number in range(DAILY_REPLY_CAP)]
        self.rows = [self.first]
        await editorial.run('inbox')
        mentions.discover.assert_awaited_once()
        editorial.save_and_push.assert_not_called()
        editorial.Memory.connect.assert_not_called()
        self.connection.get_user_by_screen_name.assert_not_awaited()

    async def test_fresh_mention_can_fund_writer_and_reviewer_after_research_uses_the_hour(self):
        self.rows = [self.first]
        until = ai_budget.next_hour(self.now)
        self.state['ai_usage'] = {'day': self.now.date().isoformat(), 'hour': self.now.strftime('%Y-%m-%dT%H'),
                                  'neurons_reserved': 900, 'hour_neurons_reserved': 349, 'requests': 14}
        self.state['reply_rejections'] = {editorial.rejection_key(self.first): {
            'until': until.isoformat(), 'reason': 'request needs 81 estimated neurons, 51 available this hour'}}
        delivery = SimpleNamespace(id='505', user=self.user, full_text='Cache miss confirmed.', in_reply_to=self.first['id'])
        self.connection.get_tweet_by_id.side_effect = [self.first, self.first, delivery]
        self.connection.create_tweet.return_value = delivery

        def compose(*args, **kwargs):
            budget = ai_budget.ACTIVE.get()
            for _ in ('writer', 'reviewer'):
                budget.reserve('fixture', {})
            return {'text': delivery.full_text, 'event_key': 'cache miss'}

        with patch.object(mentions, 'snapshot', side_effect=lambda row, now: copy.deepcopy(row)), \
                patch.object(ai_budget, 'estimate_reservation', return_value=81), \
                patch.object(editorial.reply_writer, 'generate', side_effect=compose):
            await editorial.run('inbox')
        self.connection.create_tweet.assert_awaited_once()
        self.assertEqual(self.state['ai_usage']['neurons_reserved'], 1062)
        self.assertEqual(self.state['ai_usage']['hour_neurons_reserved'], 511)
        self.assertEqual(self.state['history'][-1]['tweet_id'], '505')

        self.state['ai_pause'] = {'reason': ai_budget.LOCAL_HOURLY_PAUSE, 'until': until.isoformat()}
        self.assertEqual(scheduled_remaining(editorial.schedule_state(self.state), 'original', self.now,
                                             half_hour_start(self.now)), 0)

    async def test_provider_reset_controls_next_read_and_survives_restart(self):
        reset = self.now + timedelta(minutes=3)
        mentions.discover.side_effect = TooManyRequests('limit', headers={'x-rate-limit-reset': str(int(reset.timestamp()))})
        await editorial.run('inbox')
        self.assertEqual(self.state['x_pause_until'], reset.isoformat())
        await editorial.run('inbox')
        mentions.discover.assert_awaited_once()
        editorial.Memory.connect.assert_not_called()

    async def test_quota_rechecked_after_writing_before_reserving(self):
        slot = {**editorial.inbox_opportunity(self.first), 'kind': 'reply'}
        self.state['editorial_history'] = [{'kind': 'reply', 'slot': str(number), 'completed_at': self.now.isoformat()}
                                           for number in range(DAILY_REPLY_CAP)]
        await editorial.send_once(self.state, slot, {'text': 'test draft', 'event_key': 'test'},
                                  self.connection, self.user, self.first)
        self.assertFalse(self.state.get('outbox'))
        self.assertEqual(self.state['used_source_ids'], [])
        self.connection.create_tweet.assert_not_awaited()
