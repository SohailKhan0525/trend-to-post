import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import personality, question_posts, reply_writer
from scripts import voice_preview


NOW = datetime(2026, 11, 23, 11, tzinfo=timezone.utc)


class VoicePreviewTests(unittest.TestCase):
    def test_standalone_question_first_then_original_reply_fixtures(self):
        original, *cases = voice_preview.scenarios(NOW)
        self.assertEqual((original['kind'], original['id']), ('original', 'standalone_question'))
        self.assertNotIn('source', original)
        self.assertEqual({case['kind'] for case in cases}, {'reply'})
        self.assertEqual(cases[0]['id'], 'brief_direct_greeting')
        self.assertEqual(cases[1]['source']['origin'], 'discovery')
        for case in cases:
            with self.subTest(case=case['id']):
                reply_writer.check_source(case['source'], case['clock'], {})

    def test_discovery_uses_reply_planner_and_real_writer_with_memory(self):
        case = voice_preview.scenarios(NOW)[2]
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
        case = voice_preview.scenarios(NOW)[2]
        with patch.object(personality, 'choose', return_value={'action': 'silent'}) as planner, \
                patch.object(reply_writer, 'generate') as writer, patch.object(personality, 'original') as original:
            self.assertEqual(voice_preview.evaluate(case)['status'], 'silent')
        writer.assert_not_called()
        original.assert_not_called()

    def test_brief_direct_reply_keeps_real_premise_validation_and_review(self):
        case = voice_preview.scenarios(NOW)[1]
        assessment = {**dict.fromkeys(reply_writer.SOURCE_FLAGS, True),
                      'text': 'Hi.', 'premise': '', 'reply_mode': 'observation'}
        fixed = {**assessment, 'premise': 'The author greets Qofeno.'}
        review = {**dict.fromkeys(reply_writer.FLAGS, True), 'source_evidence': 'hi'}
        with patch.object(reply_writer, 'complete', side_effect=[assessment, fixed, review]) as model, \
                patch.object(personality, 'choose') as planner:
            result = voice_preview.evaluate(case)
        self.assertEqual(result['draft']['text'], 'Hi.')
        self.assertEqual(model.call_count, 3)
        self.assertIn('premise', model.call_args_list[1].args[1]['correction'])
        planner.assert_not_called()

    def test_independent_question_writer_uses_synthetic_state_and_produces_an_unpublished_draft(self):
        draft = {'text': 'If your deploy fails once a week, which check earns its place first?',
                 'premise': 'Finding deploy failures.', 'event_key': 'choosing a deployment check'}
        with patch.object(question_posts, 'generate', return_value=draft) as writer, \
                patch.object(reply_writer, 'generate') as reply, patch.object(personality, 'choose') as planner:
            result = voice_preview.evaluate(voice_preview.scenarios(NOW)[0])
        state, clock = writer.call_args.args
        self.assertEqual(clock, NOW)
        self.assertEqual(state['history'], [])
        self.assertEqual(result, {'status': 'approved_unpublished', 'draft': draft})
        reply.assert_not_called()
        planner.assert_not_called()

    def test_original_question_model_calls_are_traced_and_isolated(self):
        trace = []
        with patch.dict(os.environ, {}, clear=True), patch.object(voice_preview.cloudflare, 'complete', return_value={}), \
                voice_preview.isolated_model_calls(voice_preview.PreviewBudget(), trace):
            question_posts.complete(question_posts.WRITE, {})
            question_posts.complete(question_posts.REVIEW, {})
        self.assertEqual([row['stage'] for row in trace], ['original_question_writer', 'original_question_review'])
        with patch.dict(os.environ, {'X_AUTH_TOKEN': 'test-only'}, clear=True):
            with self.assertRaises(voice_preview.PreviewIsolationError):
                with voice_preview.isolated_model_calls(voice_preview.PreviewBudget(), []):
                    self.fail('Preview entered model scope with X credentials')

    def test_original_case_with_a_reply_parent_is_rejected_before_model_use(self):
        case = {**voice_preview.scenarios(NOW)[1], 'kind': 'original'}
        with patch.object(reply_writer, 'generate') as writer, patch.object(personality, 'choose') as planner:
            with self.assertRaises(voice_preview.PreviewIsolationError):
                voice_preview.evaluate(case)
        writer.assert_not_called()
        planner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
