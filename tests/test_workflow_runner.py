import asyncio
import contextlib
import io
import json
import signal
import unittest
import urllib.error
from unittest.mock import AsyncMock, Mock, patch

from app import workflow_runner as runner


ENVIRONMENT = {
    'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': runner.REPOSITORY,
    'GITHUB_REF': 'refs/heads/main', 'QOFENO_RELEASE': 'a' * 40,
    'GH_TOKEN': 'private-job-token', 'GITHUB_TOKEN': 'also-private',
    'X_AUTH_TOKEN': 'publication-secret',
}


class FakeClock:
    def __init__(self):
        self.now = 0
        self.waits = []

    def __call__(self):
        return self.now

    async def pause(self, stop, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return not stop.is_set()


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))

    async def test_default_window_paces_wakes_and_requests_one_successor(self):
        clock, stop, times = FakeClock(), asyncio.Event(), []
        async def publish(environment, event):
            times.append(clock.now)
            return True
        handoff = AsyncMock(return_value=True)
        result = await runner.run(runner.Settings(), ENVIRONMENT, stop,
                                  clock=clock, publish=publish, handoff=handoff, pause=clock.pause)
        self.assertEqual(times, [0, 600, 1200, 1800, 2400, 3000])
        self.assertEqual(clock.now, 3300)
        handoff.assert_awaited_once_with(ENVIRONMENT['GH_TOKEN'], stop)
        self.assertEqual(result, 0)

    async def test_immediate_failures_are_paced_and_remain_visible_after_handoff(self):
        clock, stop, times = FakeClock(), asyncio.Event(), []
        async def publish(environment, event):
            times.append(clock.now)
            raise RuntimeError('secret must never appear')
        handoff = AsyncMock(return_value=True)
        result = await runner.run(runner.Settings(1800, 600), ENVIRONMENT, stop,
                                  clock=clock, publish=publish, handoff=handoff, pause=clock.pause)
        self.assertEqual(times, [0, 600, 1200])
        self.assertEqual(clock.now, 1800)
        self.assertEqual(result, 1)
        handoff.assert_awaited_once()
        self.assertNotIn('secret must never appear', self.output.getvalue())

    async def test_slow_cycle_skips_missed_ticks_without_bursting(self):
        clock, stop, times = FakeClock(), asyncio.Event(), []
        async def publish(environment, event):
            times.append(clock.now)
            clock.now += 650
            return True
        await runner.run(runner.Settings(1800, 600), ENVIRONMENT, stop,
                         clock=clock, publish=publish, handoff=AsyncMock(return_value=True), pause=clock.pause)
        self.assertEqual(times, [0, 1200])
        self.assertEqual(clock.now, 1850)

    async def test_brief_bootstrap_finishes_started_cycle_before_handoff(self):
        clock, stop, times = FakeClock(), asyncio.Event(), []
        async def publish(environment, event):
            times.append(clock.now)
            clock.now += 120
            return True
        handoff = AsyncMock(return_value=True)
        result = await runner.run(runner.Settings(60, 60), ENVIRONMENT, stop,
                                  clock=clock, publish=publish, handoff=handoff, pause=clock.pause)
        self.assertEqual(times, [0])
        self.assertEqual(clock.now, 120)
        handoff.assert_awaited_once()
        self.assertEqual(result, 0)

    async def test_cancellation_during_wait_does_not_restart_workflow(self):
        clock, stop = FakeClock(), asyncio.Event()
        async def pause(event, seconds):
            if seconds:
                event.set()
                return False
            return True
        publish, handoff = AsyncMock(return_value=True), AsyncMock(return_value=True)
        result = await runner.run(runner.Settings(), ENVIRONMENT, stop,
                                  clock=clock, publish=publish, handoff=handoff, pause=pause)
        self.assertEqual(result, 130)
        publish.assert_awaited_once()
        handoff.assert_not_awaited()

    async def test_cancellation_from_child_does_not_restart_workflow(self):
        clock, stop = FakeClock(), asyncio.Event()
        async def publish(environment, event):
            event.set()
            return False
        handoff = AsyncMock(return_value=True)
        result = await runner.run(runner.Settings(), ENVIRONMENT, stop,
                                  clock=clock, publish=publish, handoff=handoff, pause=clock.pause)
        self.assertEqual(result, 130)
        handoff.assert_not_awaited()

    async def test_unconfirmed_handoff_fails_even_when_publication_succeeds(self):
        clock = FakeClock()
        result = await runner.run(runner.Settings(60, 60), ENVIRONMENT, asyncio.Event(),
                                  clock=clock, publish=AsyncMock(return_value=True),
                                  handoff=AsyncMock(return_value=False), pause=clock.pause)
        self.assertEqual(result, 1)

    async def test_cancelled_task_cleans_up_without_handoff(self):
        stop, handoff = asyncio.Event(), AsyncMock()
        result = runner.run(runner.Settings(), ENVIRONMENT, stop, clock=FakeClock(),
                            publish=AsyncMock(side_effect=asyncio.CancelledError),
                            handoff=handoff, pause=AsyncMock(return_value=True))
        with self.assertRaises(asyncio.CancelledError):
            await result
        self.assertTrue(stop.is_set())
        handoff.assert_not_awaited()

    async def test_signal_during_accepted_handoff_still_reports_cancellation(self):
        clock, stop = FakeClock(), asyncio.Event()
        async def handoff(token, event):
            event.set()
            return True
        result = await runner.run(runner.Settings(60, 60), ENVIRONMENT, stop,
                                  clock=clock, publish=AsyncMock(return_value=True),
                                  handoff=handoff, pause=clock.pause)
        self.assertEqual(result, 130)

    async def test_child_receives_no_github_write_token(self):
        child = Mock(returncode=0)
        child.wait = AsyncMock(return_value=0)
        with patch.object(asyncio, 'create_subprocess_exec', new=AsyncMock(return_value=child)) as launch:
            self.assertTrue(await runner.publish_once(ENVIRONMENT, asyncio.Event()))
        options = launch.call_args.kwargs
        self.assertNotIn('GH_TOKEN', options['env'])
        self.assertNotIn('GITHUB_TOKEN', options['env'])
        self.assertEqual(options['env']['X_AUTH_TOKEN'], 'publication-secret')
        self.assertTrue(options['start_new_session'])

    async def test_child_timeout_terminates_process_group_before_return(self):
        child_done = asyncio.Event()
        child = Mock(returncode=None, pid=1234)
        child.wait = AsyncMock(side_effect=child_done.wait)
        def terminate(process, sig):
            self.assertIs(process, child)
            if sig == signal.SIGTERM:
                child.returncode = -15
                child_done.set()
        with patch.object(asyncio, 'create_subprocess_exec', new=AsyncMock(return_value=child)), \
                patch.object(runner, 'MAX_PUBLISH_SECONDS', 0.001), \
                patch.object(runner, '_signal_process_group', side_effect=terminate) as terminate_group:
            self.assertFalse(await runner.publish_once(ENVIRONMENT, asyncio.Event()))
        self.assertEqual([call.args[1] for call in terminate_group.call_args_list],
                         [signal.SIGTERM, signal.SIGKILL])

    async def test_already_cancelled_runner_does_not_start_a_child(self):
        stop = asyncio.Event()
        stop.set()
        with patch.object(asyncio, 'create_subprocess_exec', new=AsyncMock()) as launch:
            self.assertFalse(await runner.publish_once(ENVIRONMENT, stop))
        launch.assert_not_awaited()

    async def test_dispatch_retry_budget_and_cancelled_backoff(self):
        with patch.object(runner, 'dispatch_successor', side_effect=runner.RelayError('HTTP 503')) as dispatch, \
                patch.object(runner, 'pause_until', new=AsyncMock(return_value=True)) as pause:
            self.assertFalse(await runner.relay('private', asyncio.Event()))
        self.assertEqual(dispatch.call_count, 3)
        self.assertEqual([call.args[1] for call in pause.await_args_list], [5, 10])
        with patch.object(runner, 'dispatch_successor', side_effect=runner.RelayError('HTTP 503')) as dispatch, \
                patch.object(runner, 'pause_until', new=AsyncMock(return_value=False)):
            self.assertFalse(await runner.relay('private', asyncio.Event()))
        self.assertEqual(dispatch.call_count, 1)


