import copy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from app import model_check, writer
from app.ai_budget import ACTIVE
from app.cloudflare import DraftError


NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


class ModelCheckTests(unittest.TestCase):
    def setUp(self):
        self.state = {'version': 1, 'history': [{'tweet_id': '123'}],
                      'used_source_ids': ['456'], 'used_urls': ['https://example.com/post'],
                      'used_events': ['previous-event'], 'used_texts': ['previous-text'],
                      'pending': None}
        self.before = copy.deepcopy(self.state)
        self.persisted = []
        self.provider_calls = 0
        self.modes = []

        def provider(system, data, **kwargs):
            self.assertEqual(kwargs['model'], model_check.MODEL)
            budget = ACTIVE.get()
            self.assertIsNotNone(budget)
            reservation = budget.reserve(model_check.MODEL,
                                         {'messages': [{'role': 'user', 'content': 'fixture'}],
                                          'max_tokens': 100})
            self.assertEqual(self.persisted[-1]['ai_usage']['requests'], self.provider_calls + 1)
            self.provider_calls += 1
            budget.settle(reservation, {'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
            return {'ok': True}

        self.provider = provider
        self.patches = [
            patch.object(model_check.queue, 'load', return_value=self.state),
            patch.object(model_check.queue, 'utcnow', return_value=NOW),
            patch.object(model_check.queue, 'save_and_push',
                         side_effect=lambda state: self.persisted.append(copy.deepcopy(state))),
            patch.object(model_check.queue, 'summary'),
            patch.object(model_check.queue, 'client'),
            patch.object(model_check.queue, 'send_once', new_callable=AsyncMock),
            patch.object(writer, 'complete', self.provider),
            patch.object(writer, 'select_format', return_value='original-selector', create=True),
            patch.object(writer, 'validate', side_effect=lambda draft, source: draft, create=True),
        ]
        self.mocks = [item.start() for item in self.patches]
        for item in self.patches:
            self.addCleanup(item.stop)
        self.original_selector = writer.select_format

    def generate(self, source, state, now):
        self.assertEqual(now, NOW)
        self.assertEqual(source, model_check.fixture_source(NOW))
        self.assertEqual(state['history'], [])
        self.assertEqual(state['used_source_ids'], [])
        mode = writer.select_format({}, state)
        self.modes.append(mode)
        writer.complete('fixture check', {})
        # Even if the writer changes its supplied state, production is isolated.
        state['history'].append({'tweet_id': 'fictional'})
        return {'text': '\U0001f602' if mode == 'emoji_question' else 'Fixture commentary.',
                'reply': 'Which local test would you run first?' if mode == 'emoji_question' else '',
                'format': mode, 'event_key': 'Atlas DevKit local test release',
                'premise': 'Atlas DevKit supports 8 local tests in parallel.'}

    def assert_isolated(self):
        for key, value in self.before.items():
            self.assertEqual(self.state[key], value)
        self.mocks[4].assert_not_called()  # X session client
        self.mocks[5].assert_not_awaited()  # Publication transport
        self.assertIs(writer.complete, self.provider)
        self.assertIs(writer.select_format, self.original_selector)
        self.assertIsNone(ACTIVE.get())

    def test_three_formats_share_real_budget_but_never_publication_state(self):
        with patch.object(writer, 'generate', side_effect=self.generate, create=True):
            result = model_check.check()
        self.assertTrue(result['passed'])
        self.assertEqual(self.modes, list(model_check.FORMATS))
        self.assertEqual(result['requests'], 3)
        self.assertEqual(self.provider_calls, 3)
        self.assertEqual(self.persisted[-1]['ai_usage'], self.state['ai_usage'])
        self.assertEqual(self.state['ai_usage']['neurons_reserved'], 6)
        self.assert_isolated()

    def test_one_rejection_fails_check_without_hiding_other_case_results(self):
        def generate(source, state, now):
            draft = self.generate(source, state, now)
            if draft['format'] == 'commentary':
                raise DraftError('Fixture failed a grounded-copy check')
            return draft
        with patch.object(writer, 'generate', side_effect=generate, create=True), \
             self.assertRaises(model_check.ModelCheckError):
            model_check.check()
        self.assertEqual(self.modes, list(model_check.FORMATS))
        report = self.mocks[3].call_args.args[0]
        self.assertIn('Fixture failed a grounded-copy check', report)
        self.assertIn('"passed": false', report)
        self.assert_isolated()

    def test_request_bound_stops_even_if_a_writer_calls_the_model_repeatedly(self):
        def generate(*args):
            for _ in range(model_check.MAX_REQUESTS + 1):
                writer.complete('fixture check', {})
        with patch.object(writer, 'generate', side_effect=generate, create=True), \
             self.assertRaises(model_check.ModelCheckError):
            model_check.check()
        self.assertEqual(self.provider_calls, model_check.MAX_REQUESTS)
        self.assertIn('request bound', self.mocks[3].call_args.args[0])
        self.assert_isolated()

    def test_existing_budget_pause_is_failure_without_provider_or_x_call(self):
        self.state['ai_pause'] = {'reason': 'Cloudflare quota',
                                  'until': (NOW + timedelta(hours=1)).isoformat()}
        with patch.object(writer, 'generate', side_effect=self.generate, create=True), \
             self.assertRaises(model_check.ModelCheckError):
            model_check.check()
        self.assertEqual(self.provider_calls, 0)
        self.assertIn('spending or provider limit', self.mocks[3].call_args.args[0])
        self.assert_isolated()

    def test_unexpected_error_exposes_only_type_and_restores_hooks(self):
        with patch.object(writer, 'generate', side_effect=RuntimeError('Bearer fixture-private-value'), create=True), \
             self.assertRaises(model_check.ModelCheckError):
            model_check.check()
        report = self.mocks[3].call_args.args[0]
        self.assertIn('RuntimeError', report)
        self.assertNotIn('fixture-private-value', report)
        self.assertEqual(self.provider_calls, 0)
        self.assert_isolated()

    def test_different_format_cannot_count_as_passing_required_case(self):
        def generate(source, state, now):
            return {**self.generate(source, state, now), 'format': 'emoji'}
        with patch.object(writer, 'generate', side_effect=generate, create=True), \
             self.assertRaises(model_check.ModelCheckError):
            model_check.check()
        self.assertIn('different format', self.mocks[3].call_args.args[0])
        self.assert_isolated()


if __name__ == '__main__':
    unittest.main()
