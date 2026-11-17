import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from app import editorial
from app.reply_schedule import next_decision
from test_editorial import DRAFT, ORIGINAL, PLAN, SOURCE, START, empty


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
                               get_tweet_by_id=AsyncMock(return_value=post))

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
        with patch.object(editorial.Memory, 'connect', side_effect=RuntimeError('D1 unavailable')), \
             patch.object(editorial.sources, 'discover', new_callable=AsyncMock) as discover, \
             patch.object(editorial.personality, 'choose') as choose:
            with self.assertRaisesRegex(RuntimeError, 'D1 unavailable'):
                await editorial.decide(state, opportunity, connection, self.user)
        discover.assert_not_awaited()
        choose.assert_not_called()
        connection.create_tweet.assert_not_awaited()

