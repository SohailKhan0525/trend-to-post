import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import editorial, movie_posts
from app.reply_schedule import half_hour_start, verified_originals_today
from test_editorial import empty


NOW = datetime(2026, 11, 27, 16, 37, tzinfo=timezone.utc)
DRAFT = movie_posts.scheduled_draft({}, NOW)
SELECT_MOVIE = movie_posts.scheduled_draft


class ScheduledMoviePublishingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = NOW
        self.state = empty()
        self.state['actions_inbox_since'] = (NOW - timedelta(days=1)).isoformat()
        self.user = SimpleNamespace(id='999', screen_name='qofenoai')
        self.post = SimpleNamespace(id='998', user=self.user, full_text=DRAFT['text'], in_reply_to=None,
                                    created_at_datetime=NOW, _data={'legacy': {}})
        self.saved = []
        self.connection = SimpleNamespace(
            get_user_by_screen_name=AsyncMock(return_value=self.user), get_tweet_by_id=AsyncMock(return_value=self.post),
            create_tweet=AsyncMock(return_value=self.post))
        self.enterContext(patch.object(editorial, 'utcnow', side_effect=lambda: self.now))
        self.enterContext(patch.object(editorial, 'load', return_value=self.state))
        self.enterContext(patch.object(editorial, 'client', return_value=self.connection))
        self.enterContext(patch.object(editorial, 'save_and_push',
                                       side_effect=lambda state: self.saved.append(copy.deepcopy(state))))
        self.enterContext(patch.object(editorial, 'summary'))
        self.priority = self.enterContext(patch.object(editorial, 'process_priority', new_callable=AsyncMock))
        self.inbox = self.enterContext(patch.object(editorial, 'process_inbox', new_callable=AsyncMock))
        self.writer = self.enterContext(patch.object(movie_posts, 'scheduled_draft', return_value=DRAFT))
        self.discovery = self.enterContext(patch.object(editorial, 'decide', new_callable=AsyncMock))
        self.discovery.side_effect = lambda state, slot, *a, **kw: editorial.record_silence(state, slot, self.now, {})

    async def test_release_wake_reserves_parentless_reset_then_reads_back_and_keeps_reply_routing(self):
        order = Mock()
        order.attach_mock(self.priority, 'priority')
        order.attach_mock(self.inbox, 'inbox')
        order.attach_mock(self.writer, 'writer')
        order.attach_mock(self.discovery, 'discovery')
        await editorial.run('push')
        self.assertEqual([call[0] for call in order.mock_calls], ['priority', 'inbox', 'writer', 'discovery'])
        self.assertEqual(self.discovery.call_args.args[1]['allowed_actions'], ['reply'])
        self.connection.create_tweet.assert_awaited_once_with(text=DRAFT['text'])
        self.connection.get_tweet_by_id.assert_awaited_once_with(self.post.id)
        self.assertEqual(self.state['history'][0]['text'], DRAFT['text'])
        reservation = next(saved for saved in self.saved if saved.get('outbox'))
        self.assertEqual(reservation['history'], [])
        self.assertNotIn('tweet_id', reservation['outbox'])
        self.assertNotIn('source', reservation['outbox'])
        self.assertEqual(reservation['outbox']['post_style'], 'reset_announcement')
        self.assertIn(editorial.reply_writer.fingerprint(DRAFT['text']), reservation['used_texts'])
        self.assertIn(editorial.reply_writer.fingerprint(DRAFT['event_key']), reservation['used_events'])
        self.assertEqual(self.state['used_source_ids'], [])
        self.assertEqual(self.state['history'][0]['kind'], 'original')
        self.assertEqual(verified_originals_today(self.state, self.now), 1)
        await editorial.run('scheduled')
        self.assertEqual(self.writer.call_count, 1)
        self.connection.create_tweet.assert_awaited_once()

    async def test_send_guard_only_accepts_the_requested_reset_then_credited_movie_lines(self):
        for text in ('Hey @grok, which part of the film was your favorite?', DRAFT['text'] + ' @grok', 'Reset complete.'):
            with self.subTest(text=text):
                slot = editorial.movie_slot(half_hour_start(self.now))
                with self.assertRaises(editorial.DraftError):
                    await editorial.send_once(self.state, slot, {**DRAFT, 'text': text}, self.connection, self.user)
                self.connection.create_tweet.assert_not_awaited()
                self.assertFalse(self.state.get('outbox'))
                self.assertEqual(self.state['used_texts'], [])

    async def test_native_readback_must_be_a_root_post_with_a_verified_author(self):
        self.post.in_reply_to = '777'
        await editorial.run('scheduled')
        self.connection.create_tweet.assert_awaited_once_with(text=DRAFT['text'])
        self.assertEqual(self.state['outbox']['tweet_id'], self.post.id)
        self.assertEqual(verified_originals_today(self.state, self.now), 0)
        self.discovery.assert_not_awaited()

    async def test_original_draft_failure_keeps_existing_reply_opportunity_and_paces_retries(self):
        self.writer.side_effect = editorial.DraftError('Movie line was already used')
        await editorial.run('scheduled')
        self.discovery.assert_awaited_once()
        self.assertTrue(self.discovery.call_args.args[1]['discovery_reply'])
        self.assertFalse(self.state.get('outbox'))
        self.assertEqual(verified_originals_today(self.state, self.now), 0)
        await editorial.run('scheduled')
        self.writer.assert_called_once()
        self.now += timedelta(minutes=10)
        await editorial.run('scheduled')
        self.assertEqual(self.writer.call_count, 2)
        self.connection.create_tweet.assert_not_awaited()

    async def test_readback_delay_resumes_known_original_without_another_send(self):
        self.connection.get_tweet_by_id.side_effect = [TimeoutError(), self.post]
        await editorial.run('scheduled')
        self.assertEqual(self.state['outbox']['tweet_id'], self.post.id)
        self.assertEqual(verified_originals_today(self.state, self.now), 0)
        self.discovery.assert_not_awaited()
        await editorial.run('workflow_dispatch')
        self.assertIsNone(self.state['outbox'])
        self.assertEqual(verified_originals_today(self.state, self.now), 1)
        self.writer.assert_called_once()
        self.connection.create_tweet.assert_awaited_once()
        self.discovery.assert_awaited_once()

    async def test_last_minute_review_and_duplicate_recheck_before_reservation(self):
        self.state['used_events'].append(editorial.reply_writer.fingerprint(DRAFT['event_key']))
        await editorial.run('scheduled')
        self.writer.assert_called_once()
        self.connection.create_tweet.assert_not_awaited()
        self.assertFalse(self.state.get('outbox'))
        self.assertEqual(self.state['history'], [])
        self.assertEqual(verified_originals_today(self.state, self.now), 0)
        self.discovery.assert_awaited_once()

    async def test_only_current_canonical_movie_opportunity_allows_standalone_send(self):
        current = editorial.movie_slot(half_hour_start(self.now))
        fresh = self.now
        for changes, source in (({'kind': 'reply'}, None), ({'slot': 'older-policy'}, None),
                                ({'expires_at': (fresh + timedelta(days=1)).isoformat()}, None),
                                ({}, {'id': '123'})):
            with self.subTest(changes=changes, source=source):
                await editorial.send_once(self.state, {**current, **changes}, DRAFT,
                                          self.connection, self.user, source)
        self.now += timedelta(minutes=30)
        await editorial.send_once(self.state, current, DRAFT, self.connection, self.user)
        self.connection.create_tweet.assert_not_awaited()
        self.assertEqual(self.saved, [])

    async def test_fifth_verified_original_closes_originals_for_day_but_keeps_reply_capacity(self):
        # At this hour four originals already meet the normal pacing. The reset
        # can fill today's fifth spot; a second original cannot follow it.
        self.now = NOW
        self.post.created_at_datetime = self.now
        for i in range(4):
            posted = self.now.replace(hour=i).isoformat()
            self.state['history'].append({'kind': 'original', 'posted_at': posted, 'verified_at': posted,
                                          'tweet_id': str(1200 + i)})
        await editorial.run('scheduled')
        self.assertEqual(verified_originals_today(self.state, self.now), 5)
        self.now += timedelta(minutes=30)
        await editorial.run('scheduled')
        self.writer.assert_called_once()
        self.connection.create_tweet.assert_awaited_once()
        self.assertEqual(self.discovery.await_count, 2)

    async def test_reset_waits_if_five_historical_questions_today_or_one_in_current_window(self):
        for times in ((1, 2, 3, 4, 5), (self.now.hour,)):
            with self.subTest(times=times):
                self.state['history'] = []
                for index, hour in enumerate(times):
                    stamp = self.now.replace(hour=hour).isoformat()
                    self.state['history'].append({'kind': 'original', 'post_style': 'open_question',
                        'tweet_id': str(300 + index), 'posted_at': stamp, 'verified_at': stamp})
                await editorial.scheduled_movie_original(self.state, self.connection, half_hour_start(self.now))
                self.writer.assert_not_called()
                self.connection.create_tweet.assert_not_awaited()

    async def test_originals_ignore_model_holds_but_honor_x_cooldowns_and_pending_deliveries(self):
        until = (self.now + timedelta(hours=1)).isoformat()
        self.state['ai_pause'] = {'reason': 'AI allocation exhausted', 'until': until}
        start = half_hour_start(self.now)
        for key, value in (('outbox', {'kind': 'reply'}), ('x_pause_until', until),
                           ('pause_reason', 'uncertain prior send'), ('pending_reply', {'id': '100'})):
            with self.subTest(key=key):
                self.state[key] = value
                await editorial.scheduled_movie_original(self.state, self.connection, start)
                self.state.pop(key)
                self.connection.create_tweet.assert_not_awaited()
        await editorial.run('scheduled')
        self.connection.create_tweet.assert_awaited_once_with(text=movie_posts.RESET_TEXT)
        self.discovery.assert_not_awaited()
        self.assertNotIn('ai_usage', self.state)

    async def test_movie_line_after_read_back_reset_carries_credit_and_no_reply_parent(self):
        await editorial.run('push')
        self.now = self.now.replace(hour=21)
        sample = {'quote': 'We can still take the last train.', 'speaker': 'Mara', 'title': 'Test Film',
                  'source': 'https://example.org/test-film-script'}
        self.enterContext(patch.object(movie_posts, 'catalog', return_value=[sample]))
        quote = SELECT_MOVIE(self.state, self.now)
        self.writer.return_value = quote
        self.post.full_text = quote['text']
        self.post.id = '1050'
        self.post.created_at_datetime = self.now
        await editorial.run('scheduled')
        self.assertEqual(self.connection.create_tweet.await_args.kwargs, {'text': quote['text']})
        row = self.state['history'][-1]
        self.assertEqual(row['post_style'], 'movie_quote')
        self.assertEqual(row['movie_credit'], movie_posts.credit(quote, self.state))
        self.assertNotIn('source', row)
        self.assertEqual(verified_originals_today(self.state, self.now), 2)

    async def test_reconciles_old_question_outbox_without_republishing_or_revalidating_it(self):
        legacy_text = 'Hey @grok, which dependency would you remove first?'
        legacy = {'kind': 'original', 'post_style': 'open_question', 'slot': '2026-11-27:actions-question:33',
                  'event_key': 'legacy question', 'tweet_id': self.post.id, 'author_id': self.user.id,
                  'text': legacy_text, 'reserved_at': self.now.isoformat()}
        self.state['outbox'] = legacy
        self.post.full_text = legacy_text
        await editorial.run('scheduled')
        self.assertEqual(self.state['history'][0]['text'], legacy_text)
        self.assertIsNone(self.state['outbox'])
        self.assertTrue(movie_posts.reset_pending(self.state))
        self.connection.create_tweet.assert_not_awaited()
        self.writer.assert_not_called()

    async def test_pre_send_expiry_marks_only_proven_unsent_reset_as_retryable(self):
        start = half_hour_start(self.now)
        def save(state):
            self.saved.append(copy.deepcopy(state))
            if state.get('outbox'):
                self.now = start + timedelta(minutes=30)
        editorial.save_and_push.side_effect = save
        await editorial.scheduled_movie_original(self.state, self.connection, start)
        self.connection.create_tweet.assert_not_awaited()
        self.assertIsNone(self.state['outbox'])
        self.assertEqual(self.state['delivery_rejections'][-1]['error'], 'ExpiredBeforeSend')
        self.assertEqual(SELECT_MOVIE(self.state, self.now), DRAFT)
