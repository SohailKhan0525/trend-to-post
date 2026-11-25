import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import question_posts, reply_writer
from app.cloudflare import DraftError


NOW = datetime(2026, 11, 25, 10, tzinfo=timezone.utc)
PREFIX = 'Hey @grok, '
CLOSING = 'Be BRUTALLY HONEST.'
QUESTION = {'setup': 'an AI tutor can interrupt once.',
            'question': 'Which mistake earns that interruption?',
            'choices': ['Bad arithmetic', 'A missed step', 'A wrong unit', 'A lucky guess'],
            'alternative': 'Or some other mistake',
            'premise': 'When an AI tutor should spend its one interruption',
            'event_key': 'AI tutor limited correction priority'}
ARCHIVE = {'setup': 'a chat archive can save one layer.',
           'question': 'Which part keeps the conversation legible?',
           'choices': ['Messages', 'Reactions', 'Timestamps', 'Reply links'],
           'alternative': 'Or something else entirely',
           'premise': 'Which part of a chat archive preserves its meaning',
           'event_key': 'Chat archive preservation choice'}


def rendered(raw):
    a, b, c, d = raw['choices']
    text = (f"Hey @grok, {raw['setup']}\n\n{raw['question']}\n\n"
            f"1) {a}    2) {b}\n3) {c}    4) {d}\n\n{raw['alternative']}\n\nBe BRUTALLY HONEST.")
    return {'text': text, 'premise': raw['premise'], 'event_key': raw['event_key']}


GOOD = {key: True for key in question_posts.CHECKS}


