"""Run one GitHub Actions publishing cycle with an exclusive D1 lease."""
import asyncio
import json
import os
import re
import signal
import sys
from contextlib import suppress
from uuid import uuid4

from . import server_ledger, store
from .memory import MemoryUnavailable

PULSE = 20
MAX_DECISION_SECONDS = 15 * 60


async def run_decision(ledger, instance, release, stop):
    # Model and ledger calls in the editor are synchronous. Keeping them in a
    # child lets the supervisor renew its lease or stop a stalled decision.
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'app', 'run', '--mode', 'scheduled')
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
            if not ledger.heartbeat(instance, release, 'active'):
                raise RuntimeError('GitHub lost its publishing lease during a decision.')
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


async def publish():
    release = os.environ.get('QOFENO_RELEASE', '')
    if not re.fullmatch(r'[a-f0-9]{40}', release):
        raise ValueError('Pass the full release commit used by GitHub Actions.')
    ledger = server_ledger.connect()
    instance = 'github-' + uuid4().hex
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + server_ledger.LEASE_SECONDS + PULSE
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        while not ledger.claim_github(instance, release):
            if stop.is_set() or loop.time() >= deadline:
                raise MemoryUnavailable('Another publisher still owns the D1 lease. No X request sent.')
            try:
                await asyncio.wait_for(stop.wait(), PULSE)
            except TimeoutError:
                pass
        try:
            if stop.is_set():
                raise MemoryUnavailable('Run cancelled before publication.')
            os.environ.update(QOFENO_INSTANCE=instance, QOFENO_LEDGER='d1')
            state = ledger.load()
            before = len(state['history'])
            store.summary(f'GitHub acquired D1 publishing authority. Preserved {before} publication records.')
            if not await run_decision(ledger, instance, release, stop):
                raise MemoryUnavailable('Publishing cycle did not finish. Its outbox is preserved.')
            state = ledger.load()
            for row in state['history'][before:]:
                if row.get('verified_at') and str(row.get('tweet_id', '')).isdigit():
                    store.summary(f"Verified {row.get('kind')}: https://x.com/{store.ACCOUNT}/status/{row['tweet_id']}")
                    # Inspect the public wording alongside its receipt. Keep multiline posts on one log line.
                    print('Published copy: ' + json.dumps(row.get('text', ''), ensure_ascii=False), flush=True)
            if state.get('outbox') or state.get('pause_reason'):
                raise MemoryUnavailable('A publication awaits reconciliation. The reservation is preserved.')
            ledger.finish_github(instance)
            store.summary(f'Cycle complete. D1 contains {len(state["history"])} publication records.')
        finally:
            ledger.release(instance)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
