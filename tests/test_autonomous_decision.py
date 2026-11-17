import copy
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from twikit.errors import NotFound

from app import editorial
from app.memory import MemoryAccessDenied, MemoryUnavailable
from app.reply_schedule import next_decision
from test_editorial import DRAFT, ORIGINAL, PLAN, SOURCE, START, empty
from test_sources import tweet


class AutonomousDecisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = START.replace(hour=1, minute=15)
        self.slot = next_decision(empty(), self.now)[0]
        self.user = SimpleNamespace(screen_name=editorial.ACCOUNT, id='999')
        self.saved = empty()
        for mocked in (
            patch.object(editorial, 'utcnow', return_value=self.now),
            patch.object(editorial, 'save_and_push', side_effect=self.persist),
            patch.object(editorial, 'summary'), patch.object(editorial, 'stage'),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def persist(self, state):
        self.saved = copy.deepcopy(state)

    def connection(self, source=SOURCE, draft=DRAFT):
        post = SimpleNamespace(id='333', user=self.user, full_text=draft['text'],
                               in_reply_to=source['id'] if source else None)
        return SimpleNamespace(create_tweet=AsyncMock(return_value=post),
                               get_tweet_by_id=AsyncMock(return_value=post),
                               get_notifications=AsyncMock(return_value=[]),
                               search_tweet=AsyncMock(return_value=[]))

    def inbox_post(self, ident, body, age_minutes=2):
        created = (self.now - timedelta(minutes=age_minutes)).strftime('%a %b %d %H:%M:%S %z %Y')
        return tweet(ident, text=body, author='CuriousDev', views='3', created_at=created,
                     entities={'urls': [], 'user_mentions': [{'screen_name': editorial.ACCOUNT, 'id_str': self.user.id}]},
                     in_reply_to_status_id_str='890', in_reply_to_user_id_str=self.user.id,
                     in_reply_to_screen_name=editorial.ACCOUNT)

    def thread_parent(self, body):
        created = (self.now - timedelta(minutes=10)).strftime('%a %b %d %H:%M:%S %z %Y')
        parent = tweet('890', text=body, author=editorial.ACCOUNT, views='120', created_at=created)
        parent.user.id = self.user.id
        return parent

    async def test_native_low_view_invitation_reaches_its_exact_parent_when_trend_search_is_unavailable(self):
        state = empty()
        invite = self.inbox_post('920', '@qofenoai how should I test retries in my job queue?')
        answer = {'text': 'Fail the first attempt. Count the side effects on retry.', 'event_key': 'queue retry side effects'}
        connection = self.connection({'id': invite.id}, answer)
        published = connection.create_tweet.return_value
        published.full_text = '@CuriousDev ' + answer['text']
        connection.get_notifications.return_value = [SimpleNamespace(tweet=invite, user=invite.user)]
        parent = self.thread_parent('A quiet job queue can be processing retries twice.')
        connection.get_tweet_by_id.side_effect = {invite.id: invite, parent.id: parent, published.id: published}.__getitem__
        # A previous conversation with this person is context, not a second
        # reply to their new invitation. The 30-minute send gap has elapsed.
        earlier = (self.now - timedelta(hours=2)).isoformat()
        state['history'] = [{'kind': 'reply', 'tweet_id': '889', 'posted_at': earlier, 'verified_at': earlier,
                             'text': 'Keep the build output.', 'source': {'id': '879', 'author_id': invite.user.id}}]
        plan = {'action': 'reply', 'source_id': invite.id, 'angle': 'Test the retry after one real failure.'}
        with patch.object(editorial.Memory, 'connect') as connect, \
                patch.object(editorial.sources, 'discover', AsyncMock(side_effect=NotFound('topic search unavailable'))), \
                patch.object(editorial.personality, 'choose', return_value=plan) as choose, \
                patch.object(editorial.reply_writer, 'generate', return_value=answer) as generate:
            await editorial.decide(state, self.slot, connection, self.user)
        connection.get_notifications.assert_awaited_once()
        self.assertEqual([s['id'] for s in choose.call_args.args[0]], [invite.id])
        self.assertEqual(generate.call_args.args[0]['origin'], 'mention')
        self.assertEqual(generate.call_args.args[0]['parent_context']['text'], parent.full_text)
        connection.create_tweet.assert_awaited_once_with(text=answer['text'], reply_to=invite.id)
        self.assertEqual(connection.get_tweet_by_id.await_args_list,
                         [call(invite.id), call(parent.id), call(invite.id), call(parent.id),
                          call(invite.id), call(parent.id), call(published.id)])
        row = state['history'][-1]
        self.assertEqual((row['source']['id'], row['source']['views']), (invite.id, 3))
        self.assertIn('verified_at', row)
        self.assertIn(invite.id, state['used_source_ids'])
        self.assertEqual(connect.return_value.sync.call_args.args[0][-1], row)

    async def test_parent_edit_after_writing_prevents_publication(self):
        state, connection = empty(), self.connection()
        invite = self.inbox_post('927', '@qofenoai how would you test that?')
        connection.get_notifications.return_value = [SimpleNamespace(tweet=invite, user=invite.user)]
        parent = self.thread_parent('Queue retries can charge for the same order twice.')
        edited = self.thread_parent('Queue retries can send an extra email after delivery.')
        parents = iter([parent, parent, edited])
        connection.get_tweet_by_id.side_effect = lambda ident: next(parents) if ident == parent.id else invite
        plan = {'action': 'reply', 'source_id': invite.id, 'angle': 'Check for a duplicate charge.'}
        with patch.object(editorial.Memory, 'connect'), \
                patch.object(editorial.sources, 'discover', AsyncMock(return_value=[])), \
                patch.object(editorial.personality, 'choose', return_value=plan), \
                patch.object(editorial.reply_writer, 'generate', return_value=DRAFT) as generate:
            await editorial.decide(state, self.slot, connection, self.user)
        self.assertEqual(generate.call_args.args[0]['parent_context']['text'], parent.full_text)
        connection.create_tweet.assert_not_awaited()
        self.assertEqual(state['history'], [])
        self.assertFalse(state.get('outbox'))

    async def test_stop_supersedes_older_mention_and_excludes_the_same_author_from_topic_search(self):
        state, connection = empty(), self.connection()
        invite = self.inbox_post('945', '@qofenoai how should I test retries in my job queue?', age_minutes=12)
        stop = self.inbox_post('946', '@qofenoai stop')
        connection.get_notifications.return_value = [SimpleNamespace(tweet=t, user=t.user) for t in (stop, invite)]
        connection.get_tweet_by_id.side_effect = {invite.id: invite, stop.id: stop}.__getitem__
        topic = {**SOURCE, 'author_id': stop.user.id, 'author': stop.user.screen_name,
                 'url': f'https://x.com/{stop.user.screen_name}/status/{SOURCE["id"]}'}
        def choose(candidates, *args):
            self.assertEqual(candidates, [])
            self.assertTrue(editorial.mentions.opted_out(self.saved, stop.user.id))
            return {'action': 'silent', 'source_id': '', 'angle': ''}
        with patch.object(editorial.Memory, 'connect'), \
                patch.object(editorial.sources, 'discover', AsyncMock(return_value=[topic])), \
                patch.object(editorial.personality, 'choose', side_effect=choose):
            await editorial.decide(state, self.slot, connection, self.user)
        connection.create_tweet.assert_not_awaited()
        self.assertEqual(state['history'], [])

    async def test_bare_tag_and_used_invitation_alias_never_enter_the_shortlist(self):
        state, connection = empty(), self.connection()
        invite = self.inbox_post('948', '@qofenoai what should I log on a retry?')
        invite._data['edit_control'] = {'edit_tweet_ids': ['940', invite.id]}
        tag = self.inbox_post('949', '@qofenoai')
        state['used_source_ids'] = ['940']
        connection.get_notifications.return_value = [SimpleNamespace(tweet=t, user=t.user) for t in (tag, invite)]
        connection.get_tweet_by_id.side_effect = {invite.id: invite, tag.id: tag}.__getitem__
        with patch.object(editorial.Memory, 'connect'), \
                patch.object(editorial.sources, 'discover', AsyncMock(return_value=[])), \
                patch.object(editorial.personality, 'choose', return_value={'action': 'silent', 'source_id': '', 'angle': ''}) as choose:
            await editorial.decide(state, self.slot, connection, self.user)
        self.assertEqual(choose.call_args.args[0], [])
        connection.create_tweet.assert_not_awaited()

    async def test_silence_consumes_one_opportunity_without_sending(self):
        state, connection = empty(), self.connection()
        opportunity = {**self.slot, 'kind': 'decision', 'allowed_actions': ['reply', 'original']}
        with patch.object(editorial.Memory, 'connect') as connect, \
             patch.object(editorial.sources, 'discover', AsyncMock(return_value=[])), \
             patch.object(editorial.personality, 'choose', return_value={'action': 'silent', 'source_id': '', 'angle': ''}):
            await editorial.decide(state, opportunity, connection, self.user)
        connect.return_value.sync.assert_called_once_with([])
        self.assertEqual(self.saved['decision_slots'][opportunity['slot']]['action'], 'silent')
        self.assertIsNone(editorial.next_decision(editorial.schedule_state(self.saved), self.now))
        self.assertEqual(self.saved['history'], [])
        connection.create_tweet.assert_not_awaited()

    async def test_planned_reply_reaches_verified_send_before_d1_remembers_it(self):
        state, connection = empty(), self.connection()
        opportunity = {**self.slot, 'kind': 'decision', 'allowed_actions': ['reply', 'original']}
        context = {'conversations': [{'author_id': SOURCE['author_id'], 'text': 'Keep the failure log.'}]}
        plan = {'action': 'reply', 'source_id': SOURCE['id'], 'angle': 'Focus on multi-package changes.'}
        other = {**SOURCE, 'id': '114', 'author_id': '888', 'author': 'OtherDev',
                 'url': 'https://x.com/OtherDev/status/114', 'source_ids': ['114'], 'urls': [],
                 'views': SOURCE['views'] + 100}
        snapshots = []
        with patch.object(editorial.Memory, 'connect') as connect, \
             patch.object(editorial.sources, 'discover', AsyncMock(return_value=[other, SOURCE])), \
             patch.object(editorial.sources, 'snapshot', return_value=SOURCE), \
             patch.object(editorial.personality, 'choose', return_value=plan) as choose, \
             patch.object(editorial.reply_writer, 'generate', return_value=DRAFT) as write:
            memory = connect.return_value
            memory.context.return_value = context
            memory.sync.side_effect = lambda rows: snapshots.append(copy.deepcopy(rows))
            await editorial.decide(state, opportunity, connection, self.user)
        self.assertEqual([row['id'] for row in memory.context.call_args.args[0]], ['114', '111'])
        self.assertEqual(choose.call_args.args[:2], (memory.context.call_args.args[0], context))
        write.assert_called_once()
        self.assertEqual(write.call_args.kwargs, {'memory': context, 'angle': plan['angle']})
        self.assertEqual(connection.get_tweet_by_id.await_args_list, [call('111'), call('111'), call('333')])
        connection.create_tweet.assert_awaited_once_with(text=DRAFT['text'], reply_to=SOURCE['id'])
        self.assertEqual(snapshots[0], [])
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[1][0]['tweet_id'], '333')
        self.assertIn('verified_at', snapshots[1][0])

    async def test_original_decision_remembers_its_confirmed_post(self):
        state, connection = empty(), self.connection(None, ORIGINAL)
        context = {'recent_own': [{'post_id': '332', 'text': 'The missing logs were the clue.'}]}
        history_snapshots = []
        with patch.object(editorial.Memory, 'connect') as connect, \
             patch.object(editorial.sources, 'discover', AsyncMock(return_value=[])), \
             patch.object(editorial.personality, 'choose', return_value=PLAN), \
             patch.object(editorial.personality, 'original', return_value=ORIGINAL) as write:
            memory = connect.return_value
            memory.sync.side_effect = lambda rows: history_snapshots.append(copy.deepcopy(rows))
            memory.context.return_value = context
            await editorial.decide(state, self.slot, connection, self.user)
        write.assert_called_once_with(PLAN, context, state, self.now)
        connection.create_tweet.assert_awaited_once_with(text=ORIGINAL['text'])
        self.assertEqual(history_snapshots[0], [])
        self.assertEqual(len(history_snapshots), 2)
        self.assertEqual(history_snapshots[1][0]['kind'], 'original')
        self.assertEqual(history_snapshots[1][0]['tweet_id'], '333')
        self.assertIn('verified_at', history_snapshots[1][0])

    async def test_forged_source_or_failed_planner_cannot_send(self):
        invalid = {'action': 'reply', 'source_id': '404', 'angle': 'A made-up selection'}
        for outcome, error in ((invalid, editorial.DraftError), (editorial.AIRequestTimeout(), editorial.AIRequestTimeout)):
            with self.subTest(outcome=outcome):
                state, connection = empty(), self.connection()
                with patch.object(editorial.Memory, 'connect'), \
                     patch.object(editorial.sources, 'discover', AsyncMock(return_value=[SOURCE])), \
                     patch.object(editorial.personality, 'choose', side_effect=[outcome]), \
                     self.assertRaises(error):
                    await editorial.decide(state, self.slot, connection, self.user)
                connection.create_tweet.assert_not_awaited()
                self.assertFalse(state.get('outbox'))

    async def test_d1_unavailable_prevents_a_public_send(self):
        state, connection = empty(), self.connection()
        opportunity = {**self.slot, 'kind': 'decision', 'allowed_actions': ['reply', 'original']}
        with patch.object(editorial.Memory, 'connect', side_effect=MemoryUnavailable('D1 unavailable')), \
             patch.object(editorial.sources, 'discover', new_callable=AsyncMock) as discover, \
             patch.object(editorial.personality, 'choose') as choose:
            with self.assertRaisesRegex(MemoryUnavailable, 'D1 unavailable'):
                await editorial.decide(state, opportunity, connection, self.user)
        discover.assert_not_awaited()
        choose.assert_not_called()
        connection.create_tweet.assert_not_awaited()

    async def test_denied_d1_uses_verified_public_history_for_the_actual_reply(self):
        previous = (self.now - timedelta(days=3)).isoformat()
        older = {**DRAFT, 'tweet_id': '332', 'kind': 'reply', 'posted_at': previous,
                 'verified_at': previous, 'text': 'Show the failure before the retry.',
                 'source': {**SOURCE, 'id': '108', 'text': 'Retrying our build hides its previous output.'}}
        plan = {'action': 'reply', 'source_id': SOURCE['id'], 'angle': 'Focus on the diff.'}
        for where in ('connect', 'sync', 'context'):
            with self.subTest(where=where):
                state, connection = empty(), self.connection()
                state['history'] = [older, {'kind': 'quote', 'text': 'unverified draft'}]
                state['used_source_ids'] = ['108']
                with patch.object(editorial.Memory, 'connect') as connect, \
                     patch.object(editorial.sources, 'discover', AsyncMock(return_value=[SOURCE])), \
                     patch.object(editorial.sources, 'snapshot', return_value=SOURCE), \
                     patch.object(editorial.personality, 'choose', return_value=plan) as choose, \
                     patch.object(editorial.reply_writer, 'generate', return_value=DRAFT) as write:
                    blocked = connect if where == 'connect' else getattr(connect.return_value, where)
                    blocked.side_effect = MemoryAccessDenied('D1 permission denied')
                    await editorial.decide(state, self.slot, connection, self.user)
                stored = choose.call_args.args[1]
                self.assertEqual([r['text'] for r in stored['conversations']], [older['text']])
                self.assertEqual(stored['people'][0]['author_id'], SOURCE['author_id'])
                self.assertNotIn('unverified draft', str(stored))
                self.assertEqual(write.call_args.kwargs['memory'], stored)
                connection.create_tweet.assert_awaited_once_with(text=DRAFT['text'], reply_to=SOURCE['id'])
                self.assertIn(SOURCE['id'], state['used_source_ids'])
                self.assertIn('108', state['used_source_ids'])
                self.assertEqual(state['history'][-1]['tweet_id'], '333')

    async def test_denied_post_send_sync_never_resends_the_verified_original(self):
        state, connection = empty(), self.connection(None, ORIGINAL)
        with patch.object(editorial.Memory, 'connect') as connect, \
             patch.object(editorial.sources, 'discover', AsyncMock(return_value=[])), \
             patch.object(editorial.personality, 'choose', return_value=PLAN), \
             patch.object(editorial.personality, 'original', return_value=ORIGINAL):
            connect.return_value.sync.side_effect = [None, MemoryAccessDenied('D1 permission denied')]
            await editorial.decide(state, self.slot, connection, self.user)
        self.assertEqual(connect.return_value.sync.call_count, 2)
        self.assertEqual(state['history'][0]['tweet_id'], '333')
        self.assertIn('verified_at', state['history'][0])
        self.assertIsNone(state['outbox'])
        self.assertIn(editorial.reply_writer.fingerprint(ORIGINAL['text']), state['used_texts'])
        self.assertIsNone(next_decision(editorial.schedule_state(state), self.now))
        connection.create_tweet.assert_awaited_once_with(text=ORIGINAL['text'])

