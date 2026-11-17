"""Report the active worker and latest decisions using SELECT-only D1 queries."""
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone

from app.editorial import schedule_state, upcoming_opportunity
from app.memory import Memory, MemoryUnavailable, database_uuid
from app.reply_schedule import reply_ready, timestamp
from app.server import INBOX_INTERVAL
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


def opportunity_label(slot):
    if re.fullmatch(r'\d{4}-\d\d-\d\d-inbox-[1-9][0-9]{0,19}-[a-f0-9]{16}', str(slot)):
        return 'inbound reply'
    if re.fullmatch(r'\d{4}-\d\d-\d\d:(reply|follow)-v1:\d{1,2}', str(slot)):
        return slot
    return None


def inbox_schedule(state, now):
    eligibility = 'open' if reply_ready(schedule_state(state), now) else 'on hold'
    return f'Inbox schedule: {INBOX_INTERVAL}s between idle checks. Reply eligibility in D1: {eligibility}.'


def report(state, now):
    latest = max((r for r in state['history'] if r.get('verified_at') and re.fullmatch(r'[0-9]{1,20}', str(r.get('tweet_id')))),
                 key=lambda r: timestamp(r['verified_at']), default=None)
    receipt = 'none'
    events = []
    if latest:
        receipt = f'https://x.com/qofenoai/status/{latest["tweet_id"]} verified {timestamp(latest["verified_at"]).isoformat()}'
        events.append((timestamp(latest['verified_at']),latest.get('slot',''), 'verified publication'))
        incoming_at = (latest.get('source') or {}).get('created_at')
        if latest.get('kind') == 'reply' and incoming_at and latest.get('posted_at'):
            elapsed = timestamp(latest['posted_at']) - timestamp(incoming_at)
            if elapsed.total_seconds() >= 0:
                receipt += f' ({int(elapsed.total_seconds())}s from incoming post to published reply)'
    for slot, row in state.get('decision_slots', {}).items():
        if row.get('action') == 'silent':
            events.append((timestamp(row['at']), slot, 'silence'))
    for slot, row in state.get('last_attempts', {}).items():
        events.append((timestamp(row['at']), slot, category(str(row.get('reason', '')))))
    valid_slots = [(at, label, outcome) for at, slot, outcome in events
                   if (label := opportunity_label(slot))]
    decision = 'none recorded'
    if valid_slots:
        at, slot, outcome = max(valid_slots)
        decision = f'{slot} at {at.isoformat()} ({outcome})'
    pending = any(state.get(k) for k in ('outbox','pending','pending_reply','follow_pending','verification_pending'))
    lines = [f'Latest verified X post: {receipt}', f'Latest recorded decision: {decision}',
             f'Pending delivery: {bool(pending)}', inbox_schedule(state, now), upcoming_opportunity(state, now)]
    outbox = state.get('outbox')
    if outbox:
        post_id = str(outbox.get('tweet_id') or '')
        link = f'https://x.com/qofenoai/status/{post_id}' if re.fullmatch(r'[1-9][0-9]{0,19}', post_id) else 'no X ID'
        reserved = outbox.get('reserved_at')
        since = timestamp(reserved).isoformat() if reserved else 'unknown time'
        outcome = 'X reported CouldNotTweet' if outbox.get('send_error') == 'CouldNotTweet' else 'awaiting verification'
        if state.get('pause_reason') == 'Published post does not match the reserved author, text or reply parent':
            outcome = 'readback did not match the reservation'
        lines.append(f'Reserved at {since}; {link}; {outcome}.')
    if state.get('x_pause_until') and timestamp(state['x_pause_until']) > now:
        lines.append(f'X cooldown until {timestamp(state["x_pause_until"]).isoformat()}.')
    return lines


def read_snapshot():
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
    return active, unpack({row['page']: row['body'] for row in pages}) if head else None


def main():
    active, state = read_snapshot()
    if state is not None:
        for line in report(state, datetime.now(timezone.utc)):
            summary(line)
    return 0 if active else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Azure D1 status unavailable ({type(exc).__name__}).')
        raise SystemExit(1) from None
