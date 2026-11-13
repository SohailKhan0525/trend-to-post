import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app import store


LEGACY = {
    'version': 1,
    'history': [{'tweet_id': '123', 'text': 'An existing public post',
                 'posted_at': '2026-11-13T18:00:00+00:00', 'reply': 'Existing self reply'}],
    'used_source_ids': ['10'], 'used_urls': ['https://example.com/story'],
    'used_events': ['event-fingerprint'], 'used_texts': ['text-fingerprint'],
    'pending': None,
    'ai_usage': {'requests': 30, 'neurons_reserved': 1234},
    'legacy_details': {'keep': 'all historical metadata'},
}


class StoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'state' / 'qofeno.json'
        self.path.parent.mkdir()
        self.write(LEGACY)
        location = patch.object(store, 'STATE', self.path)
        location.start()
        self.addCleanup(location.stop)

    def write(self, state):
        self.path.write_text(json.dumps(state), encoding='utf-8')

    def saved(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_load_retains_all_legacy_history_and_only_initializes_runtime_ledgers(self):
        before = self.path.read_bytes()
        loaded = store.load()
        self.assertEqual(loaded, {**LEGACY, 'editorial_history': [], 'last_attempts': {}})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn('reply_drafts', loaded)
        self.assertNotIn('drafted_texts', loaded)

    def test_existing_editorial_and_unknown_legacy_fields_round_trip_unchanged(self):
        original = {**copy.deepcopy(LEGACY),
                    'editorial_history': [{'slot': 'prior-slot', 'kind': 'follow', 'tweet_id': '123'}],
                    'reply_drafts': [{'text': 'Legacy unsent draft', 'completed_at': '2026-11-12'}],
                    'drafted_texts': ['old-draft-hash']}
        self.write(original)
        loaded = store.load()
        with patch.object(store.subprocess, 'run') as push:
            store.save_and_push(loaded)
        self.assertEqual(self.saved(), {**original, 'last_attempts': {}})
        self.assertEqual(sorted(path.relative_to(self.root).as_posix()
                                for path in self.root.rglob('*') if path.is_file()), ['state/qofeno.json'])
        push.assert_called_once_with(['bash', 'scripts/commit-state.sh'], check=True)

    def test_missing_malformed_and_incompatible_state_never_bootstrap_empty_history(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            store.load()
        self.path.write_text('{truncated', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            store.load()
        invalid = [[], {}, {**LEGACY, 'version': True}, {**LEGACY, 'version': 2},
                   {**LEGACY, 'history': None}, {**LEGACY, 'history': ['bad row']},
                   {**LEGACY, 'used_source_ids': None},
                   {**LEGACY, 'used_texts': [None]}, {**LEGACY, 'editorial_history': {}},
                   {**LEGACY, 'last_attempts': []}, {**LEGACY, 'pending': False}]
        # A missing permanent ledger must not be inferred from the remaining rows.
        missing = copy.deepcopy(LEGACY)
        missing.pop('used_texts')
        invalid.append(missing)
        for state in invalid:
            with self.subTest(state=state):
                self.write(state)
                with self.assertRaises(ValueError):
                    store.load()

    def test_new_publications_append_without_replacing_history(self):
        state = store.load()
        state['history'].append({'tweet_id': '124', 'text': 'New reply', 'kind': 'reply'})
        state['used_source_ids'].append('11')
        state['used_texts'].append('new-hash')
        state['editorial_history'].append({'slot': 'new-slot', 'kind': 'reply'})
        before = copy.deepcopy(state)
        with patch.object(store.subprocess, 'run') as push:
            store.save_and_push(state)
        self.assertEqual(self.saved(), before)
        self.assertEqual(state, before)
        self.assertEqual(self.saved()['history'][0], LEGACY['history'][0])
        push.assert_called_once()

    def test_truncation_and_replacement_abort_before_write_or_push(self):
        for history in ([], [{'tweet_id': 'different', 'text': 'Replacement'}]):
            state = store.load()
            state['history'] = history
            before = self.path.read_bytes()
            with self.subTest(history=history), patch.object(store.subprocess, 'run') as push:
                with self.assertRaisesRegex(ValueError, 'replace or truncate'):
                    store.save_and_push(state)
            self.assertEqual(self.path.read_bytes(), before)
            push.assert_not_called()

    def test_used_identities_cannot_be_removed(self):
        for key in store.IDENTITIES:
            state = store.load()
            state[key] = []
            before = self.path.read_bytes()
            with self.subTest(key=key), patch.object(store.subprocess, 'run') as push:
                with self.assertRaisesRegex(ValueError, 'permanent publication identities'):
                    store.save_and_push(state)
            self.assertEqual(self.path.read_bytes(), before)
            push.assert_not_called()

    def test_pending_reservation_is_saved_before_push_and_push_failure_propagates(self):
        state = store.load()
        state['pending'] = {'source_id': '10', 'text': 'Reserved contextual reply'}
        def fail_push(*args, **kwargs):
            self.assertEqual(self.saved()['pending'], state['pending'])
            raise subprocess.CalledProcessError(1, args[0])
        with patch.object(store.subprocess, 'run', side_effect=fail_push):
            with self.assertRaises(subprocess.CalledProcessError):
                store.save_and_push(state)
        self.assertEqual(self.saved()['pending'], state['pending'])

    def test_failed_serialization_or_atomic_replacement_preserves_old_file(self):
        before = self.path.read_bytes()
        state = store.load()
        state['extra'] = object()
        with patch.object(store.subprocess, 'run') as push, self.assertRaises(TypeError):
            store.save_and_push(state)
        self.assertEqual(self.path.read_bytes(), before)
        push.assert_not_called()
        with patch.object(Path, 'replace', side_effect=OSError('disk failure')), \
                patch.object(store.subprocess, 'run') as push, self.assertRaises(OSError):
            store.save_and_push(store.load())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_suffix('.tmp').exists())
        push.assert_not_called()

    def test_save_refuses_missing_ledger_and_never_creates_a_new_empty_one(self):
        self.path.unlink()
        with patch.object(store.subprocess, 'run') as push, self.assertRaises(FileNotFoundError):
            store.save_and_push(copy.deepcopy(LEGACY))
        self.assertFalse(self.path.exists())
        push.assert_not_called()

    def test_unknown_outbox_outcomes_remain_valid_but_corrupt_reservations_fail_closed(self):
        pending = {'kind': 'reply', 'slot': 'reply-slot', 'text': 'Reserved reply',
                   'event_key': 'reserved-event', 'author_id': '999',
                   'reserved_at': '2026-11-13T20:00:00+00:00', 'source': {'id': '111'}}
        self.write({**copy.deepcopy(LEGACY), 'outbox': pending})
        self.assertEqual(store.load()['outbox'], pending)
        self.assertNotIn('tweet_id', store.load()['outbox'])
        for bad in ({}, [], False, {**pending, 'tweet_id': 'unknown'},
                    {**pending, 'author_id': ''}, {**pending, 'source': None},
                    {**pending, 'reserved_at': '2026-11-13T20:00:00'},
                    {**pending, 'kind': 'follow'}):
            with self.subTest(bad=bad):
                self.write({**copy.deepcopy(LEGACY), 'outbox': bad})
                with self.assertRaises(ValueError):
                    store.load()

    def test_existing_editorial_history_cannot_be_replaced_or_truncated(self):
        original = {**copy.deepcopy(LEGACY),
                    'editorial_history': [{'slot': 'existing', 'kind': 'reply', 'tweet_id': '123'}]}
        self.write(original)
        state = store.load()
        state['editorial_history'] = []
        with patch.object(store.subprocess, 'run') as push, self.assertRaisesRegex(ValueError, 'replace or truncate'):
            store.save_and_push(state)
        self.assertEqual(self.saved(), original)
        push.assert_not_called()

    def test_account_client_uses_existing_environment_cookies_without_writing_them(self):
        environment = {'X_AUTH_TOKEN': 'fixture-auth', 'X_CT0': 'fixture-csrf'}
        before = self.path.read_bytes()
        with patch.dict(os.environ, environment, clear=True), patch.object(store, 'Client') as factory:
            connection = store.client()
        factory.assert_called_once_with('en-US', impersonate='chrome124')
        connection.set_cookies.assert_called_once_with({'auth_token': 'fixture-auth', 'ct0': 'fixture-csrf'})
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
