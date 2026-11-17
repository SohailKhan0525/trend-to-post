import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from twikit.utils import Result

from app import editorial
from app.reply_schedule import daily_plan, timestamp


START = datetime(2026, 11, 13, tzinfo=timezone.utc)
SOURCE = {
    'id': '111', 'author': 'Acme', 'author_id': '222',
    'text': '@qofenoai how would you test our editor across a whole repository?',
    'origin': 'mention', 'intent': 'invite', 'parent_id': None, 'parent_author': None,
    'created_at': START.isoformat(), 'views': 12345,
    'url': 'https://x.com/Acme/status/111',
    'urls': ['https://acme.example/blog/editor'], 'source_ids': ['111', '110'],
}
DRAFT = {'text': 'Does it keep the explanation useful when the diff touches several packages?',
         'event_key': 'acme-editor-explanations'}
ORIGINAL = {'text': 'A silent build feels longest just before the first error.',
            'event_key': 'silent builds before first errors'}
PLAN = {'action': 'original', 'source_id': '', 'angle': 'The silence before a failed build.'}
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
                               full_text={'reply': DRAFT, 'original': ORIGINAL, 'follow': FOLLOW}[kind]['text'],
                               in_reply_to='111' if kind == 'reply' else None)
        return SimpleNamespace(user=AsyncMock(return_value=self.user),
                               get_user_by_screen_name=AsyncMock(return_value=self.user),
                               get_user_tweets=AsyncMock(return_value=Result([])),
                               search_tweet=AsyncMock(return_value=[]),
                               http=SimpleNamespace(cookies=SimpleNamespace(jar=[])),
                               create_tweet=AsyncMock(return_value=post),
                               get_tweet_by_id=AsyncMock(return_value=post))

    def pending(self, tweet_id=None):
        row = {**DRAFT, 'slot': self.slot['slot'], 'kind': 'reply', 'source': SOURCE,
               'author_id': self.user.id, 'reserved_at': self.now.isoformat()}
        if tweet_id:
            row['tweet_id'] = tweet_id
        return row

    async def test_legacy_numeric_rejection_retries_once_per_writer_version(self):
        source = {**SOURCE, 'text': '@qofenoai three hours on one character. What can you do with that?'}
        state, connection = empty(), self.connection()
        key = editorial.rejection_key(source)
        state['reply_rejections'] = {key: {'until': (self.now + timedelta(hours=5)).isoformat()}}
        with patch.object(editorial.mentions, 'snapshot', return_value=source), \
                patch.object(editorial.reply_writer, 'generate', side_effect=editorial.reply_writer.RecoverableDraftError('bad count')) as write:
            await editorial.publish_reply(state, self.slot, connection, self.user, selected=source)
            self.assertEqual(self.saves[0]['reply_rejections'][key]['retried_version'],
                             editorial.reply_writer.EDITORIAL_POLICY_VERSION)
            await editorial.publish_reply(state, self.slot, connection, self.user, selected=source)
            self.assertEqual(write.call_count, 1)
            with patch.object(editorial.reply_writer, 'EDITORIAL_POLICY_VERSION', 'newer-policy'):
                await editorial.publish_reply(state, self.slot, connection, self.user, selected=source)
                await editorial.publish_reply(state, self.slot, connection, self.user, selected=source)
                self.assertEqual(write.call_count, 2)
        connection.create_tweet.assert_not_awaited()
        self.assertFalse(state.get('outbox'))

    def test_recoverable_retry_delay_preserves_hard_holds_and_unrelated_legacy_entries(self):
        source = {**SOURCE, 'text': '@qofenoai explain the five hours in that chart?'}
        five_hours = (self.now + timedelta(hours=5)).isoformat()
        hold = editorial.draft_format_hold(source, self.now)
        self.assertTrue(editorial.reply_rejection_active(source, hold, self.now))
        self.assertFalse(editorial.reply_rejection_active(source, hold, self.now + timedelta(minutes=10)))
        for candidate, entry in (
            (source, {'kind': 'review', 'until': five_hours}),
            (source, {'reason': 'Cloudflare budget', 'until': five_hours}),
            (source, {'until': (self.now + timedelta(minutes=90)).isoformat()}),
            (SOURCE, {'until': five_hours}),
            ({**source, 'origin': 'discovery'}, {'until': five_hours}),
            (source, {**hold, 'policy_version': 'old', 'not_before': five_hours}),
        ):
            with self.subTest(entry=entry):
                self.assertTrue(editorial.reply_rejection_active(candidate, entry, self.now))

    async def test_reply_refreshes_source_twice_and_uses_native_reply_transport(self):
        state, connection = empty(), self.connection()

        async def send(**kwargs):
            self.assertEqual(kwargs, {'text': DRAFT['text'], 'reply_to': SOURCE['id']})
            self.assertEqual(self.saved['outbox']['source'], SOURCE)
            self.assertNotIn('tweet_id', self.saved['outbox'])
            self.assertEqual(self.saved['history'], [])
            return SimpleNamespace(id='333')

        connection.create_tweet.side_effect = send
        with patch.object(editorial, 'read_inbox', AsyncMock(return_value=[SOURCE])), \
             patch.object(editorial.mentions, 'snapshot', return_value=SOURCE) as snapshot, \
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
        for changed_at in ('first', 'second', 'unavailable'):
            with self.subTest(changed_at=changed_at):
                state, connection = empty(), self.connection()
                changed = {**SOURCE, 'text': 'Edited source text'}
                results = ([changed] if changed_at == 'first' else [SOURCE, changed])
                if changed_at == 'unavailable':
                    results = [editorial.sources.SourceError('public source unavailable')]
                with patch.object(editorial, 'read_inbox', AsyncMock(return_value=[SOURCE])), \
                     patch.object(editorial.mentions, 'snapshot', side_effect=results), \
                     patch.object(editorial.reply_writer, 'generate', return_value=DRAFT) as generate:
                    await editorial.publish_reply(state, self.slot, connection, self.user)
                connection.create_tweet.assert_not_awaited()
                self.assertEqual(self.saved['history'], [])
                self.assertEqual(self.saved['used_source_ids'], [])
                if changed_at != 'second':
                    generate.assert_not_called()

    async def test_ai_failure_defers_only_that_source_before_send(self):
        reset = self.now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        longer = {**SOURCE, 'id': '112', 'text': 'A longer observation about editor performance. ' * 8}
        shorter = {**SOURCE, 'id': '113', 'text': 'We cut editor load time in half.'}
        candidates = [SOURCE, longer, shorter]
        by_id = {row['id']: row for row in candidates}
        cases = [
            (editorial.AIRequestTimeout(), longer, self.now + timedelta(minutes=30)),
            (editorial.AIRequestTooLarge('Too large', reset, 170, 159), shorter, reset),
        ]
        for error, expected, retry_at in cases:
            with self.subTest(error=type(error).__name__):
                state, connection = empty(), self.connection()
                connection.get_tweet_by_id.side_effect = by_id.__getitem__
                with patch.object(editorial, 'read_inbox', AsyncMock(return_value=candidates)), \
                     patch.object(editorial.mentions, 'snapshot', side_effect=lambda row, now: row), \
                     patch.object(editorial.reply_writer, 'generate', side_effect=[error, DRAFT]) as generate, \
                     patch.object(editorial, 'send_once', new_callable=AsyncMock) as send:
                    await editorial.publish_reply(state, self.slot, connection, self.user)
                    self.assertEqual([call.args[0]['id'] for call in generate.call_args_list],
                                     [SOURCE['id'], expected['id']])
                    send.assert_awaited_once_with(state, self.slot, DRAFT, connection, self.user, expected)
                    deferred = self.saved['reply_rejections'][editorial.rejection_key(SOURCE)]
                    self.assertEqual(timestamp(deferred['until']), retry_at)
                    self.assertFalse(self.saved.get('ai_pause'))
                    self.assertFalse(self.saved.get('outbox'))
                    self.assertEqual(self.saved['used_source_ids'], [])
                with patch.object(editorial, 'read_inbox', AsyncMock(return_value=[SOURCE])), \
                     patch.object(editorial.reply_writer, 'generate') as generate:
                    await editorial.publish_reply(copy.deepcopy(self.saved), self.slot, connection, self.user)
                    generate.assert_not_called()

    async def test_original_generation_timeout_precedes_x_reservation(self):
        state, connection = empty(), self.connection('original')
        slot = {**self.slot, 'kind': 'original', 'allowed_actions': ['original', 'reply']}
        with patch.object(editorial.personality, 'original', side_effect=editorial.AIRequestTimeout()):
            await editorial.publish_original(state, slot, connection, self.user, PLAN, {})
        connection.create_tweet.assert_not_awaited()
        self.assertFalse(self.saved.get('outbox'))
        self.assertIn('timed out', self.saved['last_attempts'][slot['slot']]['reason'])

    async def test_used_source_alias_or_url_cannot_be_sent_again(self):
        for key, value in (('used_source_ids', '110'), ('used_urls', SOURCE['urls'][0])):
            state, connection = empty(), self.connection()
            state[key] = [value]
            await editorial.send_once(state, self.slot, DRAFT, connection, self.user, SOURCE)
            connection.create_tweet.assert_not_awaited()
            self.assertEqual(self.saved['history'], [])

    async def test_own_account_and_opted_out_author_are_excluded(self):
        state, connection = empty(), self.connection()
        state['mention_preferences'] = {SOURCE['author_id']: {'intent': 'stop'}}
        own = {**SOURCE, 'id': '444', 'author': editorial.ACCOUNT.upper(), 'author_id': '999'}
        with patch.object(editorial, 'read_inbox', AsyncMock(return_value=[own, SOURCE])), \
             patch.object(editorial.reply_writer, 'generate') as generate:
            await editorial.publish_reply(state, self.slot, connection, self.user)
        generate.assert_not_called()
        connection.get_tweet_by_id.assert_not_awaited()
        connection.create_tweet.assert_not_awaited()

    async def test_original_has_no_reply_parent_or_quote_attachment(self):
        state, connection = empty(), self.connection('original')
        slot = {**self.slot, 'kind': 'original', 'allowed_actions': ['original', 'reply']}
        memory = {'recent_own': []}
        with patch.object(editorial.personality, 'original', return_value=ORIGINAL) as write:
            await editorial.publish_original(state, slot, connection, self.user, PLAN, memory)
        write.assert_called_once_with(PLAN, memory, state, self.now)
        connection.create_tweet.assert_awaited_once_with(text=ORIGINAL['text'])
        self.assertEqual(self.saved['history'][0]['kind'], 'original')
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
             patch.object(editorial, 'decide', AsyncMock()) as publish:
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
                     patch.object(editorial, 'decide', AsyncMock()) as decision:
                    await editorial.run('push')
                decision.assert_not_awaited()
                connection.user.assert_not_awaited()
                connection.create_tweet.assert_not_awaited()

    async def test_wrong_configured_account_prevents_research_and_send(self):
        connection = self.connection()
        connection.get_user_by_screen_name.return_value = SimpleNamespace(id='666', screen_name='someone_else')
        with patch.object(editorial, 'client', return_value=connection), \
             patch.object(editorial, 'decide', AsyncMock()) as decision:
            with self.assertRaises(editorial.SessionUnavailable):
                await editorial.run('push')
        decision.assert_not_awaited()
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

    async def test_missing_id_found_on_second_page_is_persisted_before_exact_readback(self):
        for kind, feed in (('reply', 'Replies'), ('follow', 'Tweets'), ('original', 'Tweets')):
            with self.subTest(kind=kind):
                state, connection = empty(), self.connection(kind)
                pending = self.pending()
                if kind != 'reply':
                    pending.update(FOLLOW if kind == 'follow' else ORIGINAL, kind=kind)
                    del pending['source']
                state['outbox'] = pending
                post = connection.create_tweet.return_value
                post.created_at_datetime = self.now
                if kind == 'reply':
                    post.full_text = '@Acme ' + post.full_text
                old = SimpleNamespace(**{**vars(post), 'id': '330', 'created_at_datetime': self.now-timedelta(hours=2)})
                second_page = AsyncMock(return_value=Result([post, post]))
                connection.get_user_tweets.return_value = Result([old], second_page, 'cursor')
                async def read(ident):
                    self.assertEqual(self.saved['outbox']['tweet_id'], ident)
                    self.assertEqual(self.saved['history'], [])
                    return post
                connection.get_tweet_by_id.side_effect = read
                self.assertTrue(await editorial.verify_delivery(state, connection))
                connection.get_user_tweets.assert_awaited_once_with('999', feed, count=40)
                second_page.assert_awaited_once()
                connection.create_tweet.assert_not_awaited()
                self.assertEqual(state['history'][0]['tweet_id'], '333')
                self.assertTrue(await editorial.verify_delivery(state, connection))
                self.assertEqual(len(state['history']), 1)

    async def test_failed_or_ambiguous_timeline_lookup_cannot_resolve_reservation(self):
        reserved = self.now
        pending = self.pending()
        pending['send_error'] = 'CouldNotTweet'
        self.now += timedelta(days=2)
        connection = self.connection()
        post = connection.create_tweet.return_value
        post.created_at_datetime = reserved
        def changed(**kwargs):
            return SimpleNamespace(**{**vars(post), **kwargs})
        for page in (TimeoutError(), Result([]), Result([post, changed(id='334')]),
                     Result([changed(full_text='Wrong text'), changed(in_reply_to='555'),
                             changed(user=SimpleNamespace(id='555', screen_name=editorial.ACCOUNT)),
                             changed(quote=object()), changed(retweeted_tweet=object()),
                             changed(created_at_datetime=reserved+timedelta(hours=1))])):
            with self.subTest(case=type(page).__name__):
                state = empty()
                state['outbox'] = dict(pending)
                connection.get_user_tweets = AsyncMock(side_effect=[page])
                self.assertFalse(await editorial.verify_delivery(state, connection))
                self.assertEqual(state['outbox'], pending)
                self.assertFalse(state.get('delivery_rejections'))
                connection.create_tweet.assert_not_awaited()
                connection.get_tweet_by_id.assert_not_awaited()

    async def test_older_verified_timeline_anchor_retires_only_explicit_unconfirmed_error(self):
        reserved = self.now
        pending = self.pending()
        self.now += timedelta(days=2)
        prior_time = reserved - timedelta(minutes=30)
        prior = {'tweet_id': '330', 'kind': 'reply', 'posted_at': prior_time.isoformat(),
                 'verified_at': prior_time.isoformat()}
        older = SimpleNamespace(id='330', user=self.user, created_at_datetime=prior_time)
        for explicit in (False, True):
            state, connection = empty(), self.connection()
            state['used_source_ids'] = ['110', '111']
            state['outbox'] = dict(pending)
            if explicit:
                state['outbox']['send_error'] = 'CouldNotTweet'
            state['history'] = [prior]
            connection.get_user_tweets.return_value = Result([older])
            self.assertEqual(await editorial.verify_delivery(state, connection), explicit)
            self.assertEqual(state['history'], [prior])
            self.assertEqual(state['used_source_ids'], ['110', '111'])
            self.assertEqual(state['outbox'] is None, explicit)
            if explicit:
                self.assertEqual(state['delivery_rejections'][0]['outcome'], 'unconfirmed')
                self.assertEqual(state['delivery_rejections'][0]['error'], 'CouldNotTweet')
            connection.create_tweet.assert_not_awaited()

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
             patch.object(editorial, 'decide', AsyncMock()) as decision, \
             patch.object(editorial.asyncio, 'sleep', AsyncMock()) as sleep:
            await editorial.run('push')
        decision.assert_awaited_once()
        opportunity = decision.await_args.args[1]
        self.assertEqual(opportunity['slot'], self.slot['slot'])
        self.assertEqual(opportunity['kind'], 'decision')
        self.assertEqual(set(opportunity['allowed_actions']), {'reply', 'original'})
        sleep.assert_not_awaited()

    async def test_inspect_and_idle_original_wake_report_spacing_from_verified_publication(self):
        connection = self.connection()
        published = self.now
        self.saved['history'] = [{'kind': 'original', 'tweet_id': '333', 'posted_at': published.isoformat(),
                                  'verified_at': (published + timedelta(minutes=5)).isoformat()}]
        self.saved['editorial_history'] = [{**self.slot, 'kind': 'original', 'tweet_id': '333',
                                           'completed_at': (published + timedelta(minutes=5)).isoformat()}]
        self.now += timedelta(minutes=12)
        expected = (published + timedelta(hours=6)).isoformat()
        with patch.object(editorial, 'client', return_value=connection) as client, \
                patch.object(editorial, 'decide', new_callable=AsyncMock) as decision, \
                patch.object(editorial.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
            await editorial.inspect()
            self.assertIn(expected, editorial.summary.call_args.args[0])
            client.assert_not_called()
            await editorial.run('original')
            self.assertIn(expected, editorial.summary.call_args.args[0])
            decision.assert_not_awaited()
            sleep.assert_not_awaited()
        connection.create_tweet.assert_not_awaited()
        self.assertEqual(self.saves, [])

    async def test_inspect_reports_next_original_after_daily_cap(self):
        last_reply = [row for row in self.plan if row['kind'] == 'reply'][-1]
        last_send = START + timedelta(hours=23, minutes=55)
        self.now = last_send + timedelta(minutes=1)
        self.saved['editorial_history'] = [{**last_reply, 'completed_at': last_send.isoformat()}]
        follows = [row for row in self.plan if row['kind'] == 'follow']
        self.saved['editorial_history'] += [{**row, 'completed_at': row['due_at']} for row in follows]
        await editorial.inspect()
        eligible_at = timestamp(follows[-1]['due_at']) + timedelta(hours=6)
        self.assertIn(eligible_at.isoformat(), editorial.summary.call_args.args[0])

    async def test_dispatch_logs_its_research_time_once_before_sleep(self):
        due = timestamp(self.slot['due_at'])
        self.now = due - timedelta(seconds=65)
        previous = [row for row in self.plan if row['kind'] == 'reply'][1]
        self.saved['editorial_history'] = [{**previous, 'completed_at': previous['due_at']}]
        def wait_messages():
            return [call.args[0] for call in editorial.summary.call_args_list
                    if due.isoformat() in call.args[0]]

        async def sleep(seconds):
            self.assertEqual(len(wait_messages()), 1)
            self.now += timedelta(seconds=seconds)

        with patch.object(editorial, 'client', return_value=self.connection()), \
                patch.object(editorial, 'decide', new_callable=AsyncMock) as decision, \
                patch.object(editorial.asyncio, 'sleep', side_effect=sleep) as sleeper:
            await editorial.run('repository_dispatch')
        decision.assert_awaited_once()
        self.assertGreater(sleeper.await_count, 1)
        self.assertEqual(len(wait_messages()), 1)

    async def test_inspect_identifies_holds_before_reporting_a_slot(self):
        self.saved['x_pause_until'] = (self.now + timedelta(minutes=5)).isoformat()
        self.saved['ai_pause'] = {'reason': 'quota', 'until': (self.now + timedelta(minutes=12)).isoformat()}
        await editorial.inspect()
        message = editorial.summary.call_args.args[0]
        self.assertIn(self.saved['x_pause_until'], message)
        self.assertIn(self.saved['ai_pause']['until'], message)
        self.assertNotIn('research opportunity', message)
        self.saved['outbox'] = self.pending()
        await editorial.inspect()
        self.assertIn('reconciliation', editorial.summary.call_args.args[0])
        self.assertNotIn('research opportunity', editorial.summary.call_args.args[0])

    async def test_ai_pause_stops_research_and_send(self):
        state, connection = empty(), self.connection()
        state['ai_pause'] = {'until': (self.now+timedelta(hours=1)).isoformat(), 'reason': 'quota'}
        self.persist(state)
        with patch.object(editorial, 'client', return_value=connection), \
             patch.object(editorial, 'decide', AsyncMock()) as decision:
            await editorial.run('push')
        decision.assert_not_awaited()
        connection.create_tweet.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
