import unittest
from datetime import datetime, timedelta, timezone

from app.reply_schedule import daily_plan, next_decision, next_original, reply_ready, timestamp


DAY = datetime(2026, 11, 17, tzinfo=timezone.utc)
PLAN = [row for row in daily_plan(DAY.date().isoformat()) if row['kind'] == 'reply']
NOW = timestamp(PLAN[24]['due_at']) + timedelta(minutes=1)


def sent(kind, at, slot='previous'):
    return {'kind': kind, 'slot': slot, 'posted_at': at.isoformat()}


class AutonomousScheduleTests(unittest.TestCase):
    def test_inbox_is_immediately_eligible_after_any_publication(self):
        # Before the first standalone opportunity of the UTC day.
        now = DAY + timedelta(seconds=10)
        slot = PLAN[0]['slot']
        for kind in ('reply', 'original', 'follow'):
            with self.subTest(kind=kind):
                state = {'history': [sent(kind, now - timedelta(seconds=1), slot)],
                         'decision_slots': {slot: {'action': 'silent'}},
                         'last_attempts': {slot: {'at': now.isoformat()}},
                         'last_posted_at': now.isoformat(), 'last_completed_at': now.isoformat()}
                self.assertTrue(reply_ready(state, now))

    def test_inbox_cap_resets_at_utc_midnight_using_actual_publication_time(self):
        before = DAY - timedelta(seconds=1)
        rows = [{**sent('reply', before, str(i)), 'completed_at': DAY.isoformat()} for i in range(48)]
        local = timezone(timedelta(hours=5, minutes=30))
        self.assertFalse(reply_ready({'history': rows}, before.astimezone(local)))
        self.assertTrue(reply_ready({'history': rows}, DAY.astimezone(local)))
        self.assertTrue(reply_ready({'history': rows[:-1] + [sent('original', before)]}, before))

    def test_original_cadence_is_independent_of_inbox_activity(self):
        recent = NOW - timedelta(seconds=1)
        state = {'history': [sent('reply', recent, 'mention:123')], 'last_posted_at': recent.isoformat()}
        slot, ready = next_original(state, NOW)
        self.assertEqual((slot['slot'], slot['allowed_actions'], ready), (PLAN[24]['slot'], ['original'], NOW))
        self.assertTrue(slot['original_only'])
        state['decision_slots'] = {slot['slot']: {'action': 'silent'}}
        self.assertIsNone(next_original(state, NOW))
        self.assertTrue(reply_ready(state, NOW))

    def test_original_send_spacing_and_cap_survive_inbox_traffic_and_midnight(self):
        first = DAY - timedelta(seconds=1)
        second = first + timedelta(hours=6)
        state = {'history': [sent('follow', first), sent('reply', second - timedelta(seconds=1), 'mention:2')]}
        self.assertIsNone(next_original(state, second - timedelta(seconds=1)))
        self.assertEqual(next_original(state, second)[1], second)
        state['history'] += [sent('original', second, 'second'), sent('original', second + timedelta(hours=6), 'third')]
        self.assertIsNone(next_original(state, DAY + timedelta(hours=22)))
        self.assertTrue(reply_ready(state, DAY + timedelta(hours=22)))

    def test_pending_publication_and_provider_holds_apply_to_both_paths(self):
        for key in ('outbox', 'pending', 'pending_reply', 'verification_pending', 'follow_pending', 'pause_reason'):
            with self.subTest(key=key):
                self.assertFalse(reply_ready({key: 'pending'}, NOW))
                self.assertIsNone(next_original({key: 'pending'}, NOW))
        until = NOW + timedelta(seconds=12)
        for state in ({'x_pause_until': until.isoformat()},
                      {'ai_pause': {'until': until.isoformat(), 'reason': 'provider'}}):
            self.assertFalse(reply_ready(state, NOW))
            self.assertTrue(reply_ready(state, until))
            self.assertIsNone(next_original(state, NOW))
            self.assertEqual(next_original(state, NOW, until + timedelta(seconds=1))[1], until)

    def test_same_legacy_slot_supports_both_actions_and_can_be_consumed_by_silence(self):
        slot, ready = next_decision({}, NOW)
        self.assertEqual((ready, slot['slot'], slot['kind']), (NOW, PLAN[24]['slot'], 'decision'))
        self.assertEqual(slot['allowed_actions'], ['reply', 'original'])
        for state in ({'decision_slots': {slot['slot']: {'action': 'silent'}}},
                      {'history': [sent('reply', NOW, slot['slot'])]}):
            self.assertIsNone(next_decision(state, NOW))
            future, _ = next_decision(state, NOW, NOW + timedelta(hours=1))
            self.assertEqual(future['slot'], PLAN[25]['slot'])

    def test_any_publication_enforces_half_hour_with_verified_time_precedence(self):
        for kind in ('reply', 'original', 'follow', 'quote'):
            row = sent(kind, NOW - timedelta(minutes=24))
            row['completed_at'] = (NOW - timedelta(minutes=3)).isoformat()  # Delayed readback.
            state = {'history': [row]}
            self.assertIsNone(next_decision(state, NOW))
            slot, ready = next_decision(state, NOW, NOW + timedelta(minutes=7))
            self.assertEqual(ready, NOW + timedelta(minutes=6))
            self.assertEqual(slot['allowed_actions'], ['reply'] if kind in ('follow', 'original') else ['reply', 'original'])

    def test_original_and_legacy_follow_share_six_hour_gap_and_daily_limit(self):
        history = [sent('follow', NOW - timedelta(hours=8)), sent('original', NOW - timedelta(hours=1))]
        self.assertEqual(next_decision({'history': history}, NOW)[0]['allowed_actions'], ['reply'])
        row = sent('follow', NOW - timedelta(hours=6) + timedelta(minutes=1))
        self.assertEqual(next_decision({'history': [row]}, NOW)[0]['allowed_actions'], ['reply'])
        self.assertIn('original', next_decision({'history': [row]}, NOW + timedelta(minutes=1))[0]['allowed_actions'])

    def test_reply_limit_can_leave_only_an_original_eligible_in_the_window(self):
        replies = [sent('reply', NOW - timedelta(hours=7), str(i)) for i in range(48)]
        state = {'history': replies + [sent('follow', NOW - timedelta(hours=6) + timedelta(minutes=2))]}
        self.assertIsNone(next_decision(state, NOW))
        slot, ready = next_decision(state, NOW, NOW + timedelta(minutes=3))
        self.assertEqual(ready, NOW + timedelta(minutes=2))
        self.assertEqual(slot['allowed_actions'], ['original'])
        state['history'].append(sent('original', NOW - timedelta(hours=8)))
        self.assertIsNone(next_decision(state, NOW, DAY + timedelta(days=1)))

    def test_midnight_resets_counts_and_preserves_spacing(self):
        previous = DAY - timedelta(minutes=5)
        state = {'history': [sent('original', previous - timedelta(hours=8)), sent('follow', previous)]}
        slot, ready = next_decision(state, DAY, DAY + timedelta(minutes=30))
        self.assertEqual((slot['slot'], ready), (PLAN[0]['slot'], DAY + timedelta(minutes=25)))
        self.assertEqual(slot['allowed_actions'], ['reply'])
        self.assertIn('original', next_decision(state, DAY + timedelta(hours=6))[0]['allowed_actions'])

    def test_limits_holds_expiry_and_research_failures_apply_to_all_actions(self):
        for key in ('outbox', 'pending', 'pending_reply', 'verification_pending', 'follow_pending', 'pause_reason'):
            self.assertIsNone(next_decision({key: 'pending'}, NOW))
        until = (NOW + timedelta(minutes=12)).isoformat()
        for state in ({'x_pause_until': until}, {'ai_pause': {'until': until, 'reason': 'provider'}}):
            self.assertIsNone(next_decision(state, NOW))
            self.assertIsNone(next_decision(state, NOW, timestamp(until)))
            self.assertEqual(next_decision(state, NOW, timestamp(until) + timedelta(seconds=1))[1], timestamp(until))
        state = {'last_attempts': {PLAN[24]['slot']: {'at': NOW.isoformat()}}}
        self.assertIsNone(next_decision(state, NOW))
        self.assertEqual(next_decision(state, NOW, NOW + timedelta(minutes=11))[1], NOW + timedelta(minutes=10))
        end = DAY + timedelta(days=1) - timedelta(seconds=1)
        self.assertEqual(next_decision({}, end)[0]['slot'], PLAN[-1]['slot'])
