import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from scripts import mention_status as status


class MentionStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        self.state = {'history': [], 'used_source_ids': [], 'used_urls': [], 'mention_preferences': {}}
        self.user = SimpleNamespace(id='99', screen_name='qofenoai')
        self.client = SimpleNamespace(get_user_by_screen_name=AsyncMock(return_value=self.user),
                                      get_tweet_by_id=AsyncMock(), create_tweet=Mock())

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
        stamp = self.now.isoformat()
        row = {'id':'81234567','author_id':'91234567','author':'NeverOutputThis','intent':'invite',
               'text':'Never output this text','origin':'mention','source_ids':['81234567'],'urls':[],
               'created_at':stamp}
        command = {**row, 'id':'81234568','source_ids':['81234568'],'intent':'stop'}
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

    def test_silence_on_current_source_counts_as_reviewed(self):
        row = {'id': '71234567', 'author_id': '81234567', 'author': 'reader',
               'text': '@qofenoai hello', 'origin': 'mention', 'intent': 'invite',
               'created_at': self.now.isoformat(), 'source_ids': ['71234567'], 'urls': []}
        slot = status.editorial.inbox_opportunity(row)['slot']
        self.state['decision_slots'] = {slot: {'action': 'silent', 'at': self.now.isoformat()}}
        self.assertEqual(status.eligibility(row, self.state, self.now, '99'), 'already reviewed')

    async def test_retry_allowed_by_publisher_is_counted_as_eligible(self):
        row = {'id':'71234567', 'author_id':'81234567', 'author':'reader', 'text':'@qofenoai hello',
               'origin':'mention', 'intent':'invite', 'created_at':self.now.isoformat(),
               'source_ids':['71234567'], 'urls':[]}
        entry = {'until':'2026-11-17T13:00:00Z'}
        self.state['reply_rejections'] = {status.editorial.rejection_key(row): entry}
        with patch.object(status.mentions, 'discover', AsyncMock(return_value=[row])), \
                patch.object(status.editorial, 'reply_rejection_active', return_value=False) as policy, \
                patch.object(status.store, 'summary') as log:
            await status.inspect_inbox(self.client, self.state, self.now, '')
        policy.assert_called_once_with(row, entry, self.now)
        result = '\n'.join(call.args[0] for call in log.call_args_list)
        self.assertIn('1 eligible for review', result)
        self.assertIn('eligible for review (bounded retry) = 1', result)
