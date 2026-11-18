"""Keep one bounded Actions runner awake and hand off to the next run.

GitHub cron is a fallback. The durable publisher remains responsible for leases,
quotas, reconciliation and duplicate protection on every individual wake.
"""
import argparse
import asyncio
import json
import math
import os
import re
import signal
import sys
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import dataclass


DEFAULT_DURATION = 55 * 60
DEFAULT_INTERVAL = 10 * 60
MAX_PUBLISH_SECONDS = 21 * 60
RELAY_TIMEOUT = 15
RELAY_ATTEMPTS = 3
REPOSITORY = 'SohailKhan0525/trend-to-post'
WORKFLOW = 'publish.yml'


class RunnerConfigurationError(ValueError):
    pass


class RelayError(RuntimeError):
    """Only a safe, body-free description may be stored in this exception."""


@dataclass(frozen=True)
class Settings:
    duration: int = DEFAULT_DURATION
    interval: int = DEFAULT_INTERVAL

    def __post_init__(self):
        if not 60 <= self.duration <= DEFAULT_DURATION:
            raise RunnerConfigurationError('Duration must be between 60 and 3300 seconds.')
        if not 60 <= self.interval <= DEFAULT_INTERVAL or self.interval > self.duration:
            raise RunnerConfigurationError('Interval must be between 60 and 600 seconds and no longer than the duration.')


def validate_environment(environment):
    if environment.get('GITHUB_ACTIONS') != 'true':
        raise RunnerConfigurationError('The continuous publisher runs only in GitHub Actions.')
    if environment.get('GITHUB_REPOSITORY') != REPOSITORY or environment.get('GITHUB_REF') != 'refs/heads/main':
        raise RunnerConfigurationError('The publisher requires its configured repository and main branch.')
    if not re.fullmatch(r'[a-f0-9]{40}', environment.get('QOFENO_RELEASE', '')):
        raise RunnerConfigurationError('Pass the full checked-out release commit.')
    token = environment.get('GH_TOKEN', '')
    if not token.strip() or '\n' in token or '\r' in token:
        raise RunnerConfigurationError('The runner requires its job-scoped GH_TOKEN.')
    return token


def publisher_environment(environment):
    # The GitHub write token must never reach generation or publication code.
    return {key: value for key, value in environment.items() if key not in ('GH_TOKEN', 'GITHUB_TOKEN')}


async def pause_until(stop, seconds):
    if stop.is_set():
        return False
    try:
        await asyncio.wait_for(stop.wait(), max(0, seconds))
        return False
    except TimeoutError:
        return not stop.is_set()


def _signal_process_group(process, sig):
    with suppress(ProcessLookupError):
        os.killpg(process.pid, sig)


async def publish_once(environment, stop):
    if stop.is_set():
        return False
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'app', 'publish', env=publisher_environment(environment),
        start_new_session=True,
    )
    finished = asyncio.create_task(process.wait())
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            (finished, stopping), timeout=MAX_PUBLISH_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if finished in done:
            return finished.result() == 0
        if not stop.is_set():
            print('::error::Publishing cycle exceeded its time limit; its durable reservation is preserved.', flush=True)
        return False
    finally:
        stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
        clean_group = process.returncode != 0
        if process.returncode is None:
            _signal_process_group(process, signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.shield(finished), 15)
            except TimeoutError:
                pass
        if clean_group:
            # The parent may have exited after SIGTERM while a decision child
            # ignored it. Reap the entire original process group regardless.
            _signal_process_group(process, signal.SIGKILL)
        await finished


def dispatch_successor(token):
    request = urllib.request.Request(
        f'https://api.github.com/repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/dispatches',
        data=json.dumps({'ref': 'main'}).encode(), method='POST',
        headers={
            'Accept': 'application/vnd.github+json',
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'qofeno-workflow-runner',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=RELAY_TIMEOUT) as response:
            if response.status not in (200, 201, 202, 204):
                raise RelayError(f'GitHub returned HTTP {response.status}.')
    except urllib.error.HTTPError as exc:
        raise RelayError(f'GitHub returned HTTP {exc.code}.') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RelayError('GitHub could not confirm the successor dispatch.') from None


async def relay(token, stop):
    for attempt in range(RELAY_ATTEMPTS):
        if stop.is_set():
            return False
        try:
            await asyncio.to_thread(dispatch_successor, token)
            print('Successor workflow dispatch accepted by GitHub.', flush=True)
            return True
        except RelayError as exc:
            # Never print a response body, URL exception, request or token.
            print(f'::warning::Workflow handoff attempt {attempt + 1} failed: {exc}', flush=True)
        if attempt + 1 < RELAY_ATTEMPTS and not await pause_until(stop, 5 * (attempt + 1)):
            return False
    print('::error::Workflow handoff failed after bounded retries. The cron fallback remains enabled.', flush=True)
    return False


async def run(settings, environment, stop, *, clock=None, publish=publish_once, handoff=relay, pause=pause_until):
    token = validate_environment(environment)
    clock = clock or asyncio.get_running_loop().time
    started = clock()
    deadline = started + settings.duration
    next_wake = started
    failed = False
    while not stop.is_set() and clock() < deadline:
        if not await pause(stop, max(0, next_wake - clock())):
            break
        if stop.is_set() or clock() >= deadline:
            break
        print('Starting a publishing wake; D1 retains all daily and half-hour limits.', flush=True)
        try:
            if not await publish(environment, stop):
                failed = True
                if not stop.is_set():
                    print('::error::Publishing wake failed. The next wake will inspect the preserved D1 state.', flush=True)
        except asyncio.CancelledError:
            stop.set()
            raise
        except Exception as exc:
            failed = True
            print(f'::error::Publishing wake could not finish ({type(exc).__name__}).', flush=True)
        # Skip missed opportunities after a slow child. Never burst to catch up.
        next_index = max(1, math.floor((clock() - started) / settings.interval) + 1)
        next_wake = min(deadline, started + next_index * settings.interval)
    if stop.is_set():
        print('Runner cancelled; no successor will be requested.', flush=True)
        return 130
    # This is deliberately outside finally: cancellation or a configuration
    # error must not restart the publisher. Even immediate child failures remain
    # paced for the full window before a single successor is requested.
    accepted = await handoff(token, stop)
    if stop.is_set():
        return 130
    if not accepted:
        return 1
    return 1 if failed else 0


async def supervised(settings, environment):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        return await run(settings, environment, stop)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration-seconds', type=int, default=DEFAULT_DURATION)
    parser.add_argument('--interval-seconds', type=int, default=DEFAULT_INTERVAL)
    args = parser.parse_args()
    try:
        settings = Settings(args.duration_seconds, args.interval_seconds)
        result = asyncio.run(supervised(settings, dict(os.environ)))
    except RunnerConfigurationError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        result = 130
    raise SystemExit(result)


if __name__ == '__main__':
    main()
