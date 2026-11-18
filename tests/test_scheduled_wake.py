from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from twikit.errors import RequestTimeout

from app import editorial
from app.reply_freshness import invitation_hold
from app.reply_schedule import HALF_HOUR, SCHEDULED_TARGETS, half_hour_start, scheduled_remaining
from test_editorial import empty
from test_reply_freshness import invitation


class CadenceTests(unittest.TestCase):
    def test_full_day_hits_both_targets_with_no_extra_on_reruns(self):
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
        self.assertEqual(Counter(post['kind'] for post in state['history']), SCHEDULED_TARGETS)
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


class ScheduledWakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(editorial, 'process_priority', new_callable=AsyncMock))

    async def test_temporary_inbox_failure_does_not_drop_due_original_or_bypass_outbox(self):
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
                             [['reply'], ['reply'], ['original']])
            calls = decide.await_count
            inbox.side_effect = fail_after_reserving
            with self.assertRaises(RequestTimeout):
                await editorial.scheduled_wake(state, object(), now)
            self.assertEqual(decide.await_count, calls)

    async def test_no_invitation_still_publishes_one_original_across_retries(self):
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
        self.enterContext(patch.object(editorial.personality, 'original', return_value={'text': text, 'event_key': 'fan noise'}))
        await editorial.run('scheduled')
        await editorial.run('scheduled')
        connection.create_tweet.assert_awaited_once_with(text=text)
        self.assertEqual([item['tweet_id'] for item in state['history']], ['601'])
        self.assertEqual(state['actions_inbox_since'], (now - timedelta(minutes=5)).isoformat())
