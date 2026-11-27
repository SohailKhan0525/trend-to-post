import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import movie_posts, personality, reply_writer
from scripts import voice_preview


NOW = datetime(2026, 11, 23, 11, tzinfo=timezone.utc)


class VoicePreviewTests(unittest.TestCase):
    def test_reset_and_first_movie_line_precede_reply_fixtures(self):
        reset, quote, *cases = voice_preview.scenarios(NOW)
        self.assertEqual([reset['id'], quote['id']], ['movie_reset', 'first_movie_line'])
        for original in (reset, quote):
            self.assertEqual(original['kind'], 'original')
            self.assertNotIn('source', original)
        self.assertEqual({case['kind'] for case in cases}, {'reply'})
        self.assertEqual(cases[0]['id'], 'brief_direct_greeting')
        self.assertEqual(cases[1]['source']['origin'], 'discovery')
        for case in cases:
            with self.subTest(case=case['id']):
                reply_writer.check_source(case['source'], case['clock'], {})

    def test_discovery_uses_reply_planner_and_real_writer_with_memory(self):
        case = voice_preview.scenarios(NOW)[3]
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
        case = voice_preview.scenarios(NOW)[3]
        with patch.object(personality, 'choose', return_value={'action': 'silent'}) as planner, \
                patch.object(reply_writer, 'generate') as writer, patch.object(personality, 'original') as original:
            self.assertEqual(voice_preview.evaluate(case)['status'], 'silent')
        writer.assert_not_called()
        original.assert_not_called()

    def test_brief_direct_reply_keeps_real_premise_validation_and_review(self):
        case = voice_preview.scenarios(NOW)[2]
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

    def test_movie_previews_use_the_real_selector_without_model_calls(self):
        reset, quote = voice_preview.scenarios(NOW)[:2]
        with patch.object(voice_preview.cloudflare, 'complete') as model:
            self.assertEqual(voice_preview.evaluate(reset)['draft']['text'], movie_posts.RESET_TEXT)
            result = voice_preview.evaluate(quote)
            self.assertEqual(result['status'], 'approved_unpublished')
            self.assertIn(' ~ ', result['draft']['text'])
            self.assertNotIn('\n', result['draft']['text'])
        model.assert_not_called()

    def test_a_mention_can_be_skipped_by_the_writer(self):
        greeting = voice_preview.scenarios(NOW)[2]
        with patch.object(reply_writer, 'generate', side_effect=reply_writer.MentionDeclined('No new point')):
            self.assertEqual(voice_preview.evaluate(greeting), {'status': 'silent'})

    def test_preview_refuses_live_x_credentials(self):
        with patch.dict(os.environ, {'X_AUTH_TOKEN': 'test-only'}, clear=True):
            with self.assertRaises(voice_preview.PreviewIsolationError):
                with voice_preview.isolated_model_calls(voice_preview.PreviewBudget(), []):
                    self.fail('Preview entered model scope with X credentials')

    def test_original_case_with_a_reply_parent_is_rejected_before_model_use(self):
        case = {**voice_preview.scenarios(NOW)[2], 'kind': 'original'}
        with patch.object(reply_writer, 'generate') as writer, patch.object(personality, 'choose') as planner:
            with self.assertRaises(voice_preview.PreviewIsolationError):
                voice_preview.evaluate(case)
        writer.assert_not_called()
        planner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
