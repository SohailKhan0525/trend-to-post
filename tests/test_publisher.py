import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app import publisher

RELEASE = 'a' * 40


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_lease_loss_terminates_the_child_before_returning(self):
        stop, ledger = asyncio.Event(), Mock()
        ledger.heartbeat.return_value = False
        child_finished = asyncio.Event()
        process = Mock()
        process.returncode = None
        process.wait.side_effect = child_finished.wait
        process.terminate.side_effect = child_finished.set
        with patch.object(publisher, 'PULSE', 0.001), \
                patch.object(asyncio, 'create_subprocess_exec', new=AsyncMock(return_value=process)), \
                self.assertRaisesRegex(RuntimeError, 'lost'):
            await asyncio.wait_for(publisher.run_decision(ledger, 'one', RELEASE, stop), 2)
        process.terminate.assert_called_once()
        process.kill.assert_not_called()
