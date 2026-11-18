import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from twikit.tweet import Tweet
from twikit.utils import Result

from app import priority, sources
from app.reply_freshness import X_EPOCH, post_created_at


NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
USER_ID = '2001'


def snowflake(at):
    return str(((at - X_EPOCH) // timedelta(milliseconds=1)) << 22)


def account(handle='zaheer0525', user_id=USER_ID, **flags):
    return SimpleNamespace(id=user_id, screen_name=handle, protected=False,
                           blocked_by=False, blocking=False, **flags)


def tweet(source_id='1001', *, created=NOW, views='7', author='zaheer0525',
          user_id=USER_ID, text='The coffee went cold while I named the project.',
          aliases=None, **legacy):
    data = {
        'rest_id': source_id,
        'legacy': {'full_text': text,
                   'created_at': created.strftime('%a %b %d %H:%M:%S %z %Y'),
                   'is_quote_status': False, 'entities': {'urls': []}, **legacy},
        'views': {'count': views, 'state': 'EnabledWithCount'},
    }
    if aliases is not None:
        data['edit_control'] = {'edit_tweet_ids': aliases}
    return Tweet(None, data, account(author, user_id))


def empty():
    return {'version': 1, 'history': [], 'editorial_history': [],
            'used_source_ids': [], 'used_urls': [], 'used_events': [],
            'used_texts': [], 'last_attempts': {}}


def connection(posts=()):
    return SimpleNamespace(
        get_user_by_screen_name=AsyncMock(return_value=account()),
        get_user_tweets=AsyncMock(return_value=Result(list(posts))),
    )


class PrioritySnapshotTests(unittest.TestCase):
    def test_exact_handle_and_low_or_missing_views(self):
        for views in ('0', '7', '9999', None):
            with self.subTest(views=views):
                source = priority.snapshot(tweet(views=views), NOW)
                self.assertEqual(source['author'], 'zaheer0525')
                self.assertEqual(source['origin'], 'priority')
                self.assertIsNone(source['parent_id'])
                self.assertEqual(source['conversation_id'], '1001')
                self.assertNotIn('intent', source)
        self.assertEqual(priority.snapshot(tweet(author='Zaheer0525'), NOW)['author'], 'Zaheer0525')
        with self.assertRaises(sources.SourceError):
            priority.snapshot(tweet(author='zaheer2505'), NOW)

    def test_short_personal_post_has_no_forced_technical_topic(self):
        self.assertEqual(priority.snapshot(tweet(text='Coffee?'), NOW)['text'], 'Coffee?')

    def test_only_original_public_posts(self):
        for fields in (
            {'in_reply_to_status_id_str': '99'},
            {'in_reply_to_user_id_str': '99'},
            {'retweeted_status_result': {'result': {'__typename': 'TweetTombstone'}}},
            {'is_quote_status': True},
            {'quoted_status_id_str': '99'},
        ):
            with self.subTest(fields=fields), self.assertRaises(sources.SourceError):
                priority.snapshot(tweet(**fields), NOW)
        for flag in ('protected', 'blocked_by', 'blocking'):
            post = tweet()
            setattr(post.user, flag, True)
            with self.subTest(flag=flag), self.assertRaises(sources.SourceError):
                priority.snapshot(post, NOW)

    def test_freshness_and_existing_topic_safety_still_apply(self):
        for post in (
            tweet(created=NOW - timedelta(hours=25)),
            tweet(created=NOW + timedelta(seconds=1)),
            tweet(text='The government announced another policy today.'),
            tweet(text='https://t.co/abc'),
        ):
            with self.subTest(post=post.id), self.assertRaises(sources.SourceError):
                priority.snapshot(post, NOW)


class PriorityDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Historical fixture IDs exercise routing; real snowflakes below retain
        # their embedded times for activation and future-edit regression tests.
        self.enterContext(patch.object(
            priority, 'post_created_at',
            side_effect=lambda ident: (NOW + timedelta(minutes=1)
                                       if len(str(ident)) < 10 else post_created_at(ident)),
        ))

    async def activate(self, state=None, posts=()):
        state = empty() if state is None else state
        client = connection(posts)
        self.assertEqual(await priority.discover(client, state, NOW), [])
        return state, client

    async def test_first_activation_records_exact_identity_and_no_historical_backlog(self):
        old = tweet(created=NOW - timedelta(minutes=1), aliases=['1000', '1001'])
        state, client = await self.activate(posts=[old])
        client.get_user_by_screen_name.assert_awaited_once_with('zaheer0525')
        client.get_user_tweets.assert_awaited_once_with(USER_ID, 'Tweets', count=40)
        saved = state[priority.STATE_KEY]
        self.assertEqual(saved['handle'], 'zaheer0525')
        self.assertEqual(saved['user_id'], USER_ID)
        self.assertEqual(saved['activated_at'], NOW.isoformat())
        self.assertTrue({'1000', '1001'} <= set(saved['baseline_ids']))
        self.assertIn('baseline_latest_at', saved)
        self.assertEqual(saved['pending'], [])
        self.assertEqual(state['used_source_ids'], [])

    async def test_new_original_is_eligible_below_discovery_view_threshold(self):
        old = tweet(created=NOW - timedelta(minutes=1))
        state, client = await self.activate(posts=[old])
        new = tweet('1002', created=NOW + timedelta(minutes=1), views='2')
        client.get_user_tweets.return_value = Result([new, old])
        rows = await priority.discover(client, state, NOW + timedelta(minutes=30))
        self.assertEqual([row['id'] for row in rows], ['1002'])
        self.assertEqual(rows[0]['views'], 2)
        self.assertEqual(rows[0]['origin'], 'priority')
        self.assertNotIn('intent', rows[0])

    async def test_saved_pending_survives_restart_and_used_identity_never_repeats(self):
        state, client = await self.activate()
        client.get_user_tweets.return_value = Result([tweet('1002', created=NOW + timedelta(minutes=1))])
        rows = await priority.discover(client, state, NOW + timedelta(minutes=30))
        self.assertEqual([row['id'] for row in rows], ['1002'])
        restarted = copy.deepcopy(state)
        client.get_user_tweets.return_value = Result([])
        rows = await priority.discover(client, restarted, NOW + timedelta(hours=1))
        self.assertEqual([row['id'] for row in rows], ['1002'])
        restarted['used_source_ids'].append('1002')
        client.get_user_tweets.return_value = Result([tweet('1002', created=NOW + timedelta(minutes=1))])
        self.assertEqual(await priority.discover(client, restarted, NOW + timedelta(hours=2)), [])
        self.assertEqual(restarted[priority.STATE_KEY]['pending'], [])

    async def test_old_baseline_edit_is_not_a_new_post(self):
        old = tweet('1001', created=NOW - timedelta(minutes=1))
        state, client = await self.activate(posts=[old])
        edited = tweet('1002', created=NOW + timedelta(minutes=1), aliases=['1001', '1002'])
        client.get_user_tweets.return_value = Result([edited])
        self.assertEqual(await priority.discover(client, state, NOW + timedelta(minutes=30)), [])

    async def test_unbaselined_original_edited_after_activation_is_still_old(self):
        state, client = await self.activate()
        original_id = snowflake(NOW - timedelta(minutes=1))
        edited_at = NOW + timedelta(minutes=1)
        edited_id = snowflake(edited_at)
        self.assertNotIn(original_id, state[priority.STATE_KEY]['baseline_ids'])
        edited = tweet(edited_id, created=edited_at, aliases=[original_id, edited_id])
        check_at = NOW + timedelta(minutes=30)
        candidate = priority.snapshot(edited, check_at)
        with self.assertRaises(sources.SourceError):
            priority.validate(candidate, check_at, state)
        client.get_user_tweets.return_value = Result([edited])
        self.assertEqual(await priority.discover(client, state, check_at), [])

    async def test_future_edit_identity_is_rejected_even_with_current_native_timestamp(self):
        state, client = await self.activate()
        created = NOW + timedelta(minutes=1)
        current_id = snowflake(created)
        future_id = snowflake(NOW + timedelta(minutes=31))
        check_at = NOW + timedelta(minutes=30)
        post = tweet(current_id, created=created, aliases=[current_id, future_id])
        candidate = priority.snapshot(post, check_at)
        with self.assertRaises(sources.SourceError):
            priority.validate(candidate, check_at, state)
        client.get_user_tweets.return_value = Result([post])
        self.assertEqual(await priority.discover(client, state, check_at), [])

    async def test_real_snowflakes_for_new_original_and_later_edit_remain_eligible(self):
        state, client = await self.activate()
        original_id = snowflake(NOW + timedelta(minutes=1))
        edited_at = NOW + timedelta(minutes=10)
        edited_id = snowflake(edited_at)
        edited = tweet(edited_id, created=edited_at, aliases=[original_id, edited_id])
        client.get_user_tweets.return_value = Result([edited])
        rows = await priority.discover(client, state, NOW + timedelta(minutes=30))
        self.assertEqual([row['id'] for row in rows], [edited_id])
        self.assertEqual(set(rows[0]['source_ids']), {original_id, edited_id})

    async def test_pre_activation_post_not_in_first_page_does_not_open_backlog(self):
        state, client = await self.activate()
        client.get_user_tweets.return_value = Result([tweet(created=NOW - timedelta(minutes=1))])
        self.assertEqual(await priority.discover(client, state, NOW + timedelta(minutes=30)), [])

    async def test_reply_repost_and_wrong_author_are_not_queued(self):
        state, client = await self.activate()
        created = NOW + timedelta(minutes=1)
        client.get_user_tweets.return_value = Result([
            tweet('1002', created=created, in_reply_to_status_id_str='99'),
            tweet('1003', created=created, retweeted_status_result={'result': {'__typename': 'TweetTombstone'}}),
            tweet('1004', created=created, author='zaheer2505'),
            tweet('1005', created=created, user_id='9999'),
        ])
        self.assertEqual(await priority.discover(client, state, NOW + timedelta(minutes=30)), [])

    async def test_lookup_mismatch_cannot_activate_another_account(self):
        for user in (account('zaheer2505'), account(user_id='not-an-id')):
            state = empty()
            before = copy.deepcopy(state)
            client = connection()
            client.get_user_by_screen_name.return_value = user
            with self.subTest(user=user.screen_name), self.assertRaises(sources.SourceError):
                await priority.discover(client, state, NOW)
            self.assertEqual(state, before)
            client.get_user_tweets.assert_not_awaited()

    async def test_handle_reassignment_keeps_pinned_stable_identity(self):
        state, client = await self.activate()
        before = copy.deepcopy(state)
        client.get_user_by_screen_name.return_value = account(user_id='9999')
        with self.assertRaises(sources.SourceError):
            await priority.discover(client, state, NOW + timedelta(minutes=30))
        self.assertEqual(state, before)

    async def test_native_transport_error_does_not_reset_activation_or_pending(self):
        state, client = await self.activate()
        before = copy.deepcopy(state)
        client.get_user_tweets.side_effect = TimeoutError('temporary read failure')
        with self.assertRaises(TimeoutError):
            await priority.discover(client, state, NOW + timedelta(minutes=30))
        self.assertEqual(state, before)

    async def test_initial_transport_error_does_not_mark_activation_complete(self):
        state = empty()
        before = copy.deepcopy(state)
        client = connection()
        client.get_user_tweets.side_effect = TimeoutError('temporary read failure')
        with self.assertRaises(TimeoutError):
            await priority.discover(client, state, NOW)
        self.assertEqual(state, before)

    async def test_old_pinned_post_does_not_hide_new_original_on_later_page(self):
        state, client = await self.activate()
        second = Result([tweet('1003', created=NOW + timedelta(minutes=2))])
        next_page = AsyncMock(return_value=second)
        client.get_user_tweets.return_value = Result([
            tweet('1001', created=NOW - timedelta(hours=2)),
            tweet('1002', created=NOW + timedelta(minutes=1)),
        ], fetch_next_result=next_page)
        rows = await priority.discover(client, state, NOW + timedelta(minutes=30))
        self.assertEqual([row['id'] for row in rows], ['1002', '1003'])
        next_page.assert_awaited_once()

    async def test_later_page_failure_preserves_durable_queue_and_boundary(self):
        state, client = await self.activate()
        before = copy.deepcopy(state)
        client.get_user_tweets.return_value = Result(
            [tweet('1002', created=NOW + timedelta(minutes=1))],
            fetch_next_result=AsyncMock(side_effect=TimeoutError('next page unavailable')),
        )
        with self.assertRaises(TimeoutError):
            await priority.discover(client, state, NOW + timedelta(minutes=30))
        self.assertEqual(state, before)


if __name__ == '__main__':
    unittest.main()