class ConfigurationTests(unittest.TestCase):
    def test_invalid_bounds_and_untrusted_environment_fail_before_any_work(self):
        for duration, interval in ((0, 60), (59, 60), (3301, 600), (60, 0), (60, 61), (900, 601)):
            with self.subTest(duration=duration, interval=interval), self.assertRaises(runner.RunnerConfigurationError):
                runner.Settings(duration, interval)
        for key, value in (('GITHUB_ACTIONS', 'false'), ('GITHUB_REPOSITORY', 'other/repo'),
                           ('GITHUB_REF', 'refs/heads/dev'), ('QOFENO_RELEASE', 'short'), ('GH_TOKEN', '')):
            with self.subTest(key=key), self.assertRaises(runner.RunnerConfigurationError):
                runner.validate_environment({**ENVIRONMENT, key: value})

    def test_dispatch_targets_main_without_forwarding_bootstrap_parameters(self):
        response = Mock(status=204)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(runner.urllib.request, 'urlopen', return_value=response) as send:
            runner.dispatch_successor('private')
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.github.com/repos/SohailKhan0525/trend-to-post/actions/workflows/publish.yml/dispatches')
        self.assertEqual(json.loads(request.data), {'ref': 'main'})
        self.assertEqual(send.call_args.kwargs['timeout'], 15)

    def test_dispatch_errors_do_not_expose_response_or_token(self):
        error = urllib.error.HTTPError('private URL', 403, 'secret response', {}, None)
        with patch.object(runner.urllib.request, 'urlopen', side_effect=error), \
                self.assertRaisesRegex(runner.RelayError, '^GitHub returned HTTP 403\\.$'):
            runner.dispatch_successor('private-token')
