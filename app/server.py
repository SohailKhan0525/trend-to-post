"""Azure publisher supervisor. D1 owns the lease, ledger and write fences."""
import asyncio
import os
import re
import signal
import sys
import tempfile
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from . import cloudflare, editorial, server_ledger, store
from .ai_budget import active_pause
from .reply_schedule import next_original, reply_ready, timestamp


PULSE = 20
INBOX_INTERVAL = 30
MAX_DECISION_SECONDS = 12 * 60


async def check():
    """Exercise all three services without writing to X or changing its inbox."""
    ledger = server_ledger.connect()
    state = ledger.load() if ledger.active() else (
        store.read_state() if store.STATE.is_file() else {})
    now = store.utcnow()
    if state.get('x_pause_until') and timestamp(state['x_pause_until']) > now:
        raise RuntimeError('Server preflight deferred for the recorded X cooldown.')
    if active_pause(state, now):
        raise RuntimeError('Server preflight deferred for the recorded AI cooldown.')

    connection = store.client()
    try:
        await connection.get_notifications('Mentions', count=1)
    finally:
        await connection.http.aclose()
        if connection._curl_session is not None:
            await connection._curl_session.close()
    model = cloudflare.selected_model()
    answer = cloudflare.complete(
        'Return the JSON object {"status":"ok"} for this connection check.',
        {'check': 'Worker startup'}, temperature=0, max_tokens=32, model=model,
        schema={'type': 'object', 'properties': {'status': {'type': 'string', 'enum': ['ok']}},
                'required': ['status'], 'additionalProperties': False})
    if answer != {'status': 'ok'}:
        raise RuntimeError('Workers AI returned an invalid connection-check response.')
    store.summary(f'Server preflight passed: D1, X mentions and Workers AI ({model}).')


def next_work(state, now, inbox_in=0, original_in=0, last_mode=None):
    """Give a fresh mention first priority; alternate if both jobs remain due."""
    if state.get('x_pause_until'):
        remaining = (timestamp(state['x_pause_until']) - now).total_seconds()
        if remaining > 0:
            return None, remaining
    if state.get('outbox'):
        return 'inbox', 0
    if not editorial.delivery_open(state):
        return None, None
    pause = active_pause(state, now)
    if pause:
        return None, (pause.until - now).total_seconds()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    schedule = editorial.schedule_state(state)
    jobs = []
    if reply_ready(schedule, now):
        jobs.append(('inbox', max(0, inbox_in)))
    opportunity = next_original(schedule, now, midnight)
    if opportunity:
        jobs.append(('original', max(0, original_in, (opportunity[1] - now).total_seconds())))
    if not jobs:
        return None, (midnight - now).total_seconds()
    return min(jobs, key=lambda job: (job[1], job[0] == last_mode))


async def run_decision(ledger, instance, release, phase, stop, mode):
    # Model and ledger calls in the editor are synchronous. Keeping them in a
    # child lets the supervisor renew its lease or stop a stalled decision.
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'app', 'run', '--mode', mode)
    finished = asyncio.create_task(process.wait())
    stopping = asyncio.create_task(stop.wait())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + MAX_DECISION_SECONDS
    try:
        while True:
            done, _ = await asyncio.wait((finished, stopping), timeout=PULSE,
                                         return_when=asyncio.FIRST_COMPLETED)
            if finished in done:
                return finished.result() == 0
            if stopping in done:
                return False
            if loop.time() >= deadline:
                store.summary('Decision timed out. Checking the durable outbox before any further action.')
                return False
            if not ledger.heartbeat(instance, release, phase):
                raise RuntimeError('Server lost its publishing lease during a decision.')
    finally:
        stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(asyncio.shield(finished), 10)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
        await finished


async def supervise(ledger, instance, release, stop):
    loop = asyncio.get_running_loop()
    retry_at = inbox_at = original_at = 0
    failures = 0
    phase = 'standby'
    last_mode = None
    while not stop.is_set():
        lease_held = ledger.heartbeat(instance, release, phase)
        if not lease_held or not ledger.active():
            phase = 'standby'
            last_mode = None
            retry_at = inbox_at = original_at = failures = 0
        elif loop.time() >= retry_at:
            state = ledger.load()
            clock = loop.time()
            mode, delay = next_work(state, store.utcnow(), inbox_at - clock, original_at - clock, last_mode)
            if delay is None:
                phase, delay = 'degraded', 60
                store.summary('Publication requires reconciliation; waiting for a ledger correction.')
            elif delay == 0:
                previous_count = len(state['history'])
                success = await run_decision(ledger, instance, release, phase, stop, mode)
                if stop.is_set():
                    break
                if success:
                    # A zero exit can still leave an ambiguous send awaiting readback.
                    state = ledger.load()
                    success = editorial.delivery_open(state)
                if success:
                    failures, phase, last_mode = 0, 'active', mode
                    clock = loop.time()
                    if mode == 'inbox':
                        answered = sum(row.get('kind') == 'reply' and bool(row.get('verified_at'))
                                       for row in state['history'][previous_count:])
                        inbox_at = clock if answered >= editorial.INBOX_BATCH_SIZE else clock + INBOX_INTERVAL
                    else:
                        original_at = clock + 60
                    _, delay = next_work(state, store.utcnow(), inbox_at - clock, original_at - clock, last_mode)
                else:
                    failures = min(failures + 1, 5)
                    phase, delay = 'degraded', min(900, 60 * 2 ** failures)
            elif not failures:
                phase = 'active' if editorial.delivery_open(state) else 'degraded'
            if not ledger.heartbeat(instance, release, phase):
                raise RuntimeError('Server lost its publishing lease.')
            retry_at = loop.time() + min(delay, 900)

        # A second, independently due lane can run immediately. Idle workers
        # keep renewing the lease even when a cooldown lasts several minutes.
        if phase != 'standby' and retry_at <= loop.time():
            continue
        sleep_for = min(PULSE, max(0, retry_at - loop.time())) if phase != 'standby' else PULSE
        try:
            await asyncio.wait_for(stop.wait(), sleep_for)
        except TimeoutError:
            pass


async def serve():
    release = os.environ.get('QOFENO_RELEASE', '')
    if not re.fullmatch('[0-9a-f]{40}', release):
        raise ValueError('QOFENO_RELEASE must be the full deployed commit SHA.')
    instance = uuid4().hex
    os.environ.update(QOFENO_INSTANCE=instance, QOFENO_LEDGER='d1')
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await check()
        ledger = server_ledger.connect()
        store.summary(f'Server {release[:12]} ready. Waiting for D1 publication authority.')
        try:
            with tempfile.TemporaryDirectory(prefix='qofeno-inbox-') as directory:
                os.environ['QOFENO_INBOX_CACHE'] = os.path.join(directory, 'inbox.json')
                await supervise(ledger, instance, release, stop)
        finally:
            ledger.release(instance)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
