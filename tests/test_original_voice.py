import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import personality
from app.cloudflare import DraftError


NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
PLAN = {'action': 'original', 'source_id': '',
        'angle': 'A startup can choose a smaller promise so its users can judge whether it kept it.'}
GOOD = {'publish': True, 'fresh_premise': True, 'natural_voice': True,
        'distinct_expression': True, 'grounded': True,
        'grand_feat_vs_annoyance': False, 'invented_firsthand_usage': False}
DRAFT = {'worthwhile': True,
         'text': 'I trust a small promise I can check more than a grand vision I cannot.',
         'event_key': 'Small promises let customers judge whether they were kept',
         'premise': 'A limited, checkable promise can earn more trust than an expansive vision.'}


def published(text, premise='A previously expressed point.'):
    return {'kind': 'original', 'verified_at': NOW.isoformat(),
            'text': text, 'premise': premise, 'event_key': premise}


class OriginalVoiceTests(unittest.TestCase):
    def test_memory_keeps_full_verified_original_points_and_ignores_reply_volume(self):
        originals = [published(f'Published original thought {i}.', f'Underlying point {i}.') for i in range(14)]
        history = [*originals, *[{'kind': 'reply', 'text': 'A separate person said something.'}] * 150,
                   {'kind': 'original', 'text': 'Unsent draft should never become public memory.'}]
        before = copy.deepcopy(history)
        memory = personality.original_memory(history)
        self.assertEqual(len(memory['recent_originals']), 8)
        self.assertEqual(memory['recent_originals'][0]['premise'], 'Underlying point 13.')
        self.assertEqual(memory['recent_originals'][-1]['premise'], 'Underlying point 6.')
        self.assertEqual(history, before)

    def test_history_projection_has_a_byte_budget_and_counts_openings_beyond_shown_examples(self):
        history = [published('The best thing about a meme is its timing.') for _ in range(3)]
        history += [published(f'Unrelated opening {i}: ' + '界' * 200, 'A compact point ' * 20) for i in range(9)]
        memory = personality.original_memory(history)
        self.assertIn('the best thing', memory['overused_openings'])
        self.assertLessEqual(sum(len(value.encode('utf-8')) for row in memory['recent_originals']
                                 for value in row.values()), 2400)
        self.assertLessEqual(len(memory['recent_originals']), 8)

    def test_chosen_topic_keeps_complete_short_public_inspiration_without_copying_memory(self):
        source = {'id': '123', 'author_id': '22', 'author': 'compiler_dev',
                  'text': 'The compiler finished before the progress bar had a useful update.',
                  'created_at': NOW.isoformat(), 'views': 20000, 'source_ids': ['123'], 'urls': []}
        plan = {**PLAN, 'angle': 'A progress message should name the remaining stage, not pretend to know the duration.'}
        state = {'actions_inbox_since': NOW.isoformat()}
        memory = {'recent_own': [{'kind': 'original', 'text': 'Duplicated old post.'}]}
        with patch.object(personality, 'complete', return_value=plan) as model:
            selected = personality.choose([source], memory, state, NOW, ['original'])
        self.assertEqual(selected['public_inspiration'], [{'id': '123', 'text': source['text']}])
        self.assertNotIn('memory', model.call_args.args[1])
        with patch.object(personality, 'complete', side_effect=[DRAFT, GOOD]) as model:
            personality.original(selected, memory, state, NOW)
        for call in model.call_args_list:
            self.assertEqual(call.args[1]['public_inspiration'], selected['public_inspiration'])
            self.assertNotIn('recent_own', call.args[1])
        oversized = {**source, 'text': source['text'] * 20}
        with patch.object(personality, 'complete', return_value=plan):
            selected = personality.choose([oversized], memory, state, NOW, ['original'])
        self.assertEqual(selected, plan)

    def test_repeated_opening_is_reconsidered_without_adding_a_joke_requirement(self):
        history = [published(text) for text in (
            'The best thing about a small launch is a readable support queue.',
            'The best thing about an error message is knowing where to look next.',
            'The best thing about a meme is recognizing the situation before the caption.',
        )]
        state = {'actions_inbox_since': NOW.isoformat(), 'history': history}
        before = copy.deepcopy(state)
        repeated = {**DRAFT, 'text': 'The best thing about a startup promise is being able to check it.'}
        with patch.object(personality, 'complete', side_effect=[repeated, DRAFT, GOOD]) as model:
            result = personality.original(PLAN, {}, state, NOW)
        self.assertEqual(result['text'], DRAFT['text'])
        self.assertEqual(model.call_count, 3)
        self.assertEqual(model.call_args_list[1].args[1]['rejected_original'], repeated['text'])
        self.assertEqual(model.call_args_list[0].args[1]['original_memory']['overused_openings'], ['the best thing'])
        self.assertEqual(state, before)

    def test_sincere_longer_thought_can_pass_without_a_joke_or_fixed_sentence_count(self):
        text = ('I prefer a startup pitch that says who should not buy the product. '
                'It gives the promise a boundary. A clear limit makes it easier to decide whether '
                'the product fits, and it tells me what the company is willing to leave out.')
        self.assertGreater(len(text), 220)
        self.assertLessEqual(len(text), 280)
        draft = {**DRAFT, 'text': text}
        state = {'actions_inbox_since': NOW.isoformat()}
        with patch.object(personality, 'complete', side_effect=[draft, GOOD]) as model:
            result = personality.original(PLAN, {}, state, NOW)
        self.assertEqual(result['text'], text)
        self.assertEqual(model.call_count, 2)

    def test_review_cannot_publish_unsupported_capability_even_if_publish_is_true(self):
        state = {'actions_inbox_since': NOW.isoformat()}
        unsupported = {**DRAFT, 'text': 'The latest coding assistant can fix every migration without human review.'}
        with patch.object(personality, 'complete', side_effect=[unsupported, {**GOOD, 'grounded': False}]) as model:
            with self.assertRaisesRegex(DraftError, 'unsupported original claims'):
                personality.original(PLAN, {}, state, NOW)
        self.assertEqual(model.call_count, 2)

    def test_no_sources_or_new_idea_can_end_in_silence_without_a_template_fallback(self):
        state = {'actions_inbox_since': NOW.isoformat()}
        silent = {'action': 'silent', 'source_id': '', 'angle': ''}
        with patch.object(personality, 'complete', return_value=silent) as model:
            self.assertEqual(personality.choose([], {}, state, NOW, ['original']), silent)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(model.call_args.args[1]['conversations'], [])
        self.assertEqual(model.call_args.args[1]['original_memory']['recent_originals'], [])

    def test_original_only_planning_has_no_reply_routing_prompt(self):
        state = {'actions_inbox_since': NOW.isoformat()}
        with patch.object(personality, 'complete', return_value=PLAN) as model:
            self.assertEqual(personality.choose([], {}, state, NOW, ['original']), PLAN)
        self.assertEqual(model.call_args.args[0], personality.ORIGINAL_DECIDE)
        self.assertNotIn('allowed_reply_ids', model.call_args.args[0])
        self.assertNotIn('conversation_role', model.call_args.args[0])
        self.assertEqual(model.call_args.kwargs['schema']['properties']['action']['enum'], ['original', 'silent'])

    def test_long_formal_draft_gets_one_edit_of_the_same_point_before_review(self):
        formal = {**DRAFT, 'text': (
            'I believe that making a narrow promise constitutes a more dependable basis for earning '
            'customer trust because people can compare the specific commitment with what they receive '
            'and reach a meaningful judgment about whether the company has delivered.')}
        state = {'actions_inbox_since': NOW.isoformat()}
        with patch.object(personality, 'complete', side_effect=[formal, DRAFT, GOOD]) as model:
            result = personality.original(PLAN, {}, state, NOW)
        self.assertEqual(result['text'], DRAFT['text'])
        self.assertEqual(model.call_count, 3)
        rewrite = model.call_args_list[1].args[1]
        self.assertEqual(rewrite['angle'], PLAN['angle'])
        self.assertEqual(rewrite['rejected_original'], formal['text'])
        self.assertIn('Keep the same point', rewrite['rewrite_instruction'])

    def test_review_wordiness_failure_rewrites_same_point_and_preserves_inspiration(self):
        plan = {**PLAN, 'public_inspiration': [{'id': '123', 'text': 'A small promise is easier to check.'}]}
        wordy = {**DRAFT, 'text': 'A smaller product promise gives people a clearer basis on which to decide whether a company has actually delivered what it said it would.'}
        state = {'actions_inbox_since': NOW.isoformat()}
        with patch.object(personality, 'complete', side_effect=[wordy, {**GOOD, 'publish': False, 'natural_voice': False},
                                                               DRAFT, GOOD]) as model:
            personality.original(plan, {}, state, NOW)
        revised = model.call_args_list[2].args[1]
        self.assertEqual(revised['angle'], PLAN['angle'])
        self.assertEqual(revised['public_inspiration'], plan['public_inspiration'])
        self.assertIn('rewrite_instruction', revised)

    def test_repeat_wordy_revision_stops_after_one_edit(self):
        draft = {**DRAFT, 'text': ('Everyone should remember that the ability to export information '
                                 'is important because it gives people flexibility when their '
                                 'requirements change and demonstrates that the company respects that choice.')}
        revised = {**draft, 'text': draft['text'].replace('Everyone should remember', 'You need to remember')}
        state = {'actions_inbox_since': NOW.isoformat()}
        with patch.object(personality, 'complete', side_effect=[draft, revised]) as model:
            with self.assertRaises(personality.OriginalNeedsRewrite):
                personality.original(PLAN, {}, state, NOW)
        self.assertEqual(model.call_count, 2)

    def test_semantic_repetition_is_rejected_even_when_surface_wording_changes(self):
        state = {'actions_inbox_since': NOW.isoformat(), 'history': [published(
            'A narrow product promise gives customers something they can actually test.',
            'A limited, checkable promise can earn more trust than an expansive vision.')]}
        paraphrase = {**DRAFT, 'premise': 'Narrow claims are easier for customers to assess.'}
        repeated_review = {**GOOD, 'publish': False, 'fresh_premise': False}
        with patch.object(personality, 'complete', side_effect=[paraphrase, repeated_review,
                                                               {**paraphrase, 'text': 'Small startup claims are easier to judge than sweeping visions.'},
                                                               repeated_review]) as model:
            with self.assertRaises(personality.OriginalNeedsNewAngle):
                personality.original(PLAN, {}, state, NOW)
        self.assertEqual(model.call_count, 4)


if __name__ == '__main__':
    unittest.main()
