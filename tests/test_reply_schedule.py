import unittest
from datetime import datetime, timedelta, timezone

from app import reply_schedule as schedule


START = datetime(2026, 11, 13, tzinfo=timezone.utc)


def empty():
    return {'history': [], 'last_attempts': {}}


def record(state, slot, sent_at):
    state['history'].append({**slot, 'completed_at': sent_at.isoformat()})
    state['last_completed_at'] = sent_at.isoformat()


class ReplyScheduleTests(unittest.TestCase):
    def test_half_hour_opportunities_allow_forty_eight_replies_and_two_follow_posts(self):
        state = empty()
        for day in range(14):
            day_start = START + timedelta(days=day)
            for band in range(48):
                now = day_start + timedelta(minutes=30 * band + 1, seconds=15)
                boundary = day_start + timedelta(minutes=30 * (band + 1))
                for _ in range(3):
                    found = schedule.next_slot(state, now, boundary)
                    if not found:
                        break
                    slot, ready = found
                    sent_at = ready
                    self.assertEqual(schedule.next_slot(state, sent_at)[0]['slot'], slot['slot'])
                    if state['history']:
                        previous = schedule.timestamp(state['history'][-1].get('completed_at') or state['history'][-1]['posted_at'])
                        self.assertGreaterEqual(sent_at - previous, schedule.MIN_ACTIVITY_GAP)
                    same_kind = [row for row in state['history'] if row['kind'] == slot['kind']]
                    if same_kind:
                        previous = schedule.timestamp(same_kind[-1].get('completed_at') or same_kind[-1]['posted_at'])
                        gap = schedule.MIN_REPLY_GAP if slot['kind'] == 'reply' else schedule.MIN_FOLLOW_GAP
                        self.assertGreaterEqual(sent_at - previous, gap)
                    record(state, slot, sent_at)
                    now = sent_at
            rows = [row for row in state['history']
                    if schedule.timestamp(row.get('completed_at') or row['posted_at']).date() == day_start.date()]
            self.assertEqual(sum(row['kind'] == 'reply' for row in rows), 48, day_start)
            self.assertEqual(sum(row['kind'] == 'follow' for row in rows), 2, day_start)

    def test_dates_have_deterministic_but_different_times_and_separated_follow_slots(self):
        first = schedule.daily_plan('2026-11-13')
        self.assertEqual(first, schedule.daily_plan('2026-11-13'))
        second = schedule.daily_plan('2026-11-14')
        self.assertNotEqual([row['due_at'][11:] for row in first], [row['due_at'][11:] for row in second])
        self.assertEqual(len({row['slot'] for row in first}), 50)
        replies = [schedule.timestamp(row['due_at']) for row in first if row['kind'] == 'reply']
        self.assertEqual(len(replies), 48)
        self.assertTrue(all(right-left == timedelta(minutes=30)
                            for left, right in zip(replies, replies[1:])))
        follows = [row for row in first if row['kind'] == 'follow']
        self.assertLess(schedule.timestamp(follows[0]['due_at']).hour, 12)
        self.assertGreaterEqual(schedule.timestamp(follows[1]['due_at']).hour, 12)
        self.assertGreaterEqual(schedule.timestamp(follows[1]['due_at']) - schedule.timestamp(follows[0]['due_at']), schedule.MIN_FOLLOW_GAP)

    def test_late_wake_skips_old_reply_slots_without_catching_up(self):
        state = empty()
        now = START + timedelta(hours=23, minutes=59)
        # Mark follow prompts sent so only the current reply opportunity matters.
        for row in schedule.daily_plan(START.date().isoformat()):
            if row['kind'] == 'follow':
                record(state, row, schedule.timestamp(row['due_at']))
        slot, ready = schedule.next_slot(state, now)
        self.assertEqual(slot['slot'], '2026-11-13:reply-v1:47')
        self.assertEqual(ready, now)
        record(state, slot, now)
        self.assertIsNone(schedule.next_slot(state, now + timedelta(seconds=1)))

    def test_kind_caps_are_independent_and_use_actual_utc_send_dates(self):
        state = empty()
        now = START + timedelta(hours=10)
        previous = (now - timedelta(hours=1)).isoformat()
        state['history'] = [{'slot': f'old:{i}', 'kind': 'reply', 'posted_at': previous} for i in range(48)]
        found = schedule.next_slot(state, now)
        self.assertIsNotNone(found)
        self.assertEqual(found[0]['kind'], 'follow')
        for index in range(2):
            state['history'].append({'slot': f'follow:{index}', 'kind': 'follow', 'posted_at': previous})
        self.assertIsNone(schedule.next_slot(state, now))
        # Local November 14 at 01:00 +05:30 is still November 13 UTC.
        state['history'] = [{'slot': f'old:{i}', 'kind': 'reply', 'posted_at': '2026-11-14T01:00:00+05:30'} for i in range(48)]
        self.assertFalse(any(row[0]['kind'] == 'reply' for row in [schedule.next_slot(state, START+timedelta(hours=23))] if row))

    def test_spacing_uses_real_history_across_midnight_and_carry_over(self):
        state = empty()
        state['history'] = [{'slot': 'yesterday', 'kind': 'reply', 'posted_at': (START-timedelta(minutes=1)).isoformat()}]
        state['last_posted_at'] = (START-timedelta(hours=5)).isoformat()  # stale shortcut cannot override history
        slot, ready = schedule.next_slot(state, START, START+timedelta(minutes=30))
        self.assertEqual(slot['kind'], 'reply')
        self.assertEqual(ready, START+timedelta(minutes=29))
        state = empty()
        first_due = schedule.timestamp(schedule.daily_plan(START.date().isoformat())[0]['due_at'])
        state['last_posted_at'] = (first_due-timedelta(minutes=1)).isoformat()
        self.assertEqual(schedule.next_slot(state, first_due, first_due+timedelta(minutes=30))[1],
                         first_due+timedelta(minutes=1))

    def test_follow_post_does_not_apply_a_thirty_minute_reply_cooldown(self):
        plan = schedule.daily_plan(START.date().isoformat())
        current = [row for row in plan if row['kind'] == 'reply'][3]
        now = schedule.timestamp(current['due_at'])
        state = empty()
        state['history'] = [{'kind': 'follow', 'slot': 'old-follow',
                             'posted_at': (now-timedelta(minutes=3)).isoformat()}]
        found = schedule.next_slot(state, now)
        self.assertEqual(found, (current, now))

    def test_cooldown_can_skip_a_follow_temporarily_without_starving_a_ready_reply(self):
        plan = schedule.daily_plan(START.date().isoformat())
        follow = next(row for row in plan if row['kind'] == 'follow')
        now = schedule.timestamp(follow['due_at']) + timedelta(minutes=13)
        state = empty()
        state['last_attempts'][follow['slot']] = {'at': now.isoformat()}
        found = schedule.next_slot(state, now, now+timedelta(minutes=9))
        self.assertIsNotNone(found)
        self.assertEqual(found[0]['kind'], 'reply')
        self.assertLess(found[1], now+schedule.RECHECK_GAP)

    def test_failed_slot_cooldown_and_expiry_do_not_send_a_stale_opportunity(self):
        state = empty()
        slot = schedule.daily_plan(START.date().isoformat())[0]
        now = schedule.timestamp(slot['expires_at']) - timedelta(minutes=1)
        state['last_attempt'] = {'slot': slot['slot'], 'at': now.isoformat()}
        self.assertIsNone(schedule.next_slot(state, now))
        found = schedule.next_slot(state, now, now+timedelta(minutes=5))
        self.assertIsNotNone(found)
        self.assertNotEqual(found[0]['slot'], slot['slot'])

    def test_unresolved_publication_guards_block_all_sends(self):
        for key in ('pending', 'verification_pending', 'pause_reason'):
            state = empty()
            state[key] = {'status': 'unknown'}
            self.assertIsNone(schedule.next_slot(state, START, START+timedelta(hours=1)))
            self.assertIsNone(schedule.next_slot(state, START+timedelta(hours=10)))

    def test_immediate_bootstrap_uses_current_reply_without_waiting_or_catching_up(self):
        replies = [row for row in schedule.daily_plan(START.date().isoformat())
                  if row['kind'] == 'reply']
        current = replies[2]  # Before the first follow opportunity.
        now = schedule.timestamp(current['due_at']) + timedelta(minutes=3)
        slot, ready = schedule.next_slot(empty(), now)
        self.assertEqual(slot, current)
        self.assertEqual(ready, now)
        state = empty()
        record(state, slot, now)
        self.assertIsNone(schedule.next_slot(state, now))

    def test_windows_are_exclusive_and_do_not_force_future_opportunities(self):
        first = schedule.timestamp(schedule.daily_plan(START.date().isoformat())[0]['due_at'])
        self.assertIsNone(schedule.next_slot(empty(), START))
        self.assertIsNone(schedule.next_slot(empty(), START, first))
        self.assertIsNotNone(schedule.next_slot(empty(), START, first+timedelta(seconds=1)))

    def test_naive_timestamps_are_rejected_instead_of_using_host_timezone(self):
        with self.assertRaises(ValueError):
            schedule.next_slot(empty(), datetime(2026,11,13))
        with self.assertRaises(ValueError):
            schedule.next_slot(empty(), START, datetime(2026,11,13,1))
        with self.assertRaises(ValueError):
            schedule.next_slot({'history':[{'posted_at':'2026-11-13T00:00:00'}]}, START)


if __name__ == '__main__':
    unittest.main()
