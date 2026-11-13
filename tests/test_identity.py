from contextlib import redirect_stdout
import io
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import httpx
from twikit.errors import Forbidden, TooManyRequests, Unauthorized

from app import identity
from app.store import SessionUnavailable


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    def connection(self, markers=(), initial=(), *, act_as=None, profile_id='12345', handle='QofenoAI'):
        jar = httpx.Cookies()
        jar.set('auth_token', 'fixture-secret-auth', domain='.x.com')
        jar.set('ct0', 'fixture-secret-csrf', domain='.x.com')
        for domain, value in initial:
            jar.set('twid', value, domain=domain)
        profile = SimpleNamespace(id=profile_id, screen_name=handle)

        async def lookup(account):
            self.assertEqual(account, identity.ACCOUNT)
            for domain, value in markers:
                jar.set('twid', value, domain=domain)
            return profile

        return SimpleNamespace(
            http=SimpleNamespace(cookies=jar), _act_as=act_as,
            get_user_by_screen_name=AsyncMock(side_effect=lookup),
            user=AsyncMock(return_value=profile),
            create_tweet=AsyncMock(), set_cookies=AsyncMock(),
        )

    async def test_fresh_consistent_server_markers_match_public_profile_without_settings(self):
        connection = self.connection(markers=[('.x.com', 'u%3D12345'), ('api.x.com', '"u=12345"')])
        output = io.StringIO()
        with redirect_stdout(output):
            result = await identity.authenticated_user(connection)
        self.assertEqual(result.id, '12345')
        connection.get_user_by_screen_name.assert_awaited_once_with(identity.ACCOUNT)
        connection.user.assert_not_awaited()
        connection.create_tweet.assert_not_awaited()
        connection.set_cookies.assert_not_called()
        self.assertEqual(output.getvalue(), 'Matched server-issued account identity.\n')
        self.assertEqual(connection.http.cookies.get('auth_token'), 'fixture-secret-auth')
        self.assertEqual(connection.http.cookies.get('ct0'), 'fixture-secret-csrf')

    async def test_no_marker_uses_existing_settings_identity_once(self):
        connection = self.connection()
        output = io.StringIO()
        with redirect_stdout(output):
            result = await identity.authenticated_user(connection)
        self.assertEqual(result.id, '12345')
        connection.user.assert_awaited_once()
        self.assertEqual(output.getvalue(), 'No identity marker; using settings.\n')

    async def test_untrusted_cookie_domain_cannot_establish_identity(self):
        for domain in ('abs.twimg.com', 'evilx.com', 'x.com.evil.test', '', '.api.x.com'):
            connection = self.connection(markers=[(domain, 'u=12345')])
            with self.subTest(domain=domain), redirect_stdout(io.StringIO()):
                await identity.authenticated_user(connection)
            connection.user.assert_awaited_once()

    async def test_preseeded_marker_or_delegated_account_fails_before_network(self):
        for options in ({'initial': [('.x.com', 'u=12345')]},
                        {'initial': [('untrusted.example', 'u=12345')]},
                        {'act_as': '12345'}, {'act_as': ''}):
            connection = self.connection(**options)
            with self.subTest(options=options), self.assertRaises(SessionUnavailable):
                await identity.authenticated_user(connection)
            connection.get_user_by_screen_name.assert_not_awaited()
            connection.user.assert_not_awaited()

    async def test_conflicting_malformed_and_wrong_identity_markers_fail_without_settings(self):
        cases = [
            [('.x.com', 'u=12345'), ('api.x.com', 'u=999')],
            [('.x.com', 'u=999')], [('.x.com', 'u=0')], [('.x.com', 'u=-12345')],
            [('.x.com', 'u=１２３４５')], [('.x.com', ' u=12345')],
            [('.x.com', 'u=12345&other=1')], [('.x.com', '"u=12345')],
            [('.x.com', 'u%253D12345')], [('.x.com', 'u=12345\n')],
        ]
        for markers in cases:
            connection = self.connection(markers=markers)
            with self.subTest(markers=markers), self.assertRaises(SessionUnavailable):
                await identity.authenticated_user(connection)
            connection.user.assert_not_awaited()
            connection.create_tweet.assert_not_awaited()

    async def test_wrong_public_handle_or_invalid_profile_id_fails_closed(self):
        for options in ({'handle': 'someone_else'}, {'profile_id': '0'}, {'profile_id': None}):
            connection = self.connection(markers=[('.x.com', 'u=12345')], **options)
            with self.subTest(options=options), self.assertRaises(SessionUnavailable):
                await identity.authenticated_user(connection)
            connection.user.assert_not_awaited()

    async def test_settings_identity_must_match_both_profile_id_and_expected_handle(self):
        for result in (SimpleNamespace(id='999', screen_name=identity.ACCOUNT),
                       SimpleNamespace(id='12345', screen_name='someone_else')):
            connection = self.connection()
            connection.user.return_value = result
            with self.subTest(result=result), redirect_stdout(io.StringIO()), self.assertRaises(SessionUnavailable):
                await identity.authenticated_user(connection)

    async def test_profile_access_errors_propagate_without_settings_or_cookie_changes(self):
        for error in (Forbidden('private details'), Unauthorized('private details'),
                      TooManyRequests('private details'), TimeoutError('private details')):
            connection = self.connection()
            connection.get_user_by_screen_name.side_effect = error
            output = io.StringIO()
            with self.subTest(error=type(error).__name__), redirect_stdout(output), self.assertRaises(type(error)):
                await identity.authenticated_user(connection)
            connection.get_user_by_screen_name.assert_awaited_once()
            connection.user.assert_not_awaited()
            connection.set_cookies.assert_not_called()
            self.assertEqual(output.getvalue(), '')

    async def test_settings_errors_propagate_without_retry_or_secret_diagnostics(self):
        for error in (Forbidden('fixture-secret-auth'), Unauthorized('fixture-secret-csrf'),
                      TooManyRequests('private details'), TimeoutError('private details')):
            connection = self.connection()
            connection.user.side_effect = error
            output = io.StringIO()
            with self.subTest(error=type(error).__name__), redirect_stdout(output), self.assertRaises(type(error)):
                await identity.authenticated_user(connection)
            connection.user.assert_awaited_once()
            connection.create_tweet.assert_not_awaited()
            connection.set_cookies.assert_not_called()
            self.assertEqual(output.getvalue(), 'No identity marker; using settings.\n')

    async def test_missing_cookie_jar_fails_before_public_lookup(self):
        connection = self.connection()
        connection.http = None
        with self.assertRaises(SessionUnavailable):
            await identity.authenticated_user(connection)
        connection.get_user_by_screen_name.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
