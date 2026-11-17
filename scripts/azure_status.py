"""Report the active worker and latest decisions using SELECT-only D1 queries."""
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone

from app.editorial import upcoming_opportunity
from app.memory import Memory, MemoryUnavailable, database_uuid
from app.reply_schedule import timestamp
from app.server_ledger import FRESH_SECONDS, HEAD, LEASE, PAGES, unpack
from app.store import summary


def category(reason):
    for prefix, result in (
        ('No fresh eligible', 'no eligible conversation'), ('No approved decision', 'wording or decision rejected'),
        ('X rate limit', 'X rate limit'), ('X rejected', 'X rejected publication'),
        ('X returned a posting error', 'awaiting X verification'), ('Source or author', 'duplicate source or author'),
        ('No reply sent', 'AI unavailable or budget'), ('Deferred original', 'AI budget'), ('AI deferred', 'AI cooldown'),
        ('Workers AI timed out', 'AI timeout'), ('Cloudflare timed out', 'AI timeout'),
        ('Opportunity changed', 'window expired'), ('Reply opportunity ended', 'window expired'),
        ('Reservation completed after', 'window expired')):
        if reason.startswith(prefix):
            return result
    return 'rejected'


def report(state, now):
    latest = max((r for r in state['history'] if r.get('verified_at') and re.fullmatch(r'[0-9]{1,20}', str(r.get('tweet_id')))),
                 key=lambda r: timestamp(r['verified_at']), default=None)
    receipt = 'none'
    events = []
    if latest:
        receipt = f'https://x.com/qofenoai/status/{latest["tweet_id"]} verified {timestamp(latest["verified_at"]).isoformat()}'
        events.append((timestamp(latest['verified_at']),latest.get('slot',''), 'verified publication'))
    for slot, row in state.get('decision_slots', {}).items():
        if row.get('action') == 'silent':
            events.append((timestamp(row['at']), slot, 'silence'))
    for slot, row in state.get('last_attempts', {}).items():
        events.append((timestamp(row['at']), slot, category(str(row.get('reason', '')))))
    valid_slots = [event for event in events if re.fullmatch(r'\d{4}-\d\d-\d\d:(reply|follow)-v1:\d{1,2}', str(event[1]))]
    decision = 'none recorded'
    if valid_slots:
        at, slot, outcome = max(valid_slots)
        decision = f'{slot} at {at.isoformat()} ({outcome})'
    pending = any(state.get(k) for k in ('outbox','pending','pending_reply','follow_pending','verification_pending'))
    return [f'Latest verified X post: {receipt}', f'Latest recorded decision: {decision}',
            f'Pending delivery: {bool(pending)}', upcoming_opportunity(state, now)]


def main():
    release = json.loads(Path('infra/azure-target.json').read_text())['release']
    account = os.environ['CLOUDFLARE_ACCOUNT_ID'].strip()
    if not re.fullmatch('[a-f0-9]{40}', release) or not re.fullmatch('[a-fA-F0-9]{32}', account):
        raise ValueError('Invalid release or account configuration.')
    db = Memory(account, os.environ['CLOUDFLARE_D1_API_TOKEN'])
    configured = os.environ.get('D1_DATABASE_ID', '').strip()
    db.database_id = database_uuid(configured) if configured else db._find()
    if not db.database_id:
        raise MemoryUnavailable('The existing D1 database was not found.')
    head, lease, pages = db._batch([
        {'sql': f'SELECT revision FROM {HEAD} WHERE id=1'},
        {'sql': f'SELECT phase, seen, seen > unixepoch()-{FRESH_SECONDS} AS fresh FROM {LEASE} WHERE id=1 AND release=?', 'params': [release]},
        {'sql': f'SELECT page, body FROM {PAGES}'}])
    phase = lease[0]['phase'] if lease and lease[0]['phase'] in ('active','standby','degraded') else 'unknown'
    seen = datetime.fromtimestamp(lease[0]['seen'],timezone.utc).isoformat() if lease else 'none'
    active = bool(head and lease and lease[0]['fresh'] and phase == 'active')
    summary(f'Azure worker {release}: {phase}; fresh active receipt={active}; heartbeat={seen}')
    if head:
        state = unpack({row['page']: row['body'] for row in pages})
        for line in report(state, datetime.now(timezone.utc)):
            summary(line)
    return 0 if active else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Azure D1 status unavailable ({type(exc).__name__}).')
        raise SystemExit(1) from None
