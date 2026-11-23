import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import personality, reply_writer
from scripts import voice_preview


NOW = datetime(2026, 11, 23, 11, tzinfo=timezone.utc)


class VoicePreviewTests(unittest.TestCase):
    def test_only_replies_with_valid_direct_and_discovered_sources(self):
        cases = voice_preview.scenarios(NOW)
        self.assertEqual({case['kind'] for case in cases}, {'reply'})
        self.assertEqual(cases[0]['id'], 'brief_direct_greeting')
        self.assertEqual(cases[1]['source']['origin'], 'discovery')
        for case in cases:
            with self.subTest(case=case['id']):
                reply_writer.check_source(case['source'], case['clock'], {})

    def test_discovery_uses_reply_planner_and_real_writer_with_memory(self):
        case = voice_preview.scenarios(NOW)[1]
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
        case = voice_preview.scenarios(NOW)[1]
        with patch.object(personality, 'choose', return_value={'action': 'silent'}) as planner, \
                patch.object(reply_writer, 'generate') as writer, patch.object(personality, 'original') as original:
            self.assertEqual(voice_preview.evaluate(case)['status'], 'silent')
        writer.assert_not_called()
        original.assert_not_called()

    def test_brief_direct_reply_keeps_real_premise_validation_and_review(self):
        case = voice_preview.scenarios(NOW)[0]
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

    def test_original_cases_are_rejected_before_model_use(self):
        case = {**voice_preview.scenarios(NOW)[0], 'kind': 'original'}
        with patch.object(reply_writer, 'generate') as writer, patch.object(personality, 'choose') as planner:
            with self.assertRaises(voice_preview.PreviewIsolationError):
                voice_preview.evaluate(case)
        writer.assert_not_called()
        planner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