class QuestionPostsTests(unittest.TestCase):
    def test_writer_composes_the_complete_public_question_before_independent_review(self):
        previous = {**rendered(ARCHIVE), 'kind': 'original', 'verified_at': NOW.isoformat()}
        reply = {'kind': 'reply', 'text': 'Private reply fixture.', 'verified_at': NOW.isoformat()}
        state = {'history': [previous, reply]}
        before = copy.deepcopy(state)
        with patch.object(question_posts, 'complete', side_effect=[QUESTION, GOOD]) as model:
            self.assertEqual(question_posts.generate(state, NOW), rendered(QUESTION))
        write, review = model.call_args_list
        self.assertEqual(write.args[0], question_posts.WRITE)
        self.assertEqual(review.args[0], question_posts.REVIEW)
        self.assertEqual(review.args[1]['draft'], rendered(QUESTION))
        self.assertEqual(review.kwargs['temperature'], 0)
        self.assertTrue(write.args[1]['recent_originals'])
        self.assertNotIn(reply['text'], str(model.call_args_list))
        self.assertEqual(state, before)

    def test_template_has_five_blocks_four_numbered_choices_and_one_grok_address(self):
        final = question_posts.compose(QUESTION)
        self.assertEqual(final, rendered(QUESTION))
        self.assertEqual(len(final['text'].split('\n\n')), 5)
        self.assertEqual(final['text'].count('@'), 1)
        self.assertEqual(final['text'].count('?'), 1)
        self.assertEqual(question_posts.validate(final, {}), final)
        # The source image leaves extra space between the numbered rows.
        spaced = {**final, 'text': final['text'].replace('\n3) ', '\n\n3) ').replace('    2)', '         2)')}
        self.assertEqual(question_posts.validate(spaced, {}), spaced)

    def test_missing_duplicate_extra_or_injected_choices_cannot_reach_publication(self):
        choices = QUESTION['choices']
        for options in (choices[:3], choices + ['Another option'], [*choices[:3], choices[0]],
                        [*choices[:3], choices[0].upper()], [*choices[:3], 'Bad\noption'],
                        [*choices[:3], '@another'], [*choices[:3], '#technology']):
            with self.subTest(choices=options), self.assertRaises(DraftError):
                question_posts.validate(question_posts.compose({**QUESTION, 'choices': options}), {})

    def test_final_readback_shape_requires_each_section_numbers_and_exact_closing(self):
        final = rendered(QUESTION)
        text = final['text']
        for changed in (text.replace(PREFIX, '', 1), text.replace(PREFIX, 'Hey @Grok, ', 1),
                        text.replace('    4) A lucky guess', ''), text.replace('3) A wrong unit', '2) A wrong unit'),
                        text.replace('4) A lucky guess', '4) Bad arithmetic'),
                        text.replace('Which mistake earns that interruption?', 'Why? Which mistake?'),
                        text.replace('\n\n' + QUESTION['alternative'], ''), text.replace(CLOSING, ''),
                        text.replace(CLOSING, 'Be brutally honest.'), text.replace('interruption?', 'interruption'),
                        text.replace('an AI tutor', '@another AI tutor'), text.replace('A wrong unit', '＠another'),
                        text.replace('A missed step', '#AI'), text.replace('A missed step', 'https://example.com'),
                        text.replace(QUESTION['setup'], 'I tested three AI tutors.'),
                        text.replace(QUESTION['question'], 'Which mistake deserves it? Wrong answers only.'),
                        text.replace('A missed step', 'A missed step\u202e')):
            with self.subTest(text=changed), self.assertRaises(DraftError):
                question_posts.validate({**final, 'text': changed}, {})

    def test_280_weighted_characters_include_every_piece_of_rendered_text(self):
        base = rendered(QUESTION)
        remaining = 280 - reply_writer.weighted_length(base['text'])
        self.assertGreater(remaining, 0)
        setup = QUESTION['setup'] + ' ' + '界' * ((remaining - 1) // 2) + 'x' * ((remaining - 1) % 2)
        raw = {**QUESTION, 'setup': setup}
        expected = rendered(raw)
        self.assertEqual(reply_writer.weighted_length(expected['text']), 280)
        self.assertEqual(question_posts.validate(question_posts.compose(raw), {}), expected)
        with self.assertRaises(DraftError):
            question_posts.validate(question_posts.compose({**raw, 'setup': setup + 'x'}), {})

    def test_public_mention_cannot_appear_in_metadata(self):
        final = rendered(QUESTION)
        for field in ('premise', 'event_key'):
            with self.subTest(field=field), self.assertRaises(DraftError):
                question_posts.validate({**final, field: '@grok ' + final[field]}, {})

    def test_prior_public_text_and_event_fingerprints_block_reuse(self):
        final = rendered(QUESTION)
        old = {**final, 'text': final['text'].removeprefix(PREFIX), 'kind': 'original', 'verified_at': NOW.isoformat()}
        states = [{'used_texts': [reply_writer.fingerprint(final['text'])]},
                  {'used_texts': [reply_writer.fingerprint(final['text'].removeprefix(PREFIX))]},
                  {'used_events': [reply_writer.fingerprint(QUESTION['event_key'])]},
                  {'history': [old, *[{'kind': 'reply', 'text': 'Unrelated short reply.'} for _ in range(170)]]}]
        for state in states:
            with self.subTest(state_keys=list(state)), self.assertRaises(DraftError):
                question_posts.validate(final, state)

    def test_similar_questions_rejected_even_when_metadata_or_greeting_changes(self):
        original = rendered(QUESTION)
        revised = rendered({**QUESTION, 'question': 'Which mistake really earns that interruption?',
                            'premise': 'How learners should receive feedback', 'event_key': 'feedback for learning'})
        for text in (original['text'], original['text'].removeprefix(PREFIX)):
            old = {**original, 'text': text, 'kind': 'original', 'verified_at': NOW.isoformat()}
            state = {'history': [old]}
            with self.subTest(text=text), self.assertRaises(DraftError):
                question_posts.validate(revised, state)
            self.assertEqual(question_posts.validate(rendered(ARCHIVE), state), rendered(ARCHIVE))

    def test_related_historical_questions_reach_review_beyond_recent_memory(self):
        old = {'text': 'Should an AI tutor interrupt when it catches an arithmetic error?',
               'premise': 'The cost of stopping a student mid-solution', 'kind': 'original',
               'verified_at': NOW.isoformat()}
        later = [{**rendered(ARCHIVE), 'text': f'Older unrelated note about a chat archive {i}.',
                  'kind': 'original', 'verified_at': NOW.isoformat()} for i in range(14)]
        with patch.object(question_posts, 'complete', side_effect=[QUESTION, GOOD]) as model:
            question_posts.generate({'history': [old, *later]}, NOW)
        self.assertIn(old['text'], model.call_args.args[1]['related_originals'])

    def test_corrected_format_or_revision_must_pass_its_own_review(self):
        with patch.object(question_posts, 'complete', side_effect=[QUESTION, {**GOOD, 'fresh_idea': False},
                                                                  ARCHIVE, GOOD]) as model:
            self.assertEqual(question_posts.generate({}, NOW), rendered(ARCHIVE))
        rewrite = model.call_args_list[2].args[1]
        self.assertIn('fresh_idea', rewrite['revision_needed'])
        self.assertEqual(rewrite['previous_attempt'], ' '.join([QUESTION['setup'], QUESTION['question'], *QUESTION['choices']]))
        self.assertNotIn(PREFIX, rewrite['previous_attempt'])
        self.assertNotIn(CLOSING, rewrite['previous_attempt'])
        self.assertEqual(model.call_args_list[3].args[1]['draft']['text'], rendered(ARCHIVE)['text'])

    def test_model_numbered_labels_are_normalized_before_review_and_final_labels_are_strict(self):
        numbered = {**QUESTION, 'choices': [f'{i}. {label}' for i, label in enumerate(QUESTION['choices'], 1)]}
        with patch.object(question_posts, 'complete', side_effect=[numbered, GOOD]) as model:
            self.assertEqual(question_posts.generate({}, NOW), rendered(QUESTION))
        self.assertEqual(model.call_args_list[1].args[1]['draft'], rendered(QUESTION))
        malformed = {**rendered(QUESTION), 'text': rendered(QUESTION)['text'].replace('1) Bad', '1) 1. Bad')}
        with self.assertRaises(DraftError):
            question_posts.validate(malformed, {})

    def test_misplaced_ordinal_or_address_returns_only_a_field_specific_hint_before_repair(self):
        for bad_fields, field in (
            ({'setup': 'Hey @grok, ' + QUESTION['setup']}, 'setup'),
            ({'setup': QUESTION['question']}, 'setup'),
            ({'question': 'Which option wins? 1. Bad arithmetic'}, 'question'),
            ({'alternative': 'Or something else?'}, 'alternative'),
            ({'choices': ['2. Bad arithmetic', *QUESTION['choices'][1:]]}, 'choice 1'),
        ):
            with self.subTest(field=field), patch.object(question_posts, 'complete',
                    side_effect=[{**QUESTION, **bad_fields}, ARCHIVE, GOOD]) as model:
                self.assertEqual(question_posts.generate({}, NOW), rendered(ARCHIVE))
            self.assertEqual(model.call_count, 3)
            rewrite = model.call_args_list[1].args[1]
            self.assertIn(field, rewrite['revision_needed'])
            self.assertEqual(rewrite['previous_attempt'], '')
            self.assertNotIn(PREFIX, str(rewrite))
            self.assertNotIn(CLOSING, str(rewrite))
            self.assertEqual(model.call_args_list[2].args[1]['draft'], rendered(ARCHIVE))

    def test_yes_no_stem_with_four_categories_must_be_rewritten_before_review(self):
        bad = {'setup': 'if an AI assistant could handle your inbox',
               'question': 'would you use it to automate your emails?',
               'choices': ['Daily routine tasks', 'Meetings', 'Project updates', 'Newsletters'],
               'alternative': 'Or something else in your inbox',
               'premise': 'Which part of an email inbox to delegate first',
               'event_key': 'AI assistant delegation for email categories'}
        fixed = {**bad, 'question': 'which emails would you hand over first?',
                 'choices': ['Receipts', 'Scheduling requests', 'Project updates', 'Newsletters']}
        self.assertLessEqual(reply_writer.weighted_length(rendered(bad)['text']), 280)
        with self.assertRaises(DraftError):
            question_posts.validate(rendered(bad), {})
        with patch.object(question_posts, 'complete', side_effect=[bad, fixed, GOOD]) as model:
            self.assertEqual(question_posts.generate({}, NOW), rendered(fixed))
        self.assertEqual([call.args[0] for call in model.call_args_list],
                         [question_posts.WRITE, question_posts.WRITE, question_posts.REVIEW])
        self.assertEqual(model.call_args_list[-1].args[1]['draft'], rendered(fixed))

    def test_missing_failed_or_non_boolean_review_flag_blocks_publishing(self):
        for flag in ('addresses_grok', 'four_plausible_choices', 'invites_alternative', 'grounded'):
            self.assertIn(flag, question_posts.CHECKS)
            for value in (False, 'yes', None):
                verdict = {**GOOD, flag: value}
                if value is None:
                    del verdict[flag]
                with self.subTest(flag=flag, value=value), patch.object(question_posts, 'complete',
                        side_effect=[QUESTION, verdict, ARCHIVE, verdict]) as model:
                    with self.assertRaises(DraftError):
                        question_posts.generate({}, NOW)
                    self.assertEqual(model.call_count, 4)

    def test_repeating_a_rejected_question_never_reaches_another_review(self):
        with patch.object(question_posts, 'complete', side_effect=[QUESTION, {**GOOD, 'natural_voice': False},
                                                                  QUESTION]) as model:
            with self.assertRaisesRegex(DraftError, 'repeats the rejected draft'):
                question_posts.generate({}, NOW)
        self.assertEqual(model.call_count, 3)
