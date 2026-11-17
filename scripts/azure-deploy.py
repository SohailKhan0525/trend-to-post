"""Deliver the existing publisher settings to its dedicated vault with GitHub OIDC."""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


TARGET = Path('infra/azure-target.json')
REQUIRED = ('X_AUTH_TOKEN', 'X_CT0', 'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_API_TOKEN')
OPTIONAL = ('CLOUDFLARE_D1_API_TOKEN', 'D1_DATABASE_ID', 'QOFENO_AI_MODEL', 'DASH_RATTLE_MODEL')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a credential-bearing request to a redirect destination.
        return None


def request_json(request, operation):
    try:
        with build_opener(NoRedirect).open(request, timeout=30) as result:
            return json.load(result)
    except HTTPError as exc:
        # A Key Vault success body contains the secret; neither bodies nor headers are loggable.
        raise RuntimeError(f'{operation} returned HTTP {exc.code}.') from None
    except (OSError, ValueError, URLError) as exc:
        raise RuntimeError(f'{operation} failed ({type(exc).__name__}).') from None


def github_identity():
    url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    if not url.startswith('https://'):
        raise ValueError('Invalid GitHub identity endpoint.')
    query = urlencode({'audience': 'api://AzureADTokenExchange'})
    request = Request(url + ('&' if '?' in url else '?') + query,
                      headers={'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    return request_json(request, 'GitHub identity request')['value']


def show_subject():
    # Entra validates the signed assertion itself. Only its nonsecret, public subject is displayed.
    body = github_identity().split('.')[1]
    subject = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))['sub']
    if not isinstance(subject, str) or not re.fullmatch(r'[A-Za-z0-9_@./:%-]{1,512}', subject):
        raise ValueError('Unexpected GitHub subject format.')
    print(f'Azure federated identity subject: {subject}')


def target():
    config = json.loads(TARGET.read_text(encoding='utf-8'))
    for name in ('tenant_id', 'client_id'):
        if str(UUID(config[name])) != config[name]:
            raise ValueError(f'{name} must be a lowercase UUID.')
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,22}[a-z0-9]', config['vault_name']):
        raise ValueError('Invalid Azure Key Vault name.')
    if not re.fullmatch(r'[0-9a-f]{40}', config['release']):
        raise ValueError('release must be a full lowercase Git commit SHA.')
    return config


def report_target():
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as out:
        if not TARGET.is_file():
            print('configured=false', file=out)
            print('No Azure target is configured. Existing publishing is unchanged.')
            return
        cfg = target()
        print('configured=true', file=out)
        print(f'release={cfg["release"]}', file=out)
        print('manifest=' + hashlib.sha256(TARGET.read_bytes()).hexdigest(), file=out)


def deliver():
    cfg = target()
    missing = [name for name in REQUIRED if not os.environ.get(name, '').strip()]
    if missing:
        raise ValueError('Missing existing publisher settings: ' + ', '.join(missing))
    env = {name: os.environ[name] for name in (*REQUIRED, *OPTIONAL) if os.environ.get(name)}
    env['QOFENO_RELEASE'] = cfg['release']
    encoded = urlencode({'client_id': cfg['client_id'], 'scope': 'https://vault.azure.net/.default',
                         'grant_type': 'client_credentials', 'client_assertion_type':
                         'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
                         'client_assertion': github_identity()}).encode('ascii')
    login = Request(f'https://login.microsoftonline.com/{cfg["tenant_id"]}/oauth2/v2.0/token', data=encoded)
    token = request_json(login, 'Microsoft Entra token exchange')['access_token']
    secret = json.dumps({'revision': cfg['release'], 'environment': env})
    payload = json.dumps({'value': secret, 'contentType': 'application/json'}).encode('utf-8')
    url = f'https://{cfg["vault_name"]}.vault.azure.net/secrets/qofeno-runtime?api-version=7.4'
    write = Request(url, method='PUT', headers={'Authorization': 'Bearer ' + token,
                    'Content-Type': 'application/json'}, data=payload)
    # Role assignments may take time to propagate just after first provisioning.
    for delay in (0, 10, 20, 30, 40):
        time.sleep(delay)
        try:
            request_json(write, 'Azure Key Vault')
            break
        except RuntimeError as exc:
            if str(exc) != 'Azure Key Vault returned HTTP 403.' or delay == 40:
                raise
    print('Updated the VM runtime settings in its dedicated Azure Key Vault.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('subject', 'target', 'deliver'))
    command = parser.parse_args().command
    try:
        {'subject': show_subject, 'target': report_target, 'deliver': deliver}[command]()
    except (ValueError, KeyError, IndexError, OSError, RuntimeError) as exc:
        # Surface our own scoped HTTP diagnostics, not an opaque response or received value.
        if isinstance(exc, RuntimeError):
            print(str(exc), file=sys.stderr)
        else:
            print(f'Invalid deployment settings or identity response ({type(exc).__name__}).', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
