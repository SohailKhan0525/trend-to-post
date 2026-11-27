import copy
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import movie_posts, reply_writer
from app.cloudflare import DraftError


NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
RESET = dict(movie_posts.RESET_DRAFT)
RESET_RECEIPT = {**RESET, 'kind': 'original', 'tweet_id': '123', 'verified_at': NOW.isoformat()}
ENTRY = {'quote': 'The curtains remember.', 'speaker': 'The usher', 'title': 'An invented test film',
         'source': 'https://example.com/sample-film'}
ANOTHER = {'quote': 'Bring an umbrella.', 'speaker': 'Her friend', 'title': 'A fictional series',
           'source': 'https://example.com/sample-series'}
DYNAMIC = {'quote': 'That ticket is yours.', 'speaker': 'The clerk', 'title': 'A sample series',
           'source': 'https://example.com/sample-dialogue?oldid=1234'}


class MoviePostsTests(unittest.TestCase):
    def setUp(self):
        self.catalog = patch.object(movie_posts, 'catalog', return_value=[ENTRY, ANOTHER]).start()
        self.addCleanup(patch.stopall)

    def test_reset_only_until_native_standalone_readback_even_after_old_posts(self):
        old = {'kind': 'original', 'text': 'Hey @grok, which?', 'tweet_id': '99', 'verified_at': NOW.isoformat()}
        for altered in ({'kind': 'reply'}, {'tweet_id': '0'}, {'verified_at': None},
                        {'text': 'A different announcement'}, {'source': {'id': '456'}}):
            with self.subTest(altered=altered):
                state = {'history': [old, {**RESET_RECEIPT, **altered}]}
                self.assertEqual(movie_posts.scheduled_draft(state, NOW), RESET)
                with self.assertRaises(DraftError):
                    movie_posts.validate(movie_posts.line_draft(ENTRY), state)
        self.assertIsNone(movie_posts.credit(RESET, {}))
        self.catalog.assert_not_called()

    def test_confirmation_switches_to_a_single_exact_sourced_line_without_a_reset_repeat(self):
        state = {'history': [RESET_RECEIPT]}
        before = copy.deepcopy(state)
        draft = movie_posts.scheduled_draft(state, NOW)
        self.assertEqual(draft['text'], '“The curtains remember.” ~ The usher')
        self.assertEqual(movie_posts.credit(draft, state), ENTRY)
        self.assertEqual(movie_posts.validate(draft, state), draft)
        self.assertEqual(state, before)
        with self.assertRaises(DraftError):
            movie_posts.validate(RESET, state)

    def test_reserved_or_ambiguous_reset_blocks_all_new_content(self):
        for state in ({'outbox': {**RESET, 'kind': 'original'}}, {'pause_reason': 'Uncertain'},
                      {'used_texts': [reply_writer.fingerprint(RESET['text'])]},
                      {'used_events': [reply_writer.fingerprint(RESET['event_key'])]},
                      {'delivery_rejections': [{**RESET, 'error': 'CouldNotTweet', 'outcome': 'unconfirmed'}]},
                      {'used_texts': [reply_writer.fingerprint(RESET['text'])], 'delivery_rejections': [
                          {**RESET, 'error': 'TooManyRequests'}, {**RESET, 'error': 'CouldNotTweet'}]}):
            with self.subTest(state=list(state)), self.assertRaises(DraftError):
                movie_posts.scheduled_draft(state, NOW)

    def test_proven_rejection_or_pre_send_expiry_keeps_reset_first(self):
        for reason in ('TooManyRequests', 'Forbidden', 'ExpiredBeforeSend'):
            state = {'used_texts': [reply_writer.fingerprint(RESET['text'])],
                     'used_events': [reply_writer.fingerprint(RESET['event_key'])],
                     'delivery_rejections': [{**RESET, 'error': reason}]}
            with self.subTest(reason=reason):
                self.assertEqual(movie_posts.scheduled_draft(state, NOW), RESET)
                with self.assertRaises(DraftError):
                    movie_posts.validate(movie_posts.line_draft(ANOTHER), state)

    def test_unlisted_or_misattributed_lines_and_private_metadata_never_validate(self):
        state = {'history': [RESET_RECEIPT]}
        draft = movie_posts.line_draft(ENTRY)
        for fields in ({'text': '“Something new.” ~ The usher'}, {'text': '“The curtains remember.” ~ Another'},
                       {'text': draft['text'] + '\nFollow for more'}, {'event_key': 'a changed event'},
                       {'premise': 'A different film'}, {'unexpected': 'field'}):
            with self.subTest(fields=fields), self.assertRaises(DraftError):
                movie_posts.validate({**draft, **fields}, state)

    def test_each_permanent_identity_and_rejection_blocks_replays_across_edits(self):
        draft = movie_posts.line_draft(ENTRY)
        for added in ({'used_events': [reply_writer.fingerprint(draft['event_key'])]},
                      {'used_texts': [reply_writer.fingerprint(draft['text'])]},
                      {'delivery_rejections': [{**draft, 'error': 'Forbidden'}]},
                      {'history': [RESET_RECEIPT, {'kind': 'original', 'text': '"The curtains remember!" ~ Someone else'}]},
                      {'history': [RESET_RECEIPT, {'text': 'The curtains remember.'}]}):
            state = {'history': [RESET_RECEIPT], **added}
            with self.subTest(keys=list(added)):
                self.assertEqual(movie_posts.scheduled_draft(state, NOW), movie_posts.line_draft(ANOTHER))
                with self.assertRaises(DraftError):
                    movie_posts.validate(draft, state)

    def test_recently_quoted_title_yields_to_a_different_film_or_series(self):
        same_title = {**ENTRY, 'quote': 'A different line from its second scene.'}
        self.catalog.return_value = [ENTRY, same_title, ANOTHER]
        state = {'history': [RESET_RECEIPT, {**movie_posts.line_draft(ENTRY), 'kind': 'original',
                                           'verified_at': NOW.isoformat(), 'movie_credit': ENTRY}]}
        self.assertEqual(movie_posts.scheduled_draft(state, NOW), movie_posts.line_draft(ANOTHER))

    def test_validation_rejects_multiline_overlength_handles_and_missing_credit(self):
        for changed in ({'quote': 'Part one\npart two'}, {'speaker': '@advertiser'}, {'title': ''},
                        {'quote': '長' * 150}, {'source': 'http://example.com/sample-film'},
                        {'source': 'https://example.com/'}, {'source': 'https://me@example.com/q'},
                        {'source': 'https://[invalid/url'}, {'source': None}):
            with self.subTest(changed=changed):
                self.assertFalse(movie_posts.vetted({**ENTRY, **changed}))

    def test_exhaustion_fetches_only_verified_new_dialogue_and_reuses_the_vetted_candidate(self):
        self.catalog.return_value = []
        state = {'history': [RESET_RECEIPT]}
        def find(history, *, now, accept):
            self.assertFalse(accept({**DYNAMIC, 'quote': 'bad\nquote'}))
            self.assertTrue(accept(DYNAMIC))
            return DYNAMIC
        provider = SimpleNamespace(find_quote=Mock(side_effect=find))
        with patch.dict(sys.modules, {'app.movie_catalog': provider}):
            draft = movie_posts.scheduled_draft(state, NOW)
            self.assertEqual(state['movie_quote_candidate'], DYNAMIC)
            self.assertEqual(movie_posts.validate(draft, state), draft)
            self.assertEqual(movie_posts.scheduled_draft(state, NOW), draft)
        provider.find_quote.assert_called_once()

    def test_missing_or_duplicate_replenishment_cannot_create_a_post(self):
        self.catalog.return_value = []
        state = {'history': [RESET_RECEIPT, {**movie_posts.line_draft(ENTRY), 'kind': 'original',
                                           'verified_at': NOW.isoformat(), 'movie_credit': ENTRY}]}
        previous = copy.deepcopy(state)
        for result in (None, ENTRY, {**DYNAMIC, 'source': ''}):
            provider = SimpleNamespace(find_quote=Mock(return_value=result))
            with self.subTest(result=result), patch.dict(sys.modules, {'app.movie_catalog': provider}), self.assertRaises(DraftError):
                movie_posts.scheduled_draft(state, NOW)
        self.assertEqual(state, previous)
