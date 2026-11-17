import base64
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs
from urllib.request import Request


script = Path(__file__).resolve().parents[1] / 'scripts' / 'azure-deploy.py'
spec = importlib.util.spec_from_file_location('azure_deploy', script)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class AzureDeploymentTests(unittest.TestCase):
    def test_subject_diagnostic_never_outputs_signin_token(self):
        claim = {'sub': 'repo:owner@7/repo@8:ref:refs/heads/main', 'some_other_claim': 'private-data'}
        encoded = base64.urlsafe_b64encode(json.dumps(claim).encode()).decode().rstrip('=')
        token = 'header.' + encoded + '.signature'
        output = io.StringIO()
        with patch.object(deploy, 'github_identity', return_value=token), redirect_stdout(output):
            deploy.show_subject()
        self.assertEqual(output.getvalue().strip(), f'Azure federated identity subject: {claim["sub"]}')

    def test_existing_credentials_travel_only_to_the_configured_vault(self):
        release = 'a' * 40
        settings = {name: 'dummy-' + name for name in (*deploy.REQUIRED, *deploy.OPTIONAL)}
        config = {'tenant_id': 'tenant', 'client_id': 'client', 'vault_name': 'qofeno', 'release': release}
        requests = []

        def send(request, operation):
            requests.append(request)
            if operation == 'Microsoft Entra token exchange':
                return {'access_token': 'vault-token'}
            return {'value': 'sensitive-response'}

        output = io.StringIO()
        with patch.object(deploy, 'target', return_value=config), patch.dict(os.environ, settings, clear=True), \
             patch.object(deploy, 'github_identity', return_value='signed-assertion'), \
             patch.object(deploy, 'request_json', side_effect=send), redirect_stdout(output):
            deploy.deliver()
        self.assertEqual(len(requests), 2)
        login, vault = requests
        self.assertEqual(login.full_url, 'https://login.microsoftonline.com/tenant/oauth2/v2.0/token')
        login_data = parse_qs(login.data.decode())
        self.assertEqual(login_data['scope'], ['https://vault.azure.net/.default'])
        self.assertEqual(login_data['client_assertion'], ['signed-assertion'])
        self.assertFalse(any(value in login.data.decode() for value in settings.values()))
        self.assertEqual(vault.get_method(), 'PUT')
        self.assertEqual(vault.full_url, 'https://qofeno.vault.azure.net/secrets/qofeno-runtime?api-version=7.4')
        self.assertEqual(vault.get_header('Authorization'), 'Bearer vault-token')
        payload = json.loads(json.loads(vault.data)['value'])
        self.assertEqual(payload, {'revision': release, 'environment': dict(settings, QOFENO_RELEASE=release)})
        self.assertFalse(any(value in output.getvalue() for value in [*settings.values(), 'vault-token', 'sensitive-response']))

    def test_missing_target_skips_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'out'
            with patch.object(deploy, 'TARGET', Path(directory) / 'missing'), \
                 patch.dict(os.environ, {'GITHUB_OUTPUT': str(out)}), redirect_stdout(io.StringIO()):
                deploy.report_target()
            self.assertEqual(out.read_text().strip(), 'configured=false')

    def test_vault_name_cannot_redirect_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'target.json'
            cfg = dict.fromkeys(('client_id', 'tenant_id'), 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
            cfg.update(release='a' * 40, vault_name='example.invalid/elsewhere?')
            path.write_text(json.dumps(cfg))
            with patch.object(deploy, 'TARGET', path), self.assertRaises(ValueError):
                deploy.target()
        no_redirect = deploy.NoRedirect().redirect_request(Request('https://vault.azure.net/'), None, 302, '', {}, 'https://example.invalid')
        self.assertIsNone(no_redirect)
