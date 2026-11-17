import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import editorial
from test_editorial import SOURCE, START, empty


class NativeReplyReadbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.text = 'One cache miss and the build has a personality 🫠.'
        self.prefix = '@Acme @thirdparty @qofenoai '
        self.pending = {'slot': 'inbox-test', 'kind': 'reply', 'source': SOURCE,
                        'reserved_at': START.isoformat(), 'author_id': '999',
                        'tweet_id': '333', 'text': self.text, 'event_key': 'cache-miss'}

    def post(self, text=None, bounds=None):
        full_text = self.prefix + self.text if text is None else text
        if bounds is None:
            bounds = [len(self.prefix), len(full_text.encode('utf-16-le')) // 2]
        return SimpleNamespace(id='333', full_text=full_text, in_reply_to=SOURCE['id'],
                               user=SimpleNamespace(id='999', screen_name=editorial.ACCOUNT),
                               _data={'legacy': {'full_text': full_text, 'display_text_range': bounds}})

    async def test_native_hidden_multiparticipant_prefix_reconciles_known_id_once(self):
        state = {**empty(), 'outbox': self.pending,
                 'pause_reason': 'Published post does not match the reserved author, text or reply parent'}
        connection = SimpleNamespace(get_tweet_by_id=AsyncMock(return_value=self.post()), create_tweet=AsyncMock())
        with patch.object(editorial, 'save_and_push'), patch.object(editorial, 'summary'), patch.object(editorial, 'stage'):
            self.assertTrue(await editorial.verify_delivery(state, connection))
            self.assertTrue(await editorial.verify_delivery(state, connection))
        self.assertEqual([row['tweet_id'] for row in state['history']], ['333'])
        self.assertIsNone(state['outbox'])
        self.assertNotIn('pause_reason', state)
        connection.get_tweet_by_id.assert_awaited_once_with('333')
        connection.create_tweet.assert_not_awaited()

    def test_visible_handles_changed_text_malformed_ranges_and_mismatched_identity_remain_pending(self):
        full = self.prefix + self.text
        wrong_native = self.post()
        wrong_native._data['legacy']['full_text'] = 'old snapshot'
        wrong_parent = self.post()
        wrong_parent.in_reply_to = '444'
        wrong_author = self.post()
        wrong_author.user.id = '444'
        quoted = self.post()
        quoted.quoted_status_id = '444'
        repost = self.post()
        repost.retweeted_tweet = object()
        for post in (wrong_native, wrong_parent, wrong_author, quoted, repost,
                     self.post(bounds=[len('@Acme '), len(full)]),
                     self.post(bounds=[0, len(full)]), self.post(bounds=[-1, len(full)]),
                     self.post(bounds=[len(self.prefix), len(full) + 5]),
                     self.post(self.prefix + self.text + '!'),
                     self.post('written words ' + self.text, [len('written words '), len(self.text) + 14])):
            with self.subTest(text=post.full_text, metadata=post._data):
                self.assertFalse(editorial.matches_publication(self.pending, post))
