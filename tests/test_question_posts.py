import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import question_posts, reply_writer
from app.cloudflare import DraftError


NOW = datetime(2026, 11, 25, 10, tzinfo=timezone.utc)
QUESTION = {'text': 'An AI tutor can spot every mistake but can only interrupt once. Which mistake earns that interruption?',
            'premise': 'When an AI tutor should spend its one interruption',
            'event_key': 'AI tutor limited correction priority'}
CHOICES = {'text': 'A chat archive can keep one layer. Which would you miss most?\n\n1) The messages\n2) The reactions\n3) The timestamps',
           'premise': 'Which part of a chat archive preserves its meaning',
           'event_key': 'Chat archive preservation choice'}
GOOD = {key: True for key in question_posts.CHECKS}


class QuestionPostsTests(unittest.TestCase):
    def test_model_writes_the_question_and_an_independent_reviewer_reads_the_public_text(self):
        previous = {**CHOICES, 'kind': 'original', 'verified_at': NOW.isoformat()}
        reply = {'kind': 'reply', 'text': 'Private reply fixture.', 'verified_at': NOW.isoformat()}
        state = {'history': [previous, reply]}
        before = copy.deepcopy(state)
        with patch.object(question_posts, 'complete', side_effect=[QUESTION, GOOD]) as model:
            self.assertEqual(question_posts.generate(state, NOW), QUESTION)
        write, review = model.call_args_list
        self.assertEqual(write.args[0], question_posts.WRITE)
        self.assertEqual(review.args[0], question_posts.REVIEW)
        self.assertEqual(review.args[1]['draft'], QUESTION)
        self.assertEqual(review.kwargs['temperature'], 0)
        self.assertEqual(write.args[1]['recent_originals'][0]['text'], CHOICES['text'])
        self.assertNotIn(reply['text'], str(model.call_args_list))
        self.assertEqual(state, before)

    def test_choices_can_follow_main_question_and_one_line_is_allowed(self):
        self.assertEqual(question_posts.validate(CHOICES, {}), CHOICES)
        self.assertEqual(question_posts.validate(QUESTION, {}), QUESTION)

    def test_formatting_blocks_answers_bait_trailing_questions_and_unsupported_framing(self):
        for text in ('Energy. That is the real bottleneck.', 'Which tradeoff matters? Why?',
                     'Which feature would you keep? Be brutally honest.',
                     '@grok Which feature would you keep and why?',
                     'I tested three different meme generators. Which would you keep?',
                     'Which option fits this tiny screen best? ' + '界' * 125,
                     'Which do you prefer for this hypothetical chat archive?\u202e'):
            with self.subTest(text=text), self.assertRaises(DraftError):
                question_posts.validate({**QUESTION, 'text': text}, {})

    def test_rechecks_reserved_hashes_and_old_verified_premises_amid_reply_volume(self):
        old = {**QUESTION, 'kind': 'original', 'verified_at': NOW.isoformat()}
        states = [{'used_texts': [reply_writer.fingerprint(QUESTION['text'])]},
                  {'used_events': [reply_writer.fingerprint(QUESTION['event_key'])]},
                  {'history': [old, *[{'kind': 'reply', 'event_key': 'a different answer',
                                      'text': 'Unrelated short reply.'} for _ in range(170)]]}]
        for state in states:
            with self.subTest(state_keys=list(state)), self.assertRaises(DraftError):
                question_posts.validate(QUESTION, state)

    def test_related_historical_questions_reach_review_after_falling_out_of_recent_memory(self):
        old = {'text': 'Should an AI tutor interrupt when it catches an arithmetic error?',
               'premise': 'The cost of stopping a student mid-solution', 'kind': 'original',
               'verified_at': NOW.isoformat()}
        later = [{**CHOICES, 'text': f'Older unrelated note about a chat archive {i}.',
                  'kind': 'original', 'verified_at': NOW.isoformat()} for i in range(14)]
        with patch.object(question_posts, 'complete', side_effect=[QUESTION, GOOD]) as model:
            question_posts.generate({'history': [old, *later]}, NOW)
        self.assertIn(old['text'], model.call_args.args[1]['related_originals'])

    def test_revision_must_pass_its_own_review(self):
        with patch.object(question_posts, 'complete', side_effect=[QUESTION, {**GOOD, 'fresh_idea': False},
                                                                  CHOICES, GOOD]) as model:
            self.assertEqual(question_posts.generate({}, NOW), CHOICES)
        rewrite = model.call_args_list[2].args[1]
        self.assertIn('fresh_idea', rewrite['revision_needed'])
        self.assertEqual(rewrite['previous_attempt'], QUESTION['text'])
        self.assertEqual(model.call_args_list[3].args[1]['draft']['text'], CHOICES['text'])

    def test_semantic_rejection_cannot_be_overridden_by_the_writer(self):
        for incorrect in ({**GOOD, 'grounded': False}, {**GOOD, 'fresh_idea': 'yes'}, {'publish': True}):
            with self.subTest(verdict=incorrect), patch.object(question_posts, 'complete',
                    side_effect=[QUESTION, incorrect, CHOICES, incorrect]) as model:
                with self.assertRaises(DraftError):
                    question_posts.generate({}, NOW)
                self.assertEqual(model.call_count, 4)

    def test_repeating_a_rejected_question_never_reaches_another_review(self):
        with patch.object(question_posts, 'complete', side_effect=[QUESTION, {**GOOD, 'natural_voice': False},
                                                                  QUESTION]) as model:
            with self.assertRaisesRegex(DraftError, 'repeats the rejected draft'):
                question_posts.generate({}, NOW)
        self.assertEqual(model.call_count, 3)
