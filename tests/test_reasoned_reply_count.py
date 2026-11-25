import copy
import unittest
from datetime import datetime, timezone

from app.reasoned_replies import DAILY_REASONED_GOAL, reasoned_reply_count


NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


def verified_reply(tweet_id='123', **changes):
    return {
        'kind': 'reply',
        'reply_style': 'reasoned_answer',
        'tweet_id': tweet_id,
        'posted_at': '2026-11-25T10:00:00Z',
        'verified_at': '2026-11-25T10:00:05Z',
        **changes,
    }


class ReasonedReplyCountTests(unittest.TestCase):
    def test_daily_goal_and_empty_history(self):
        self.assertEqual(DAILY_REASONED_GOAL, 5)
        self.assertEqual(reasoned_reply_count({}, NOW), 0)
        self.assertEqual(reasoned_reply_count({'history': []}, NOW), 0)

    def test_distinct_verified_own_posts_count_once(self):
        first = verified_reply('123', in_reply_to_tweet_id='900')
        state = {'history': [
            first,
            dict(first),
            verified_reply('00123', in_reply_to_tweet_id='900'),
            verified_reply('124', in_reply_to_tweet_id='900'),
        ]}
        # Repeated reconciliation of one receipt does not inflate progress.
        # Identity belongs to the sent reply, not to the parent post.
        self.assertEqual(reasoned_reply_count(state, NOW), 2)

    def test_only_verified_reasoned_native_replies_in_history_count(self):
        state = {
            'history': [
                verified_reply('1'),
                verified_reply('2', kind='original'),
                verified_reply('3', kind='follow'),
                verified_reply('4', reply_style='short_reply'),
                verified_reply('5', reply_style=None),
                verified_reply('6', verified_at=None),
                verified_reply('7', posted_at=None),
                None,
                'invalid history row',
            ],
            'pending': verified_reply('10'),
            'outbox': [verified_reply('11')],
            'verification_pending': verified_reply('12'),
            'delivery_rejections': [verified_reply('13')],
            'editorial_history': [verified_reply('14')],
        }
        self.assertEqual(reasoned_reply_count(state, NOW), 1)

    def test_missing_or_invalid_receipt_timestamps_do_not_count(self):
        for field in ('posted_at', 'verified_at'):
            for value in (None, '', 'not-a-date', '2026-11-25',
                          '2026-11-25T10:00:00', 123, [], {}):
                with self.subTest(field=field, value=value):
                    row = verified_reply(**{field: value})
                    self.assertEqual(reasoned_reply_count({'history': [row]}, NOW), 0)
            row = verified_reply()
            del row[field]
            with self.subTest(field=field, missing=True):
                self.assertEqual(reasoned_reply_count({'history': [row]}, NOW), 0)

    def test_invalid_or_nonpositive_publication_ids_do_not_count(self):
        for ident in (None, '', '0', '000', '-1', '+123', '1.0', ' 123 ',
                      '123x', '١٢٣', '1' * 21, 123, True, [], {}):
            with self.subTest(tweet_id=ident):
                self.assertEqual(reasoned_reply_count({
                    'history': [verified_reply(ident)],
                }, NOW), 0)

    def test_posted_day_uses_utc_boundaries_and_offsets(self):
        state = {'history': [
            verified_reply('1', posted_at='2026-11-25T00:00:00Z'),
            verified_reply('2', posted_at='2026-11-25T23:59:59.999999Z'),
            verified_reply('3', posted_at='2026-11-26T01:00:00+05:30'),
            verified_reply('4', posted_at='2026-11-24T23:00:00-02:00'),
            verified_reply('5', posted_at='2026-11-25T01:00:00+05:30'),
            verified_reply('6', posted_at='2026-11-25T23:00:00-02:00'),
            verified_reply('7', posted_at='2026-11-26T00:00:00Z'),
        ]}
        self.assertEqual(reasoned_reply_count(state, NOW), 4)
        # A reporting clock with a local November 26 date is still November 25 UTC.
        local_now = datetime.fromisoformat('2026-11-26T01:00:00+05:30')
        self.assertEqual(reasoned_reply_count(state, local_now), 4)

    def test_delayed_verification_is_attributed_to_actual_posted_day(self):
        state = {'history': [verified_reply(
            posted_at='2026-11-24T23:59:59Z',
            verified_at='2026-11-25T00:02:00Z',
        )]}
        self.assertEqual(reasoned_reply_count(state, NOW), 0)
        previous_day = datetime(2026, 11, 24, 23, 59, 59, tzinfo=timezone.utc)
        self.assertEqual(reasoned_reply_count(state, previous_day), 1)

    def test_goal_is_not_a_count_cap_and_counting_does_not_mutate_state(self):
        state = {'history': [verified_reply(str(i)) for i in range(1, 8)]}
        before = copy.deepcopy(state)
        self.assertEqual(reasoned_reply_count(state, NOW), 7)
        self.assertEqual(state, before)

    def test_naive_reporting_clock_cannot_depend_on_host_timezone(self):
        with self.assertRaises(ValueError):
            reasoned_reply_count({'history': [verified_reply()]}, datetime(2026, 11, 25))


if __name__ == '__main__':
    unittest.main()
