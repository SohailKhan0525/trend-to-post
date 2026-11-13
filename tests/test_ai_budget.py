import io
import json
import os
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from app import ai_budget
from app.cloudflare import complete


MODEL = '@cf/zai-org/glm-4.7-flash'
NOW = datetime(2026, 11, 11, 12, 34, tzinfo=timezone.utc)
PAYLOAD = {'messages': [{'role': 'user', 'content': 'Review this source.'}], 'max_tokens': 100}
ENV = {'CLOUDFLARE_ACCOUNT_ID': 'a' * 32, 'CLOUDFLARE_API_TOKEN': 'fixture-token',
       'QOFENO_AI_MODEL': MODEL}


def response(usage=None):
    output = {'response': {'ok': True}}
    if usage is not None:
        output['usage'] = usage
    return io.StringIO(json.dumps({'success': True, 'result': output}))


def rate_limit(message):
    body = json.dumps({'errors': [{'message': message}]}).encode()
    return urllib.error.HTTPError('https://api.cloudflare.com', 429, 'Too many requests', {}, io.BytesIO(body))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'state.json'
        self.state = {}
        self.now = NOW
        self.budget = ai_budget.Budget(self.state, self.persist, lambda: self.now)
        environment = patch.dict(os.environ, ENV, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def persist(self, state):
        self.path.write_text(json.dumps(state))

    def saved(self):
        return json.loads(self.path.read_text())

    def scope(self):
        return ai_budget.budget_scope(self.state, self.persist, lambda: self.now)

    def test_reservation_is_on_disk_before_network_request(self):
        def provider(*args, **kwargs):
            saved = self.saved()['ai_usage']
            self.assertEqual(saved['requests'], 1)
            self.assertGreater(saved['neurons_reserved'], 0)
            self.assertEqual(saved['neurons_reserved'], saved['hour_neurons_reserved'])
            self.assertEqual(saved['model'], MODEL)
            return response()
        with self.scope(), patch('urllib.request.urlopen', side_effect=provider) as network:
            self.assertEqual(complete('review', {'text': 'Fixture source'}, max_tokens=100), {'ok': True})
        network.assert_called_once()
        self.assertIsNone(ai_budget.ACTIVE.get())

    def test_failed_persistence_prevents_any_provider_request(self):
        with ai_budget.budget_scope(self.state, lambda state: (_ for _ in ()).throw(OSError('disk failed')), lambda: self.now),\
             patch('urllib.request.urlopen') as network, self.assertRaises(OSError):
            complete('review', {}, max_tokens=100)
        network.assert_not_called()
        self.assertIsNone(ai_budget.ACTIVE.get())

    def test_ambiguous_network_failure_keeps_the_full_durable_reservation(self):
        with self.scope(), patch('urllib.request.urlopen', side_effect=TimeoutError('provider timed out')) as network,\
             self.assertRaises(TimeoutError):
            complete('review', {}, max_tokens=100)
        network.assert_called_once()
        self.assertEqual(self.saved()['ai_usage'], self.state['ai_usage'])
        self.assertGreater(self.saved()['ai_usage']['neurons_reserved'], 0)
        self.assertIsNone(ai_budget.ACTIVE.get())

    def test_reported_token_usage_refunds_only_the_unused_reservation(self):
        reservation = self.budget.reserve(MODEL, PAYLOAD)
        reserved = self.saved()['ai_usage']['neurons_reserved']
        self.assertGreater(reserved, 2)
        self.budget.settle(reservation, {'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
        # Published model rates yield ceil(0.55 + 0.728) = 2 neurons.
        self.assertEqual(self.state['ai_usage']['neurons_reserved'], 2)
        self.assertEqual(self.state['ai_usage']['hour_neurons_reserved'], 2)
        # A crash before the next save conservatively keeps the durable maximum.
        self.assertEqual(self.saved()['ai_usage']['neurons_reserved'], reserved)
        self.budget.reserve(MODEL, PAYLOAD)
        self.assertEqual(self.saved()['ai_usage']['neurons_reserved'], 2 + reservation[0])

    def test_unusable_provider_usage_keeps_reservation_without_crashing(self):
        reservation = self.budget.reserve(MODEL, PAYLOAD)
        original = dict(self.state['ai_usage'])
        invalid = [None, [], 'unknown', 3, {},
                   {'prompt_tokens': True, 'completion_tokens': 20},
                   {'prompt_tokens': -1, 'completion_tokens': 20},
                   {'prompt_tokens': '100', 'completion_tokens': 20},
                   {'prompt_tokens': 100, 'completion_tokens': 20.5}]
        for usage in invalid:
            with self.subTest(usage=usage):
                self.budget.settle(reservation, {'usage': usage})
                self.assertEqual(self.state['ai_usage'], original)

    def test_usage_above_estimate_is_charged_and_cannot_create_a_refund(self):
        reservation = self.budget.reserve(MODEL, PAYLOAD)
        self.budget.settle(reservation, {'usage': {'prompt_tokens': 10_000, 'completion_tokens': 1000}})
        self.assertEqual(self.state['ai_usage']['neurons_reserved'], 92)
        self.assertEqual(self.state['ai_usage']['hour_neurons_reserved'], 92)

    def test_hourly_cap_pauses_until_next_hour_and_preserves_daily_spending(self):
        self.state['ai_usage'] = {'day': NOW.date().isoformat(), 'hour': NOW.strftime('%Y-%m-%dT%H'),
                                 'neurons_reserved': 1000, 'hour_neurons_reserved': ai_budget.HOURLY_LIMIT, 'requests': 9}
        with self.assertRaises(ai_budget.AIPaused) as raised:
            self.budget.reserve(MODEL, PAYLOAD)
        boundary = NOW.replace(minute=0) + timedelta(hours=1)
        self.assertEqual(raised.exception.until, boundary)
        self.assertEqual(self.saved()['ai_usage']['requests'], 9)
        self.assertEqual(self.saved()['ai_pause']['until'], boundary.isoformat())
        self.now = boundary
        amount, _, _, _ = self.budget.reserve(MODEL, PAYLOAD)
        self.assertEqual(self.saved()['ai_usage']['neurons_reserved'], 1000 + amount)
        self.assertEqual(self.saved()['ai_usage']['hour_neurons_reserved'], amount)
        self.assertNotIn('ai_pause', self.saved())

    def test_daily_cap_pauses_until_utc_midnight_then_resets_counters(self):
        self.state['ai_usage'] = {'day': NOW.date().isoformat(), 'hour': NOW.strftime('%Y-%m-%dT%H'),
                                 'neurons_reserved': ai_budget.DAILY_LIMIT, 'hour_neurons_reserved': 0, 'requests': 99}
        with self.assertRaises(ai_budget.AIPaused) as raised:
            self.budget.reserve(MODEL, PAYLOAD)
        boundary = datetime(2026, 11, 12, tzinfo=timezone.utc)
        self.assertEqual(raised.exception.until, boundary)
        self.assertEqual(self.saved()['ai_usage']['requests'], 99)
        self.now = boundary
        amount, _, _, _ = self.budget.reserve(MODEL, PAYLOAD)
        usage = self.saved()['ai_usage']
        self.assertEqual(usage['day'], '2026-11-12')
        self.assertEqual(usage['requests'], 1)
        self.assertEqual(usage['neurons_reserved'], amount)
        self.assertEqual(usage['hour_neurons_reserved'], amount)
        self.assertNotIn('ai_pause', self.saved())

    def test_exact_daily_limit_is_allowed_but_next_request_is_not(self):
        amount = self.budget.reserve(MODEL, PAYLOAD)[0]
        self.state['ai_usage'].update(neurons_reserved=ai_budget.DAILY_LIMIT - amount,
                                      hour_neurons_reserved=0)
        self.budget.reserve(MODEL, PAYLOAD)
        self.assertEqual(self.saved()['ai_usage']['neurons_reserved'], ai_budget.DAILY_LIMIT)
        with self.assertRaises(ai_budget.AIPaused):
            self.budget.reserve(MODEL, PAYLOAD)

    def test_daily_quota_429_persists_pause_blocks_retries_and_resumes_at_midnight(self):
        error = rate_limit('You have used up your daily free allocation of 10,000 neurons.')
        with self.scope(), patch('urllib.request.urlopen', side_effect=[error, response()]) as network, redirect_stdout(io.StringIO()):
            with self.assertRaises(ai_budget.AIPaused) as raised:
                complete('review', {}, max_tokens=100)
            boundary = datetime(2026, 11, 12, tzinfo=timezone.utc)
            self.assertEqual(raised.exception.until, boundary)
            self.assertEqual(self.saved()['ai_pause']['until'], boundary.isoformat())
            self.now = boundary - timedelta(microseconds=1)
            with self.assertRaises(ai_budget.AIPaused):
                complete('review', {}, max_tokens=100)
            self.assertEqual(network.call_count, 1)
            self.now = boundary
            self.assertEqual(complete('review', {}, max_tokens=100), {'ok': True})
            self.assertEqual(network.call_count, 2)
        self.assertNotIn('ai_pause', self.saved())
        self.assertEqual(self.saved()['ai_usage']['requests'], 1)

    def test_other_429_persists_fifteen_minute_cooldown_without_retries(self):
        with self.scope(), patch('urllib.request.urlopen', side_effect=[rate_limit('Too many requests'), response()]) as network,\
             redirect_stdout(io.StringIO()):
            with self.assertRaises(ai_budget.AIPaused) as raised:
                complete('review', {}, max_tokens=100)
            boundary = NOW + timedelta(minutes=15)
            self.assertEqual(raised.exception.until, boundary)
            self.assertEqual(self.saved()['ai_pause']['until'], boundary.isoformat())
            self.now = boundary - timedelta(seconds=1)
            with self.assertRaises(ai_budget.AIPaused):
                complete('review', {}, max_tokens=100)
            self.assertEqual(network.call_count, 1)
            self.now = boundary
            self.assertEqual(complete('review', {}, max_tokens=100), {'ok': True})
            self.assertEqual(network.call_count, 2)

    def test_unpriced_model_fails_before_network_or_spending_reservation(self):
        with self.scope(), patch.dict(os.environ, {'QOFENO_AI_MODEL': '@cf/example/unpriced'}),\
             patch('urllib.request.urlopen') as network, self.assertRaises(ValueError):
            complete('review', {}, max_tokens=100)
        network.assert_not_called()
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
