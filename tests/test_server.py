import asyncio
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app import server


NOW = datetime(2026, 11, 17, 10, 10, tzinfo=timezone.utc)
RELEASE = 'a' * 40
STATE = {'history': [], 'last_posted_at': (NOW - timedelta(minutes=5)).isoformat()}


class ServerCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_requires_actual_x_d1_and_model_without_publishing(self):
        ledger = Mock()
        ledger.active.return_value = True
        ledger.load.return_value = STATE
        client = Mock()
        client.get_notifications = AsyncMock(return_value=[])
        client.http.aclose = AsyncMock()
        client._curl_session.close = AsyncMock()
        with patch.object(server.server_ledger, 'connect', return_value=ledger), \
                patch.object(server.store, 'client', return_value=client), \
                patch.object(server.cloudflare, 'complete', return_value={'status': 'ok'}) as ai:
            await server.check()
        ledger.load.assert_called_once()
        client.get_notifications.assert_awaited_once_with('Mentions', count=1)
        client.create_tweet.assert_not_called()
        client.http.aclose.assert_awaited_once()
        client._curl_session.close.assert_awaited_once()
        self.assertEqual(ai.call_args.kwargs['max_tokens'], 32)

    async def test_cooldown_prevents_x_and_ai_calls(self):
        ledger = Mock()
        ledger.load.return_value = {'x_pause_until': (NOW + timedelta(hours=2)).isoformat()}
        with patch.object(server.server_ledger, 'connect', return_value=ledger), \
                patch.object(server.store, 'utcnow', return_value=NOW), \
                patch.object(server.store, 'client') as client, self.assertRaisesRegex(RuntimeError, 'cooldown'):
            await server.check()
        client.assert_not_called()

    async def test_release_is_required_before_preflight(self):
        with patch.dict(os.environ, {'QOFENO_RELEASE': 'latest'}), \
                patch.object(server, 'check', new_callable=AsyncMock) as check:
            with self.assertRaisesRegex(ValueError, 'commit SHA'):
                await server.serve()
            check.assert_not_awaited()


class SchedulingTests(unittest.TestCase):
    def test_only_call_x_for_an_eligible_decision_or_outbox_reconciliation(self):
        self.assertGreater(server.delay_until_work(STATE, NOW), 0)
        self.assertEqual(server.delay_until_work({'history': []}, NOW), 0)
        self.assertEqual(server.delay_until_work({**STATE, 'outbox': {'pending': True}}, NOW), 0)
        self.assertIsNone(server.delay_until_work({**STATE, 'pause_reason': 'manual review'}, NOW))
        self.assertEqual(server.delay_until_work({**STATE, 'outbox': {'pending': True},
                         'x_pause_until': (NOW + timedelta(minutes=8)).isoformat()}, NOW), 480)


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_standby_waits_for_handover_then_reports_active_on_a_healthy_idle_tick(self):
        stop, ledger = asyncio.Event(), Mock()
        ledger.active.side_effect = [False, True]
        ledger.load.return_value = STATE
        def beat(instance, release, phase):
            if phase == 'active':
                stop.set()
            return True
        ledger.heartbeat.side_effect = beat
        with patch.object(server, 'PULSE', 0.001), patch.object(server.store, 'utcnow', return_value=NOW), \
                patch.object(server, 'run_decision', new_callable=AsyncMock) as run:
            await asyncio.wait_for(server.supervise(ledger, 'one', RELEASE, stop), 2)
        self.assertEqual([call.args[2] for call in ledger.heartbeat.call_args_list],
                         ['standby', 'standby', 'active'])
        run.assert_not_awaited()

    async def test_another_instance_prevents_publication(self):
        stop, ledger = asyncio.Event(), Mock()
        def already_running(*_):
            stop.set()
            return False
        ledger.heartbeat.side_effect = already_running
        with patch.object(server, 'run_decision', new_callable=AsyncMock) as run:
            await server.supervise(ledger, 'other', RELEASE, stop)
        ledger.load.assert_not_called()
        run.assert_not_awaited()

    async def test_failure_or_unresolved_delivery_backs_off_and_reports_degraded(self):
        for success in (False, True):
            with self.subTest(success=success):
                stop, ledger = asyncio.Event(), Mock()
                state = {'history': [], 'outbox': {'pending': True}}
                ledger.active.return_value = True
                ledger.load.return_value = state
                def beat(*_):
                    if ledger.heartbeat.call_count >= 4:
                        stop.set()
                    return True
                ledger.heartbeat.side_effect = beat
                with patch.object(server, 'PULSE', 0.001), \
                        patch.object(server, 'run_decision', new=AsyncMock(return_value=success)) as run:
                    await asyncio.wait_for(server.supervise(ledger, 'one', RELEASE, stop), 2)
                run.assert_awaited_once()
                self.assertEqual(ledger.heartbeat.call_args.args[2], 'degraded')

    async def test_lease_loss_terminates_the_child_before_returning(self):
        stop, ledger = asyncio.Event(), Mock()
        ledger.heartbeat.return_value = False
        child_finished = asyncio.Event()
        process = Mock()
        process.returncode = None
        process.wait.side_effect = child_finished.wait
        process.terminate.side_effect = child_finished.set
        with patch.object(server, 'PULSE', 0.001), \
                patch.object(asyncio, 'create_subprocess_exec', new=AsyncMock(return_value=process)), \
                self.assertRaisesRegex(RuntimeError, 'lost'):
            await asyncio.wait_for(server.run_decision(ledger, 'one', RELEASE, 'active', stop), 2)
        process.terminate.assert_called_once()
        process.kill.assert_not_called()
