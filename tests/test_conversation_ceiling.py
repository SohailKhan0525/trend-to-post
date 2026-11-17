import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import editorial, mentions
from test_editorial import DRAFT, SOURCE, START, empty


class ConversationCeilingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # These historical fixtures exercise scheduling and delivery with abbreviated X IDs.
        self.enterContext(patch.object(editorial, 'fresh_invitation', return_value=True))
        self.state = empty()
        self.now = START + timedelta(minutes=1)
        self.user = SimpleNamespace(id='999', screen_name=editorial.ACCOUNT)
        self.source = {**SOURCE, 'conversation_id': '50', 'parent_id': '55', 'parent_author': 'Builder'}
        self.connection = SimpleNamespace(get_tweet_by_id=AsyncMock(), create_tweet=AsyncMock())

    def record(self, ident, previous, root):
        return {'kind': 'reply', 'tweet_id': ident, 'source': previous, 'thread_root': root,
                'posted_at': (START - timedelta(days=5)).isoformat(), 'verified_at': START.isoformat()}

    def test_two_per_recipient_across_days_and_siblings_with_an_independent_allowance_per_original(self):
        alice = self.source['author_id']
        bob = {**self.source, 'author_id': '32', 'author': 'Bob'}
        self.state['history'] = [
            self.record('81', {'id': '65', 'author_id': alice, 'parent_id': '50'}, '50'),
            self.record('82', {'id': '66', 'author_id': alice, 'parent_id': '81'}, '81'),
            {'kind': 'original', 'tweet_id': '50', 'verified_at': START.isoformat()},
        ]
        self.assertEqual(editorial.thread_reply_count(self.source, self.state['history']), 2)
        self.assertFalse(editorial.eligible_author(self.source, self.state, self.now, self.user.id))
        self.assertTrue(editorial.eligible_author(bob, self.state, self.now, self.user.id))
        self.state['history'].append(self.record('83', {'id': '68', 'author_id': '32', 'parent_id': '55'}, '50'))
        self.assertEqual(editorial.thread_reply_count(bob, self.state['history']), 1)
        self.state['history'].append(self.record('84', {'id': '69', 'author_id': '32', 'parent_id': '55'}, '50'))
        self.assertFalse(editorial.eligible_author(bob, self.state, self.now, self.user.id))
        other = {**self.source, 'id': '120', 'source_ids': ['120'], 'parent_id': '115', 'conversation_id': '113'}
        self.assertTrue(editorial.eligible_author(other, self.state, self.now, self.user.id))

    def test_legacy_self_reply_and_other_recipients_dont_consume_personal_allowance(self):
        self.state['history'] = [{'tweet_id': '50', 'source_id': '48', 'reply_id': '85', 'reply_status': 'verified'},
                                 {'tweet_id': '55', 'reply_id': '86', 'reply_status': 'unconfirmed'}]
        observed = {'50': {'999': ['85'], self.source['author_id']: ['87']}}
        self.assertEqual(editorial.thread_reply_count(self.source, self.state['history'], observed), 1)
        quoted = {**self.source, 'id': '129', 'source_ids': ['129'], 'parent_id': '48', 'conversation_id': '48'}
        self.assertEqual(editorial.thread_reply_count(quoted, self.state['history'], observed), 0)

    async def test_manually_published_siblings_block_before_writer_or_after_last_source_refresh(self):
        for first_check, last_check, drafts in (({'85', '86'}, set(), 0), ({'85'}, {'85', '86'}, 1)):
            with self.subTest(first=first_check):
                state = empty()
                slot = {**editorial.inbox_opportunity(self.source), 'kind': 'reply'}
                with patch.object(editorial, 'utcnow', return_value=self.now), patch.object(editorial, 'save_and_push'), \
                        patch.object(mentions, 'snapshot', return_value=self.source), \
                        patch.object(mentions, 'conversation_reply_ids', AsyncMock(return_value=first_check)) as search, \
                        patch.object(mentions, 'native_reply_ids', side_effect=[set(), last_check]), \
                        patch.object(editorial.reply_writer, 'generate', return_value=DRAFT) as writer:
                    await editorial.publish_reply(state, slot, self.connection, self.user, selected=self.source)
                search.assert_awaited_once_with(self.connection, self.user.id, '50', self.source['author_id'], self.source['author'])
                self.assertEqual(writer.call_count, drafts)
                self.assertEqual(state['observed_recipient_replies']['50'][self.source['author_id']], ['85', '86'])
                self.assertFalse(editorial.eligible_author(self.source, state, self.now, self.user.id))
                self.connection.create_tweet.assert_not_awaited()

    async def test_no_new_allowance_for_nested_reply_without_resolvable_original(self):
        unresolved = {**self.source, 'conversation_id': None}
        with patch.object(mentions, 'conversation_reply_ids', AsyncMock()) as search:
            self.assertFalse(await editorial.live_thread_open(self.state, unresolved, object(), self.user.id,
                                                              self.connection, set(), search=True))
            search.assert_not_awaited()

    async def test_one_unavailable_sibling_search_defers_only_its_thread(self):
        other = {**self.source, 'id': '129', 'source_ids': ['129'], 'parent_id': '48', 'conversation_id': '48'}
        with patch.object(mentions, 'conversation_reply_ids', AsyncMock(side_effect=[None, set()])):
            self.assertFalse(await editorial.live_thread_open(self.state, self.source, object(), self.user.id,
                                                              self.connection, set(), search=True))
            self.assertTrue(await editorial.live_thread_open(self.state, other, object(), self.user.id,
                                                             self.connection, set(), search=True))
        self.assertTrue(editorial.delivery_open(self.state))
