import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import personality, reply_writer, sources
from scripts import voice_preview


NOW = datetime(2026, 11, 25, 11, tzinfo=timezone.utc)


def preview_case(ident):
    return next(case for case in voice_preview.scenarios(NOW) if case['id'] == ident)


class VoicePreviewTests(unittest.TestCase):
    def test_only_replies_with_valid_direct_and_discovered_sources(self):
        cases = voice_preview.scenarios(NOW)
        self.assertEqual({case['kind'] for case in cases}, {'reply'})
        self.assertEqual([case['expected_reply_style'] for case in cases[:2]], ['reasoned_answer'] * 2)
        self.assertEqual(cases[2]['id'], 'brief_direct_greeting')
        self.assertTrue(all(sources.is_reasoned_question(case['source']) for case in cases[:2]))
        self.assertFalse(sources.is_reasoned_question(cases[2]['source']))
        self.assertIn('addressed Grok, not Qofeno', cases[0]['provenance']['note'])
        for case in cases:
            with self.subTest(case=case['id']):
                reply_writer.check_source(case['source'], case['clock'], {})

    def test_discovery_uses_reply_planner_and_real_writer_with_memory(self):
        case = preview_case('discovered_agent_tests')
        plan = {'action': 'reply', 'source_id': case['source']['id'],
                'angle': 'Keep a check independent from the agent being checked.'}
        with patch.object(personality, 'choose', return_value=plan) as planner, \
                patch.object(reply_writer, 'generate', return_value={'text': 'An independent check still matters.'}) as writer, \
                patch.object(personality, 'original') as original:
            result = voice_preview.evaluate(case)
        self.assertEqual(result['status'], 'approved_unpublished')
        self.assertEqual(planner.call_args.args[4], ['reply'])
        self.assertTrue(planner.call_args.kwargs['allow_discovery_replies'])
        self.assertTrue(planner.call_args.args[1]['recent_own'])
        self.assertTrue(writer.call_args.args[1]['history'])
        self.assertEqual(writer.call_args.kwargs['angle'], plan['angle'])
        original.assert_not_called()

    def test_discovery_silence_never_falls_back_to_an_original(self):
        case = preview_case('discovered_agent_tests')
        with patch.object(personality, 'choose', return_value={'action': 'silent'}) as planner, \
                patch.object(reply_writer, 'generate') as writer, patch.object(personality, 'original') as original:
            self.assertEqual(voice_preview.evaluate(case)['status'], 'silent')
        writer.assert_not_called()
        original.assert_not_called()

    def test_brief_direct_reply_keeps_real_premise_validation_and_review(self):
        case = preview_case('brief_direct_greeting')
        assessment = {**dict.fromkeys(reply_writer.SOURCE_FLAGS, True),
                      'text': 'Hi.', 'premise': '', 'reply_mode': 'observation'}
        fixed = {**assessment, 'premise': 'The author greets Qofeno.'}
        review = {**dict.fromkeys(reply_writer.FLAGS, True), 'source_evidence': 'hi'}
        with patch.object(reply_writer, 'complete', side_effect=[assessment, fixed, review]) as model, \
                patch.object(personality, 'choose') as planner:
            result = voice_preview.evaluate(case)
        self.assertEqual(result['draft']['text'], 'Hi.')
        self.assertEqual(model.call_count, 3)
        self.assertEqual(result['selected_reply_style'], 'short')
        self.assertIn('premise', model.call_args_list[1].args[1]['correction'])
        planner.assert_not_called()

    def test_both_questions_use_actual_reasoned_writer_and_review(self):
        examples = (
            ('reasoned_ai_bottleneck', "People deciding what to do next.\nSolving a task doesn't choose the goal or get a decision acted on."),
            ('reasoned_backup_tradeoff', "I'd keep an immutable copy first: fast recovery can't restore a deleted backup.\nIf downtime is the main cost, restore speed matters more."),
        )
        for ident, answer in examples:
            case = preview_case(ident)
            assessment = {**dict.fromkeys(reply_writer.SOURCE_FLAGS, True), 'text': answer,
                          'premise': 'The reader asks for a choice and an explanation of the trade-off.',
                          'reply_mode': 'observation'}
            review = {**dict.fromkeys(reply_writer.REASONED_REVIEW_SCHEMA['properties'], True),
                      'source_evidence': case['source']['text']}
            with self.subTest(case=ident), patch.object(reply_writer, 'complete', side_effect=[assessment, review]) as model:
                result = voice_preview.evaluate(case)
            self.assertEqual(result['draft']['reply_style'], 'reasoned_answer')
            self.assertEqual(result['draft']['text'], answer)
            self.assertEqual(model.call_count, 2)
            self.assertEqual(model.call_args_list[0].kwargs['schema'], reply_writer.REASONED_MENTION_DRAFT_SCHEMA)
            self.assertEqual(model.call_args_list[1].kwargs['schema'], reply_writer.REASONED_REVIEW_SCHEMA)

    def test_reasoned_fixture_cannot_bypass_production_eligibility(self):
        with patch.object(sources, 'is_reasoned_question', return_value=False), \
                patch.object(reply_writer, 'generate') as writer:
            with self.assertRaises(voice_preview.PreviewIsolationError):
                voice_preview.evaluate(preview_case('reasoned_ai_bottleneck'))
        writer.assert_not_called()

    def test_review_trace_keeps_reasoning_flags(self):
        verdict = {'answers_question': True, 'reasoning_complete': False}
        trace = []
        with patch.dict(os.environ, {}, clear=True), patch.object(voice_preview.cloudflare, 'complete', return_value=verdict), \
                voice_preview.isolated_model_calls(voice_preview.PreviewBudget(), trace):
            reply_writer.complete('reasoned review', {}, schema=reply_writer.REASONED_REVIEW_SCHEMA)
        self.assertEqual(trace, [{'stage': 'reply_review', 'response': verdict}])

    def test_original_cases_are_rejected_before_model_use(self):
        case = {**voice_preview.scenarios(NOW)[0], 'kind': 'original'}
        with patch.object(reply_writer, 'generate') as writer, patch.object(personality, 'choose') as planner:
            with self.assertRaises(voice_preview.PreviewIsolationError):
                voice_preview.evaluate(case)
        writer.assert_not_called()
        planner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
