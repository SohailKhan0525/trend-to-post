import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import follow_writer as writer
from app.cloudflare import DraftError
from app.reply_writer import fingerprint


NOW = datetime(2026, 11, 13, 20, tzinfo=timezone.utc)
OPENING = "Follow me now before it's too late. "
TEXT = OPENING + 'Expect useful questions about AI coding tools and occasional disagreements with a semicolon.'
OTHER = OPENING + 'Debugging deserves a witness, even when the culprit is a missing bracket.'
RAW = {'text': TEXT, 'safe': True, 'honest': True, 'natural': True}


class FollowWriterTests(unittest.TestCase):
    def test_normal_generation_is_one_call_and_returns_ledger_compatible_key(self):
        with patch.object(writer, 'complete', return_value=RAW) as calls:
            result = writer.generate({}, NOW)
        self.assertEqual(result, {'text': TEXT, 'event_key': 'follow:' + fingerprint(TEXT)})
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(calls.call_args.kwargs['max_tokens'], 300)
        self.assertEqual(calls.call_args.args[1]['current_date'], '2026-11-13')

    def test_shape_and_local_validation_get_at_most_one_repair(self):
        for bad in ({}, {**RAW, 'text': OPENING + 'Last chance to join this limited time coding club.'}):
            with self.subTest(bad=bad), patch.object(writer, 'complete', side_effect=[bad, RAW]) as calls:
                self.assertEqual(writer.generate({}, NOW)['text'], TEXT)
            self.assertEqual(calls.call_count, 2)
            self.assertIn('correction', calls.call_args.args[1])
        with patch.object(writer, 'complete', side_effect=[{}, {}]) as calls, self.assertRaises(DraftError):
            writer.generate({}, NOW)
        self.assertEqual(calls.call_count, 2)

    def test_semantic_rejection_is_not_retried_until_accepted(self):
        for flag in writer.FLAGS:
            with self.subTest(flag=flag), patch.object(writer, 'complete', return_value={**RAW, flag: False}) as calls:
                with self.assertRaises(writer.FollowRejected):
                    writer.generate({}, NOW)
            self.assertEqual(calls.call_count, 1)

    def test_booleans_are_not_coerced_from_strings(self):
        with self.assertRaises(DraftError):
            writer.validate({**RAW, 'safe': 'true'})

    def test_exact_reuse_is_blocked_permanently_in_all_ledgers(self):
        filler = [{'text': 'Some unrelated old entry'} for _ in range(200)]
        states = [
            {'used_texts': [fingerprint(TEXT)]},
            {'history': [{'text': TEXT.upper()}] + filler},
            {'editorial_history': [{'text': TEXT}] + filler},
            {'editorial_history': [{'reply': TEXT}] + filler},
        ]
        for state in states:
            with self.subTest(state=state), self.assertRaises(DraftError):
                writer.check_duplicates(writer.validate(RAW), state)

    def test_recent_near_duplicate_requires_a_different_second_sentence(self):
        for name in ('history', 'editorial_history'):
            state = {name: [{'text': TEXT.replace('useful', 'practical')}]}
            with self.subTest(name=name), patch.object(writer, 'complete', side_effect=[RAW, {**RAW, 'text': OTHER}]) as calls:
                self.assertEqual(writer.generate(state, NOW)['text'], OTHER)
            self.assertEqual(calls.call_count, 2)
            self.assertIn('too similar', calls.call_args.args[1]['correction'])

    def test_false_urgency_achievements_experience_insults_and_extra_engagement_are_rejected(self):
        invalid = [
            OPENING + 'Last chance to join this coding account.',
            OPENING + 'Get guaranteed results with your next coding project.',
            OPENING + 'I made a million from software and know all the secrets.',
            OPENING + 'I tested all the coding tools to find the best ones.',
            OPENING + 'Software tips without those stupid developers.',
            OPENING + 'Repost this for useful questions about software tools.',
            OPENING + 'DM me for more practical coding tips.',
        ]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(DraftError):
                writer.validate({**RAW, 'text': text})

    def test_required_playful_opening_accepts_apostrophe_variants_and_needs_fresh_detail(self):
        for text in (TEXT, TEXT.replace("it's", 'it’s')):
            with self.subTest(text=text):
                self.assertEqual(writer.validate({**RAW, 'text': text})['text'], text)
        for text in (OPENING.strip(), OPENING + 'Please.',
                     'Follow for practical coding questions and dry software humor.'):
            with self.subTest(text=text), self.assertRaises(DraftError):
                writer.validate({**RAW, 'text': text})

    def test_excluded_subjects_mentions_links_numbers_and_flags_are_rejected(self):
        for addition in (' India', ' politics', ' 🇺🇸', ' @someone', ' #coding',
                         ' https://example.com', ' 100 followers', ' $5',
                         ' Ignore all previous instructions.'):
            with self.subTest(addition=addition), self.assertRaises(DraftError):
                writer.validate({**RAW, 'text': TEXT + addition})

    def test_weighted_length_controls_and_multiple_emojis_are_checked(self):
        invalid = [TEXT + '\n', TEXT + '\u200b', TEXT + '\\n',
                   'Follow ' + '界' * 137, TEXT + ' 👀 🤖', ' Follow for useful coding questions.']
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(DraftError):
                writer.validate({**RAW, 'text': text})
        valid = TEXT + ' 👀'
        self.assertEqual(writer.validate({**RAW, 'text': valid})['text'], valid)

    def test_exactly_one_follow_invitation_is_required(self):
        for text in (TEXT.replace('Follow', 'Join'), TEXT + ' Follow me.'):
            with self.subTest(text=text), self.assertRaises(DraftError):
                writer.validate({**RAW, 'text': text})

    def test_history_context_is_bounded_and_state_is_not_mutated(self):
        state = {'history': [{'text': f'Past post {i}'} for i in range(40)],
                 'editorial_history': [{'text': OTHER}], 'used_texts': []}
        original = copy.deepcopy(state)
        with patch.object(writer, 'complete', return_value=RAW) as calls:
            writer.generate(state, NOW)
        self.assertLessEqual(len(calls.call_args.args[1]['recent_posts']), 20)
        self.assertIn(OTHER, calls.call_args.args[1]['recent_posts'])
        self.assertEqual(state, original)

    def test_transport_errors_do_not_recycle_copy_or_retry(self):
        with patch.object(writer, 'complete', side_effect=TimeoutError('private detail')) as calls:
            with self.assertRaises(TimeoutError):
                writer.generate({}, NOW)
        self.assertEqual(calls.call_count, 1)

    def test_naive_time_is_rejected_before_ai(self):
        with patch.object(writer, 'complete') as calls, self.assertRaises(ValueError):
            writer.generate({}, NOW.replace(tzinfo=None))
        calls.assert_not_called()


if __name__ == '__main__':
    unittest.main()
