import io
import json
import sqlite3
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app.memory import DATABASE_NAME, SCHEMA, Memory, MemoryUnavailable


UUID = 'a51711e0-20ee-49de-94ed-5e6ee96683d1'
ENV = {'CLOUDFLARE_ACCOUNT_ID': 'a' * 32, 'CLOUDFLARE_API_TOKEN': 'fixture-ai-token'}


class LocalMemory(Memory):
    """Execute every production D1 statement against SQLite using the REST result shape."""
    def __init__(self):
        super().__init__('a' * 32, 'fixture')
        self.database_id = UUID
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.batches = []
        self.fail_batch = None
        self._batch([{'sql': sql} for sql in SCHEMA])

    def _request(self, path, payload=None):
        assert path == f'/{UUID}/query'
        statements = payload['batch']
        self.batches.append(statements)
        if self.fail_batch == len(self.batches):
            raise MemoryUnavailable('D1 unavailable')
        result = []
        with self.db:
            for statement in statements:
                params = statement.get('params', [])
                assert len(params) <= 100
                cursor = self.db.execute(statement['sql'], params)
                result.append({'success': True, 'results': [dict(row) for row in cursor.fetchall()]})
        return result


def reply(i, author='876'):
    ts = f'2026-11-17T{(i // 60) % 24:02}:{i % 60:02}:00+00:00'
    return {'kind': 'reply', 'tweet_id': str(2000+i), 'verified_at': ts, 'posted_at': ts,
            'text': f'Own reply {i}', 'event_key': f'idea {i}', 'outbox_secret': 'never stored',
            'source': {'author_id': author, 'author': 'dev', 'id': str(i+100),
                       'text': f"From the post: ');DROP TABLE your_data;-- {i}", 'private': 'never stored'}}


class MemoryTests(unittest.TestCase):
    def test_partial_backfill_retries_and_does_not_duplicate_or_profile_people(self):
        mem = LocalMemory()
        history = [{'kind': 'quote', 'text': 'unverified legacy'}]
        history += [reply(i) for i in range(94)]
        history += [{**reply(94), 'kind': 'original', 'source': None}]
        # Simulate a mid-sync interruption after the first 80 records committed.
        mem.fail_batch = len(mem.batches) + 3
        with self.assertRaises(MemoryUnavailable):
            mem.sync(history)
        mem.fail_batch = None
        self.assertEqual(mem.sync(history), 15)
        self.assertEqual(mem.sync(history), 0)
        context = mem.context([{'author_id': '876'}, {'author_id': '876'}])
        self.assertEqual(context['people'], [{'author_id': '876', 'author': 'dev', 'interactions': 94,
                                              'last_at': history[-2]['posted_at']}])
        self.assertEqual(context['recent_own'][0]['kind'], 'original')
        self.assertEqual([row['text'] for row in context['conversations']], ['Own reply 93', 'Own reply 92'])
        self.assertIn('DROP TABLE', context['conversations'][0]['source_text'])
        self.assertNotIn('never stored', str(mem.batches))
        self.assertNotIn('unverified legacy', str(mem.batches))
        unrelated = mem.context([{'author_id': '234'}])
        self.assertEqual(unrelated['people'], [])
        self.assertEqual(unrelated['conversations'], [])
        self.assertEqual(unrelated['recent_own'], mem.context([])['recent_own'])
        self.addCleanup(mem.db.close)

    def test_explicit_database_uses_separate_token_and_skips_list_or_create(self):
        env = {**ENV, 'CLOUDFLARE_D1_API_TOKEN': 'dedicated', 'D1_DATABASE_ID': UUID}
        with patch.dict('os.environ', env, clear=True), patch.object(Memory, '_batch') as bootstrap, \
                patch.object(Memory, '_request') as request:
            mem = Memory.connect()
        self.assertEqual(mem.token, 'dedicated')
        self.assertEqual(mem.database_id, UUID)
        request.assert_not_called()
        bootstrap.assert_called_once()

    def test_name_lookup_and_concurrent_creation_use_exact_database(self):
        for race in (False, True):
            calls = [[{'name': DATABASE_NAME + '-other', 'uuid': 'unrelated'}]]
            if race:
                calls.extend([MemoryUnavailable('concurrent create'), [{'name': DATABASE_NAME, 'uuid': UUID}]])
            else:
                calls.append({'name': DATABASE_NAME, 'uuid': UUID})
            calls.append([{'success': True, 'results': []} for _ in SCHEMA])
            with self.subTest(race=race), patch.dict('os.environ', ENV, clear=True), \
                    patch.object(Memory, '_request', side_effect=calls) as request:
                mem = Memory.connect()
                self.assertEqual(mem.database_id, UUID)
                self.assertEqual(request.call_args_list[1].args, ('', {'name': DATABASE_NAME}))
                self.assertEqual(request.call_args_list[-1].args[0], f'/{UUID}/query')

    def test_denied_or_malformed_responses_abort_without_exposing_credentials(self):
        mem = Memory('a'*32, 'private-token')
        mem.database_id = UUID
        error = HTTPError('https://api.cloudflare.com/', 403, 'private-token', {}, io.BytesIO(b'private-token'))
        with patch('app.memory.urlopen', side_effect=error), self.assertRaisesRegex(MemoryUnavailable, 'D1 Write') as failed:
            mem.context([])
        self.assertNotIn('private-token', str(failed.exception))
        bad_batch = io.BytesIO(json.dumps({'success': True, 'result': [{'success': False}]}).encode())
        with patch('app.memory.urlopen', return_value=bad_batch), self.assertRaisesRegex(MemoryUnavailable, 'SQL statement'):
            mem.sync([])


if __name__ == '__main__':
    unittest.main()
