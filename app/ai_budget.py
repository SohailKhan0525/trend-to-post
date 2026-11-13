"""Durable AI spending estimates and provider cooldowns, separate from sent posts."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import json
import math

# Cloudflare's published neurons per million input/output tokens, checked 2026-11-11.
RATES = {
    '@cf/qwen/qwen3-30b-a3b-fp8': (4625, 30475),
    '@cf/meta/llama-3.3-70b-instruct-fp8-fast': (26668, 204805),
    '@cf/meta/llama-3.1-8b-instruct-fp8-fast': (4119, 34868),
    '@cf/zai-org/glm-4.7-flash': (5500, 36400),
    '@cf/google/gemma-4-26b-a4b-it': (9091, 27273),
}
DAILY_LIMIT = 9000
HOURLY_LIMIT = 400
ACTIVE = ContextVar('qofeno_ai_budget', default=None)


class AIPaused(Exception):
    def __init__(self, reason, until):
        self.reason, self.until = reason, until
        super().__init__(reason)


def midnight(now):
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def active_pause(state, now):
    pause = state.get('ai_pause')
    if not pause:
        return None
    until = datetime.fromisoformat(pause['until'])
    if now < until:
        return AIPaused(pause['reason'], until)
    return None


class Budget:
    def __init__(self, state, persist, clock):
        self.state, self.persist, self.clock = state, persist, clock

    def pause(self, reason, until):
        self.state['ai_pause'] = {'reason': reason, 'until': until.isoformat()}
        self.persist(self.state)
        raise AIPaused(reason, until)

    def reserve(self, model, payload):
        now = self.clock().astimezone(timezone.utc)
        pause = active_pause(self.state, now)
        if pause:
            raise pause
        self.state.pop('ai_pause', None)
        if model not in RATES:
            raise ValueError('Configure a documented Workers AI model cost before publishing')
        usage = self.state.get('ai_usage', {})
        if usage.get('day') != now.date().isoformat():
            usage = {'day': now.date().isoformat(), 'neurons_reserved': 0, 'requests': 0}
            self.state['ai_usage'] = usage
        hour = now.strftime('%Y-%m-%dT%H')
        if usage.get('hour') != hour:
            usage.update(hour=hour, hour_neurons_reserved=0)
        input_rate, output_rate = RATES[model]
        # UTF-8 bytes plus framing conservatively overestimate input tokens.
        # Refund only when the provider returns usable token counts; otherwise keep it.
        prompt_bound = len(json.dumps(payload['messages'], ensure_ascii=False).encode()) + 512
        amount = math.ceil((prompt_bound * input_rate + payload['max_tokens'] * output_rate) / 1_000_000)
        if usage['neurons_reserved'] + amount > DAILY_LIMIT:
            self.pause('Local daily AI budget reserved; waiting for the next UTC day.', midnight(now))
        if usage['hour_neurons_reserved'] + amount > HOURLY_LIMIT:
            self.pause('Local hourly AI budget reserved; spreading research across the day.',
                       now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        usage['neurons_reserved'] += amount
        usage['hour_neurons_reserved'] += amount
        usage['requests'] += 1
        usage['model'] = model
        # Reserve durably before the request. A crash must not erase spending.
        self.persist(self.state)
        return (amount, model, usage['day'], hour)

    def settle(self, reservation, output):
        reported = output.get('usage', {})
        if not isinstance(reported, dict):
            return
        prompt, completion = reported.get('prompt_tokens'), reported.get('completion_tokens')
        if any(type(value) is not int or value < 0 for value in (prompt, completion)):
            return
        amount, model, day, hour = reservation
        rates = RATES[model]
        actual = math.ceil((prompt * rates[0] + completion * rates[1]) / 1_000_000)
        usage = self.state['ai_usage']
        if usage['day'] == day:
            usage['neurons_reserved'] += actual - amount
        if usage['hour'] == hour:
            usage['hour_neurons_reserved'] += actual - amount
        # Caller persists this adjustment on the next reservation or editorial result.


@contextmanager
def budget_scope(state, persist, clock):
    token = ACTIVE.set(Budget(state, persist, clock))
    try:
        yield
    finally:
        ACTIVE.reset(token)
