import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from scripts import mention_status as status


class MentionStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_reserved_reply_does_not_stop_read_only_inbox_report(self):
        self.state['outbox'] = {'kind': 'reply', 'tweet_id': '123'}
        self.client.get_tweet_by_id.side_effect = status.NotFound('gone')
        with patch.object(status.mentions, 'discover', AsyncMock(return_value=[])) as discover, patch.object(status.store, 'summary') as log:
            await status.inspect_inbox(self.client, self.state, self.now, '')
        discover.assert_awaited_once()
        self.assertIn('unavailable for readback', log.call_args_list[0].args[0])
        self.client.create_tweet.assert_not_called()

    def setUp(self):
        self.now = datetime(2026, 11, 17, 16, tzinfo=timezone.utc)
        self.state = {'history': [], 'used_source_ids': [], 'used_urls': [], 'mention_preferences': {}}
        self.user = SimpleNamespace(id='99', screen_name='qofenoai')
        self.client = SimpleNamespace(get_user_by_screen_name=AsyncMock(return_value=self.user),
                                      get_tweet_by_id=AsyncMock(), create_tweet=Mock())

    def invite(self, number=1, author_id='81', root='500', at=None):
        created = at or self.now
        snowflake = str(((int(created.timestamp() * 1000) - 1288834974657) << 22) + number)
        return {'id': snowflake, 'author_id': author_id, 'author': 'reader' + author_id,
                'text': '@qofenoai hello', 'origin': 'mention', 'intent': 'invite',
                'conversation_id': root, 'created_at': created.isoformat(),
                'source_ids': [snowflake], 'urls': []}

    def test_ledger_distinguishes_verified_edited_sources_pending_and_rejected(self):
        source = {'id': '444', 'source_ids': ['443', '444']}
        self.state.update(history=[{'verified_at': '2026-11-17T11:00:00Z', 'source': source}],
                          outbox={'source': {'id': '774'}},
                          delivery_rejections=[{'source': {'id': '775'}}], used_source_ids=['776'])
        self.assertEqual(status.previously_handled('443', self.state), 'reply verified on X')
        self.assertEqual(status.previously_handled('774', self.state), 'delivery pending verification')
        self.assertEqual(status.previously_handled('775', self.state), 'previous delivery rejected or unconfirmed')
        self.assertEqual(status.previously_handled('776', self.state), 'previously reserved source')
        self.assertIsNone(status.previously_handled('777', self.state))

    async def test_live_sample_sanitizes_text_and_honors_optouts_only_in_local_copy(self):
        row = {**self.invite(1, '91234567'), 'author': 'NeverOutputThis', 'text': 'Never output this text'}
        command = {**self.invite(2, '91234567'), 'author': row['author'], 'intent':'stop'}
        with patch.object(status.mentions, 'discover', AsyncMock(return_value=[row,command])), \
                patch.object(status.store, 'summary') as log:
            await status.inspect_inbox(self.client, self.state, self.now, row['id'])
        output = '\n'.join(call.args[0] for call in log.call_args_list)
        self.assertIn('0 eligible for review', output)
        self.assertIn('present in inbox sample; author opted out', output)
        self.assertNotIn('Never', output)
        self.assertNotIn('1234567', output)
        self.assertEqual(self.state['mention_preferences'], {})
        self.client.create_tweet.assert_not_called()
        self.client.get_tweet_by_id.assert_not_awaited()

    async def test_direct_lookup_labels_missing_inbox_result_without_assuming_delivery(self):
        with patch.object(status.mentions,'discover', AsyncMock(return_value=[])), \
                patch.object(status.mentions,'snapshot', side_effect=status.sources.SourceError('PRIVATE')), \
                patch.object(status.store,'summary') as log:
            await status.inspect_inbox(self.client, self.state, self.now, '1234')
        self.client.get_tweet_by_id.assert_awaited_once_with('1234')
        self.assertIn('absent from inbox sample; post failed', log.call_args.args[0])
        self.assertNotIn('PRIVATE', log.call_args.args[0])
        self.client.create_tweet.assert_not_called()

    async def test_cap_applies_independently_per_person_and_per_original(self):
        for sent, source in [('504', self.invite(1)), ('505', self.invite(2))]:
            self.state['history'].append({'kind': 'reply', 'thread_root': '500', 'tweet_id': sent,
                                         'source': source, 'verified_at': self.now.isoformat(),
                                         'posted_at': self.now.isoformat()})
        rows = [self.invite(3), self.invite(4, author_id='82'), self.invite(5, root='600')]
        with patch.object(status.mentions, 'discover', AsyncMock(return_value=rows)), \
                patch.object(status.store, 'summary') as log:
            await status.inspect_inbox(self.client, self.state, self.now, rows[0]['id'])
        output = '\n'.join(call.args[0] for call in log.call_args_list)
        self.assertIn('2 eligible for review', output)
        self.assertIn('present in inbox sample; this person reached 2 replies in this conversation', output)
        self.client.create_tweet.assert_not_called()
        # One reply may have been published directly on X and observed later.
        self.state['history'].pop()
        self.state['observed_recipient_replies'] = {'500': {'81': ['504', '505']}}
        self.assertEqual(status.eligibility(rows[0], self.state, self.now, '99'),
                         'this person reached 2 replies in this conversation')
        self.assertEqual(status.eligibility(rows[1], self.state, self.now, '99'), 'eligible for review')

    def test_queued_mention_and_edits_to_old_invites_are_identified_before_cutoff(self):
        older = self.invite(at=datetime(2026, 11, 17, 14, 5, 10, tzinfo=timezone.utc))
        at_start = datetime(2026, 11, 17, 14, 5, 11, tzinfo=timezone.utc)
        new = self.invite(at=at_start)
        edited = {**new, 'source_ids': [older['id'], new['id']]}
        self.assertEqual(status.eligibility(older, self.state, self.now, '99'), 'before reply cutoff')
        self.assertEqual(status.eligibility(edited, self.state, self.now, '99'), 'before reply cutoff')
        self.assertEqual(status.eligibility(new, self.state, at_start, '99'), 'eligible for review')
        self.assertEqual(status.eligibility(new, self.state, at_start + timedelta(minutes=5, seconds=1), '99'),
                         'source is over five minutes old')

    def test_direct_review_is_per_version_and_legacy_silence_can_be_revisited(self):
        row = self.invite()
        slot = status.editorial.inbox_opportunity(row)['slot']
        self.state['decision_slots'] = {slot: {'action': 'silent', 'at': self.now.isoformat()}}
        self.assertEqual(status.eligibility(row, self.state, self.now, '99'), 'eligible for review')
        self.state['decision_slots'][slot]['direct_reviewed'] = True
        self.assertEqual(status.eligibility(row, self.state, self.now, '99'), 'already reviewed')
        edited = {**row, 'text': '@qofenoai hello again'}
        self.assertEqual(status.eligibility(edited, self.state, self.now, '99'), 'eligible for review')
        self.state['used_source_ids'] = row['source_ids']
        self.assertEqual(status.eligibility(edited, self.state, self.now, '99'), 'previously reserved source')

    async def test_retry_allowed_by_publisher_is_counted_as_eligible(self):
        row = self.invite()
        entry = {'until':'2026-11-17T17:00:00Z'}
        self.state['reply_rejections'] = {status.editorial.rejection_key(row): entry}
        with patch.object(status.mentions, 'discover', AsyncMock(return_value=[row])), \
                patch.object(status.editorial, 'reply_rejection_active', return_value=False) as policy, \
                patch.object(status.store, 'summary') as log:
            await status.inspect_inbox(self.client, self.state, self.now, '')
        policy.assert_called_once_with(row, entry, self.now)
        result = '\n'.join(call.args[0] for call in log.call_args_list)
        self.assertIn('1 eligible for review', result)
        self.assertIn('eligible for review (bounded retry) = 1', result)
