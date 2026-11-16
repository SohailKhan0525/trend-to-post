from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from app import editorial


class ReconciliationStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_block_fails_the_run_while_a_fresh_reservation_waits(self):
        now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        pending = {'slot': 'reserved-slot', 'reserved_at': now.isoformat()}
        with patch.object(editorial, 'utcnow', return_value=now), \
             patch.object(editorial, 'load', return_value={'outbox': pending}), \
             patch.object(editorial, 'client'), \
             patch.object(editorial, 'verify_delivery', new_callable=AsyncMock, return_value=False), \
             patch.object(editorial, 'publish_reply', new_callable=AsyncMock) as publish:
            await editorial.run('push')
            pending['reserved_at'] = (now - timedelta(hours=1)).isoformat()
            with self.assertRaisesRegex(editorial.DeliveryUnresolved, 'reserved-slot'):
                await editorial.run('push')
            publish.assert_not_awaited()

    async def test_x_cooldown_is_observed_before_reconciliation_reads(self):
        now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        state = {'outbox': {'send_error': 'CouldNotTweet'},
                 'x_pause_until': (now + timedelta(minutes=20)).isoformat()}
        with patch.object(editorial, 'utcnow', return_value=now), \
             patch.object(editorial, 'load', return_value=state), \
             patch.object(editorial, 'client') as client, patch.object(editorial, 'summary'):
            await editorial.run('push')
            client.assert_not_called()
