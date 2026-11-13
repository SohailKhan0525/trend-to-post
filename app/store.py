"""Persist publication reservations and history without exposing account sessions."""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from twikit import Client

STATE = Path('state/qofeno.json')
ACCOUNT = 'qofenoai'
IDENTITIES = ('used_source_ids', 'used_urls', 'used_events', 'used_texts')


class SessionUnavailable(Exception):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def validate(state):
    if not isinstance(state, dict) or type(state.get('version')) is not int or state['version'] != 1:
        raise ValueError('Invalid publication ledger')
    for key in ('history', *IDENTITIES):
        if not isinstance(state.get(key), list):
            raise ValueError('Invalid publication ledger')
    if any(not isinstance(row, dict) for row in state['history']):
        raise ValueError('Invalid publication history')
    for key in IDENTITIES:
        if any(not isinstance(value, str) or not value for value in state[key]):
            raise ValueError('Invalid permanent publication identities')
    if 'editorial_history' in state:
        if (not isinstance(state['editorial_history'], list)
                or any(not isinstance(row, dict) for row in state['editorial_history'])):
            raise ValueError('Invalid editorial ledger')
    if 'last_attempts' in state and not isinstance(state['last_attempts'], dict):
        raise ValueError('Invalid attempt ledger')
    for key in ('pending', 'pending_reply', 'follow_pending', 'verification_pending'):
        if key in state and state[key] is not None and not isinstance(state[key], dict):
            raise ValueError('Invalid pending publication state')
    if state.get('outbox') is not None:
        validate_outbox(state['outbox'])
    return state


def validate_outbox(pending):
    if not isinstance(pending, dict) or pending.get('kind') not in ('reply', 'follow'):
        raise ValueError('Invalid publication outbox')
    if any(not isinstance(pending.get(key), str) or not pending[key].strip()
           for key in ('slot', 'text', 'event_key', 'reserved_at')):
        raise ValueError('Invalid publication outbox')
    for key in ('author_id', 'tweet_id'):
        if key == 'tweet_id' and key not in pending:
            continue
        value = pending.get(key)
        if not isinstance(value, str) or not value.isascii() or not value.isdigit() or int(value) <= 0:
            raise ValueError('Invalid publication outbox identity')
    try:
        reserved = datetime.fromisoformat(pending['reserved_at'].replace('Z', '+00:00'))
        if reserved.tzinfo is None or reserved.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise ValueError('Invalid publication reservation timestamp') from None
    source = pending.get('source')
    if pending['kind'] == 'reply':
        value = source.get('id') if isinstance(source, dict) else None
        if not isinstance(value, str) or not value.isascii() or not value.isdigit() or int(value) <= 0:
            raise ValueError('Invalid reserved reply parent')
    elif source is not None:
        raise ValueError('Own-post reservation must not contain a reply source')


def read_state():
    # Missing, malformed or incompatible state must stop the workflow, not reset it.
    return validate(json.loads(STATE.read_text(encoding='utf-8')))


def load():
    state = read_state()
    state.setdefault('editorial_history', [])
    state.setdefault('last_attempts', {})
    return state


def preserve_history(previous, state):
    for key in ('history', 'editorial_history'):
        old = previous.get(key, [])
        if state.get(key, [])[:len(old)] != old:
            raise ValueError('Refusing to replace or truncate publication history')
    for key in IDENTITIES:
        if not set(previous[key]) <= set(state[key]):
            raise ValueError('Refusing to remove permanent publication identities')


def save_and_push(state):
    validate(state)
    preserve_history(read_state(), state)
    # Serialize before touching disk: invalid values cannot damage the last ledger.
    serialized = json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    temp = STATE.with_suffix('.tmp')
    try:
        with temp.open('w', encoding='utf-8') as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(STATE)
    finally:
        temp.unlink(missing_ok=True)
    # A failed remote commit/push propagates; callers must not send afterward.
    subprocess.run(['bash', 'scripts/commit-state.sh'], check=True)


def summary(message):
    print(message, flush=True)
    target = os.environ.get('GITHUB_STEP_SUMMARY')
    if target:
        with open(target, 'a') as stream:
            stream.write(message + '\n\n')


def client():
    auth = os.environ.get('X_AUTH_TOKEN', '').strip()
    csrf = os.environ.get('X_CT0', '').strip()
    if not auth or not csrf:
        raise SessionUnavailable()
    connection = Client('en-US', impersonate='chrome124')
    connection.set_cookies({'auth_token': auth, 'ct0': csrf})
    return connection
