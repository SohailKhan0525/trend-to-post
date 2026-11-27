from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from twikit.errors import RequestTimeout

from app import cli, editorial, server_ledger
from app.ai_budget import AIRequestTooLarge
from app.reply_freshness import invitation_hold
from app.reply_schedule import HALF_HOUR, SCHEDULED_TARGETS, half_hour_start, scheduled_remaining
from test_editorial import empty
from test_reply_freshness import invitation


class CadenceTests(unittest.TestCase):
    def test_full_day_has_seventy_replies_and_five_originals_with_no_extra_on_reruns(self):
        day = datetime(2026, 11, 18, tzinfo=timezone.utc)
        state = {'history': []}
        for index in range(48):
            start = day + HALF_HOUR * index
            now = start + timedelta(minutes=7)
            for kind in SCHEDULED_TARGETS:
                allowance = scheduled_remaining(state, kind, now, start)
                self.assertLessEqual(allowance, 2 if kind == 'reply' else 1)
                for _ in range(allowance):
                    state['history'].append({'kind': kind, 'posted_at': now.isoformat()})
                self.assertEqual(scheduled_remaining(state, kind, now + timedelta(minutes=2), start), 0)
        self.assertEqual(Counter(post['kind'] for post in state['history']), Counter(SCHEDULED_TARGETS))
        self.assertEqual(SCHEDULED_TARGETS, {'reply': 70, 'original': 5})
        self.assertEqual(scheduled_remaining(state, 'reply', day + timedelta(days=1), day), 0)

    def test_scheduled_window_and_opt_out_of_old_backlog(self):
        cutover = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        state = {'actions_inbox_since': cutover.isoformat()}
        child = invitation(cutover + timedelta(minutes=2))
        self.assertIsNone(invitation_hold(child, cutover + timedelta(minutes=59), state=state))
        self.assertIsNotNone(invitation_hold(child, cutover + timedelta(minutes=63), state=state))
        self.assertIsNotNone(invitation_hold(invitation(cutover - timedelta(seconds=1)), cutover, state=state))

    def test_legacy_historical_publications_count_against_caps(self):
        now = datetime(2026, 11, 18, 23, 37, tzinfo=timezone.utc)
        state = empty()
        for ident in range(40):
            state['history'].append({'tweet_id': str(ident), 'kind': 'follow', 'posted_at': now.isoformat(),
                                     'verified_at': now.isoformat()})
        self.assertEqual(scheduled_remaining(editorial.schedule_state(state), 'original', now, half_hour_start(now)), 0)



class CliModeTests(unittest.TestCase):
    def test_default_run_is_scheduled_and_legacy_original_keeps_compatibility_routing(self):
        for arguments, expected in ((['qofeno', 'run'], 'scheduled'),
                                    (['qofeno', 'run', '--mode', 'original'], 'original')):
            with self.subTest(arguments=arguments), patch('sys.argv', arguments), \
                    patch.object(cli, 'ledger_mode', return_value='git'), \
                    patch.object(server_ledger, 'connect') as connect, \
                    patch.object(editorial, 'run', new_callable=AsyncMock) as run:
                connect.return_value.active.return_value = False
                cli.main()
                run.assert_awaited_once_with(expected)


class ScheduledWakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(editorial, 'process_priority', new_callable=AsyncMock))
        self.scheduled_original = self.enterContext(patch.object(editorial, 'scheduled_movie_original', new_callable=AsyncMock))

    async def test_temporary_inbox_failure_allows_discovery_but_never_bypasses_outbox(self):
        now = datetime(2026, 11, 18, 12, 7, tzinfo=timezone.utc)
        state = {'history': [], 'actions_inbox_since': now.isoformat()}
        def fail_after_reserving(*args, **kwargs):
            state['outbox'] = {'kind': 'reply'}
            raise RequestTimeout('delivery unknown')
        with patch.object(editorial, 'utcnow', return_value=now), \
                patch.object(editorial, 'process_inbox', new_callable=AsyncMock) as inbox, \
                patch.object(editorial, 'posting_account', new_callable=AsyncMock), \
                patch.object(editorial, 'decide', new_callable=AsyncMock) as decide:
            inbox.side_effect = RequestTimeout('X search timed out')
            await editorial.scheduled_wake(state, object(), now)
            self.assertEqual([call.args[1]['allowed_actions'] for call in decide.await_args_list],
                             [['reply'], ['reply']])
            calls = decide.await_count
            inbox.side_effect = fail_after_reserving
            with self.assertRaises(RequestTimeout):
                await editorial.scheduled_wake(state, object(), now)
            self.assertEqual(decide.await_count, calls)

    async def test_priority_and_inbox_precede_original_questions_and_discovery(self):
        now = datetime(2026, 11, 21, 9, 7, tzinfo=timezone.utc)
        state = {**empty(), 'actions_inbox_since': now.isoformat()}
        order = []
        self.scheduled_original.side_effect = lambda *args: order.append('question')
        async def decide(state, slot, *args, **kwargs):
            order.append(slot['allowed_actions'][0])
        with patch.object(editorial, 'utcnow', return_value=now), \
                patch.object(editorial, 'process_priority', new=AsyncMock(side_effect=lambda *a, **k: order.append('priority'))), \
                patch.object(editorial, 'process_inbox', new=AsyncMock(side_effect=lambda *a, **k: order.append('inbox'))), \
                patch.object(editorial, 'posting_account', new_callable=AsyncMock), \
                patch.object(editorial, 'decide', side_effect=decide):
            await editorial.scheduled_wake(state, object(), now)
        self.assertEqual(order, ['priority', 'inbox', 'question', 'reply', 'reply'])

    async def test_historical_originals_and_follows_do_not_reduce_the_reply_allowance(self):
        now = datetime(2026, 11, 21, 9, 7, tzinfo=timezone.utc)
        recent = (now - timedelta(minutes=40)).isoformat()
        for kind in ('original', 'follow'):
            state = {**empty(), 'actions_inbox_since': now.isoformat(), 'history': [
                {'kind': kind, 'tweet_id': '700', 'posted_at': recent, 'verified_at': recent}]}
            with self.subTest(kind=kind), patch.object(editorial, 'utcnow', return_value=now), \
                    patch.object(editorial, 'process_inbox', new_callable=AsyncMock), \
                    patch.object(editorial, 'posting_account', new_callable=AsyncMock), \
                    patch.object(editorial, 'decide', new_callable=AsyncMock) as decide:
                await editorial.scheduled_wake(state, object(), now)
                self.assertEqual([call.args[1]['allowed_actions'] for call in decide.await_args_list],
                                 [['reply'], ['reply']])

    async def test_original_history_and_old_silence_cannot_give_the_reply_planner_an_original_slot(self):
        now = datetime(2026, 11, 21, 23, 37, tzinfo=timezone.utc)
        prior = (now - timedelta(hours=2)).isoformat()
        for changes in (
                {'history': [{'kind': 'original', 'tweet_id': str(i), 'posted_at': prior,
                              'verified_at': prior} for i in range(40)]},
                {'decision_slots': {'2026-11-21:actions-original:47': {'action': 'silent'}}},
                {'outbox': {'kind': 'original'}},
        ):
            state = {**empty(), 'actions_inbox_since': now.isoformat(), **changes}
            with self.subTest(changes=changes), patch.object(editorial, 'utcnow', return_value=now), \
                    patch.object(editorial, 'process_inbox', new_callable=AsyncMock), \
                    patch.object(editorial, 'posting_account', new_callable=AsyncMock), \
                    patch.object(editorial, 'decide', new_callable=AsyncMock) as decide:
                await editorial.scheduled_wake(state, object(), now)
                self.assertFalse(any(call.args[1]['allowed_actions'] == ['original'] for call in decide.await_args_list))

    async def test_reply_budget_deferral_reports_headroom_without_a_fallback_in_the_reply_planner(self):
        now = datetime(2026, 11, 21, 9, 7, tzinfo=timezone.utc)
        state = {**empty(), 'actions_inbox_since': now.isoformat()}
        failure = AIRequestTooLarge('Do not log provider or secret text', now + timedelta(hours=1), 123, 42)
        with patch.object(editorial, 'utcnow', return_value=now), \
                patch.object(editorial, 'process_inbox', new_callable=AsyncMock), \
                patch.object(editorial, 'posting_account', new_callable=AsyncMock), \
                patch.object(editorial, 'save_and_push'), patch.object(editorial, 'summary') as summary, \
                patch.object(editorial, 'decide', new=AsyncMock(side_effect=failure)) as decide:
            await editorial.scheduled_wake(state, object(), now)
        self.assertEqual([call.args[1]['allowed_actions'] for call in decide.await_args_list], [['reply']])
        messages = [call.args[0] for call in summary.call_args_list]
        self.assertEqual(len(messages), 1)
        self.assertTrue(all('123 estimated neurons, 42 available this UTC hour' in message for message in messages))
        self.assertTrue(all('secret' not in message for message in messages))
        self.assertEqual(len(state['last_attempts']), 1)

    async def test_empty_reply_search_does_not_create_an_extra_original(self):
        now = datetime(2026, 11, 18, 12, 7, tzinfo=timezone.utc)
        state = empty()
        user = SimpleNamespace(id='999', screen_name=editorial.ACCOUNT)
        text = 'The build passed. The laptop fan would like a second opinion.'
        post = SimpleNamespace(id='601', full_text=text, user=user, in_reply_to=None)
        connection = SimpleNamespace(get_user_by_screen_name=AsyncMock(return_value=user),
                                     create_tweet=AsyncMock(return_value=post), get_tweet_by_id=AsyncMock(return_value=post))
        for name, options in (
            ('load', {'return_value': state}), ('utcnow', {'return_value': now}),
            ('client', {'return_value': connection}), ('save_and_push', {}),
            ('read_inbox', {'new_callable': AsyncMock, 'return_value': []}),
        ):
            self.enterContext(patch.object(editorial, name, **options))
        self.enterContext(patch.object(editorial.Memory, 'connect'))
        self.enterContext(patch.object(editorial.sources, 'discover', new=AsyncMock(return_value=[])))
        self.enterContext(patch.object(editorial.personality, 'choose', side_effect=lambda rows, memory, state, clock, actions, **options:
                                     {'action': 'original', 'angle': 'fan'} if 'original' in actions else {'action': 'silent'}))
        original = self.enterContext(patch.object(editorial.personality, 'original'))
        await editorial.run('scheduled')
        await editorial.run('scheduled')
        connection.create_tweet.assert_not_awaited()
        original.assert_not_called()
        self.assertEqual(state['history'], [])
        self.assertEqual(state['actions_inbox_since'], (now - timedelta(minutes=5)).isoformat())
