import asyncio
import copy
import os
import sqlite3
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app import cli, editorial, server_ledger, store
from app.memory import LedgerMemory, MemoryUnavailable


RELEASE = 'a' * 40
INSTANCE = 'worker-a'
LEGACY = {'version': 1,
          'history': [{'tweet_id': str(i), 'text': '備忘録'} for i in range(135)],
          'used_source_ids': ['123'], 'used_urls': [], 'used_events': [], 'used_texts': [],
          'old_settings': {'preserve': True}, 'decision_slots': {'prior': 'silent'},
          'editorial_history': [], 'last_attempts': {}}


class ServerLedgerTests(unittest.TestCase):
    def setUp(self):
        self.db = LedgerMemory()
        self.addCleanup(self.db.db.close)
        self.ledger = server_ledger.ServerLedger(self.db)

    def activate(self):
        self.assertTrue(self.ledger.heartbeat(INSTANCE, RELEASE, 'standby'))
        self.assertTrue(self.ledger.seed(copy.deepcopy(LEGACY), RELEASE))

    def test_seed_and_preflight_are_release_specific_and_require_live_server(self):
        with self.assertRaises(MemoryUnavailable):
            self.ledger.seed(LEGACY, RELEASE)
        self.assertFalse(self.ledger.active())
        self.assertTrue(self.ledger.heartbeat(INSTANCE, RELEASE, 'standby'))
        self.assertFalse(self.ledger.heartbeat('other', RELEASE, 'active'))
        self.assertIsNone(self.ledger.status(RELEASE))
        self.assertIsNone(self.ledger.status('b' * 40, ready=True))
        self.assertEqual(self.ledger.status(RELEASE, ready=True), 'standby')
        with self.assertRaises(MemoryUnavailable):
            self.ledger.seed(LEGACY, 'b' * 40)
        self.assertFalse(self.ledger.active())
        self.assertTrue(self.ledger.seed(LEGACY, RELEASE))
        self.assertEqual(self.ledger.load(), LEGACY)
        self.assertTrue(self.ledger.heartbeat(INSTANCE, RELEASE, 'active'))
        self.assertEqual(self.ledger.status(RELEASE), 'active')

    def test_reserved_outbox_and_unknown_metadata_survive_retry_and_restart(self):
        self.activate()
        state = self.ledger.load()
        state['outbox'] = {'kind': 'reply', 'slot': 'one', 'text': 'small and specific', 'event_key': 'event',
                           'author_id': '1', 'source': {'id': '12'}, 'reserved_at': '2026-11-16T00:00:00+00:00'}
        state['used_texts'].append('signature')
        self.ledger.save(state, INSTANCE)
        self.assertFalse(self.ledger.seed(LEGACY, RELEASE))
        restarted = server_ledger.ServerLedger(self.db).load()
        self.assertEqual(restarted, state)
        self.assertFalse(editorial.delivery_open(restarted))

    def test_competing_and_stale_writers_cannot_overwrite_an_outbox_or_decision(self):
        self.activate()
        winner = self.ledger.load()
        stale = self.ledger.load()
        winner['ai_usage'] = {'charged': 1}
        self.ledger.save(winner, INSTANCE)
        stale['history'].append({'tweet_id': 'unpublished'})
        stale.pop('old_settings')
        with self.assertRaises(MemoryUnavailable):
            self.ledger.save(stale, INSTANCE)
        different = self.ledger.load()
        different['used_source_ids'].append('125')
        with self.assertRaises(MemoryUnavailable):
            self.ledger.save(different, 'other-worker')
        self.assertEqual(self.ledger.load(), winner)

    def test_server_lease_expiry_and_release_fence_old_process(self):
        self.activate()
        state = self.ledger.load()
        self.db.db.execute(f'UPDATE {server_ledger.LEASE} SET seen=0')
        self.assertIsNone(self.ledger.status(RELEASE, ready=True))
        with self.assertRaises(MemoryUnavailable):
            self.ledger.save(state, INSTANCE)
        self.assertTrue(self.ledger.heartbeat('successor', RELEASE, 'active'))
        self.ledger.release(INSTANCE)
        self.assertEqual(self.ledger.status(RELEASE), 'active')
        with self.assertRaises(MemoryUnavailable):
            self.ledger.save(state, INSTANCE)
        self.ledger.save(state, 'successor')
        self.ledger.release('successor')
        self.assertIsNone(self.ledger.status(RELEASE))

    def test_chunk_edits_reconstruction_and_full_transaction_rollback(self):
        self.activate()
        initial = self.ledger.load()
        state = self.ledger.load()
        state['used_urls'] = ['https://example.com/notes']
        state.pop('old_settings')
        state['history'].append({'text': '🛰️'})
        state['new_values'] = list(range(80))
        self.ledger.save(state, INSTANCE)
        self.assertEqual(server_ledger.ServerLedger(self.db).load(), state)
        state['new_values'] = []
        self.ledger.save(state, INSTANCE)
        self.assertEqual(server_ledger.ServerLedger(self.db).load(), state)
        self.db.db.execute(f"CREATE TRIGGER fail_save BEFORE UPDATE ON {server_ledger.HEAD} BEGIN SELECT RAISE(ABORT,'failed'); END;")
        state['used_events'] = ['new value triggering write']
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.save(state, INSTANCE)
        self.assertEqual(server_ledger.ServerLedger(self.db).load()['used_events'], initial['used_events'])
        state['history'] = []
        with self.assertRaisesRegex(ValueError, 'truncate'):
            self.ledger.save(state, INSTANCE)

    def test_auto_mode_reads_current_authority_and_refuses_writes(self):
        with patch.object(server_ledger, 'connect', return_value=self.ledger), \
                patch.dict(os.environ, {'QOFENO_LEDGER': 'auto'}), patch.object(store, 'read_state', return_value=LEGACY):
            self.assertEqual(store.load(), LEGACY)
            self.activate()
            state = self.ledger.load()
            state['used_events'] = ['kept in D1']
            self.ledger.save(state, INSTANCE)
            self.assertEqual(store.load()['used_events'], ['kept in D1'])
            with self.assertRaisesRegex(ValueError, 'read-only'):
                store.save_and_push(state)

    def test_legacy_github_entrypoints_fail_closed_when_d1_owns_or_cannot_answer(self):
        self.activate()
        with patch.object(server_ledger, 'connect', return_value=self.ledger), \
                patch.dict(os.environ, {'QOFENO_LEDGER': 'git'}), \
                patch.object(editorial, 'run', new_callable=AsyncMock) as run, \
                patch.object(editorial, 'check_inbox', new_callable=AsyncMock) as inbox:
            for cmd in ('run', 'inbox'):
                with patch.object(sys, 'argv', ['app', cmd]):
                    cli.main()
                    with patch.object(self.ledger, 'active', side_effect=MemoryUnavailable('test outage')), self.assertRaises(SystemExit):
                        cli.main()
            run.assert_not_awaited()
            inbox.assert_not_awaited()

    def test_non_owner_cannot_send_even_with_an_approved_draft(self):
        self.activate()
        state = self.ledger.load()
        user = Mock(id='99', screen_name=store.ACCOUNT)
        x = Mock(create_tweet=AsyncMock())
        slot = {'kind': 'original', 'slot': '123', 'expires_at': '2100-01-01T00:00:00+00:00',
                'allowed_actions': ['original']}
        with patch.dict(os.environ, {'QOFENO_LEDGER': 'd1', 'QOFENO_INSTANCE': 'not-leader'}), \
                patch.object(server_ledger, 'connect', return_value=self.ledger), \
                patch.object(editorial, 'next_decision', return_value=(slot, None)), self.assertRaises(MemoryUnavailable):
            asyncio.run(editorial.send_once(state, slot, {'text': 'New take', 'event_key': 'fun'}, x, user))
        x.create_tweet.assert_not_awaited()
        self.assertNotIn('outbox', self.ledger.load())
