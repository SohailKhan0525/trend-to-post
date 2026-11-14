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
LOCAL_HOURLY_PAUSE = 'Local hourly AI budget reserved; spreading research across the day.'
ACTIVE = ContextVar('qofeno_ai_budget', default=None)


class AIPaused(Exception):
    def __init__(self, reason, until):
        self.reason, self.until = reason, until
        super().__init__(reason)


class AIRequestTooLarge(AIPaused):
    """This reservation exceeds hourly headroom. Smaller requests may still fit."""

    def __init__(self, reason, until, needed, remaining):
        self.needed, self.remaining = needed, remaining
        super().__init__(reason, until)


def estimate_reservation(model, payload):
    """Upper bound in neurons for a request, with no ledger changes or I/O."""
    if model not in RATES:
        raise ValueError('Configure a documented Workers AI model cost before publishing')
    output_tokens = payload['max_tokens']
    if type(output_tokens) is not int or output_tokens <= 0:
        raise ValueError('Expected a positive integer generation token limit')
    input_rate, output_rate = RATES[model]
    # UTF-8 bytes plus framing conservatively overestimate input tokens.
    prompt_bound = len(json.dumps(payload['messages'], ensure_ascii=False).encode()) + 512
    return math.ceil((prompt_bound * input_rate + output_tokens * output_rate) / 1_000_000)


def midnight(now):
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def next_hour(now):
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _legacy_hourly_headroom(state, now, until):
    """Recognize an old local pause caused by a single large estimate."""
    usage = state.get('ai_usage')
    if not isinstance(usage, dict) or until != next_hour(now):
        return False
    if usage.get('day') != now.date().isoformat() or usage.get('hour') != now.strftime('%Y-%m-%dT%H'):
        return False
    hourly = usage.get('hour_neurons_reserved')
    daily = usage.get('neurons_reserved')
    return (type(hourly) is int and 0 < hourly < HOURLY_LIMIT
            and type(daily) is int and hourly <= daily < DAILY_LIMIT)


def active_pause(state, now):
    pause = state.get('ai_pause')
    if not pause:
        return None
    until = datetime.fromisoformat(pause['until'])
    if now < until:
        if pause['reason'] == LOCAL_HOURLY_PAUSE and _legacy_hourly_headroom(state, now.astimezone(timezone.utc), until):
            return None
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
        amount = estimate_reservation(model, payload)
        # Calculate changes in a copy so an unaffordable request cannot change
        # the ledger, even across an hour boundary or an expired legacy pause.
        usage = dict(self.state.get('ai_usage', {}))
        if usage.get('day') != now.date().isoformat():
            usage = {'day': now.date().isoformat(), 'neurons_reserved': 0, 'requests': 0}
        hour = now.strftime('%Y-%m-%dT%H')
        if usage.get('hour') != hour:
            usage.update(hour=hour, hour_neurons_reserved=0)
        if any(type(usage.get(key)) is not int or usage[key] < 0
               for key in ('neurons_reserved', 'hour_neurons_reserved', 'requests')):
            raise ValueError('Invalid AI usage counters')
        if usage['neurons_reserved'] + amount > DAILY_LIMIT:
            self.pause('Local daily AI budget reserved; waiting for the next UTC day.', midnight(now))
        remaining = HOURLY_LIMIT - usage['hour_neurons_reserved']
        if remaining <= 0:
            self.pause(LOCAL_HOURLY_PAUSE, next_hour(now))
        if amount > remaining:
            reason = f'This AI request needs {amount} reserved neurons; {remaining} remain this UTC hour.'
            raise AIRequestTooLarge(reason, next_hour(now), amount, remaining)
        usage['neurons_reserved'] += amount
        usage['hour_neurons_reserved'] += amount
        usage['requests'] += 1
        usage['model'] = model
        self.state['ai_usage'] = usage
        self.state.pop('ai_pause', None)
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
