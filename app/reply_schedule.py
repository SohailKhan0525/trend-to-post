"""Schedule standalone posts and check inbound reply capacity against the ledger."""
import random
from datetime import date, datetime, timedelta, timezone
from .ai_budget import active_pause

DAILY_REPLY_CAP = 48
DAILY_FOLLOW_CAP = 2
MIN_REPLY_GAP = timedelta(minutes=30)
MIN_FOLLOW_GAP = timedelta(hours=6)
MIN_ACTIVITY_GAP = timedelta(minutes=2)
RECHECK_GAP = timedelta(minutes=10)
CAPS = {'reply': DAILY_REPLY_CAP, 'follow': DAILY_FOLLOW_CAP}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('A scheduling timestamp must include a timezone')
    return parsed.astimezone(timezone.utc)


def daily_plan(day):
    """Produce reproducible, varied opportunities without depending on process RNG."""
    calendar_day = date.fromisoformat(day)
    start = datetime.combine(calendar_day, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    rng = random.Random('qofeno-replies-v1/' + day)
    offset = rng.randint(120, 360)
    reply_times = [start + timedelta(seconds=1800 * index + offset)
                   for index in range(24 * 2)]
    plan = [{'slot': f'{day}:reply-v1:{index}', 'kind': 'reply',
             'due_at': due.isoformat(),
             'expires_at': (reply_times[index + 1] if index + 1 < len(reply_times) else end).isoformat()}
            for index, due in enumerate(reply_times)]
    # Midpoints keep follow posts clear of the regular reply opportunities.
    # These ranges also preserve the 6-hour follow gap across UTC boundaries.
    follow_gaps = (rng.randint(6, 16), rng.randint(30, 40))
    for index, gap in enumerate(follow_gaps):
        due = reply_times[gap] + (reply_times[gap + 1] - reply_times[gap]) / 2
        expires = start + timedelta(hours=12 * (index + 1))
        plan.append({'slot': f'{day}:follow-v1:{index}', 'kind': 'follow',
                     'due_at': due.isoformat(), 'expires_at': expires.isoformat()})
    return sorted(plan, key=lambda item: item['due_at'])


def _retry_ready(state, slot_id, ready):
    attempts = [state.get('last_attempts', {}).get(slot_id)]
    legacy = state.get('last_attempt') or {}
    if legacy.get('slot') == slot_id:
        attempts.append(legacy)
    for attempt in attempts:
        if attempt and attempt.get('at'):
            ready = max(ready, timestamp(attempt['at']) + RECHECK_GAP)
    return ready


def _publication_start(state, now):
    if now.tzinfo is None:
        raise ValueError('The scheduling clock must include a timezone')
    if any(state.get(key) for key in ('pending', 'pending_reply', 'follow_pending', 'outbox',
                                      'verification_pending', 'pause_reason')):
        return None
    earliest = now
    if state.get('x_pause_until'):
        earliest = max(earliest, timestamp(state['x_pause_until']))
    pause = active_pause(state, now)
    if pause:
        earliest = max(earliest, pause.until)
    return earliest


def _publications(state):
    for row in state.get('history', []):
        when = row.get('posted_at') or row.get('completed_at')
        if when:
            yield row, timestamp(when)


def reply_ready(state, now):
    """Check actual UTC-day volume and provider holds when an invitation arrives."""
    if _publication_start(state, now) != now:
        return False
    day = now.astimezone(timezone.utc).date()
    count = sum(row.get('kind') == 'reply' and at.date() == day
                for row, at in _publications(state))
    return count < DAILY_REPLY_CAP


def next_original(state, now, window_end=None):
    """Return the next standalone decision, independent of new replies."""
    earliest = _publication_start(state, now)
    if earliest is None:
        return None
    now = now.astimezone(timezone.utc)
    originals = []
    used = set(state.get('decision_slots', {}))
    for row, at in _publications(state):
        used.add(row.get('slot'))
        if row.get('kind') in ('follow', 'original'):
            originals.append(at)
    if sum(at.date() == now.date() for at in originals) >= DAILY_FOLLOW_CAP:
        return None
    if originals:
        earliest = max(earliest, max(originals) + MIN_FOLLOW_GAP)
    return _decision_window(state, now, window_end, used, {'original': earliest}, original_only=True)


def next_decision(state, now, window_end=None):
    """Return an opportunity for the legacy mixed discovery planner."""
    earliest = _publication_start(state, now)
    if earliest is None:
        return None
    now = now.astimezone(timezone.utc)
    day = now.date()
    counts = {'reply': 0, 'original': 0}
    last_original = None
    used = set(state.get('decision_slots', {}))
    for row, at in _publications(state):
        used.add(row.get('slot'))
        earliest = max(earliest, at + MIN_REPLY_GAP)
        kind = row.get('kind')
        if kind in ('follow', 'original'):
            kind = 'original'
            last_original = max(last_original, at) if last_original else at
        if kind in counts and at.date() == day:
            counts[kind] += 1
    for key in ('last_posted_at', 'last_completed_at'):
        if state.get(key):
            earliest = max(earliest, timestamp(state[key]) + MIN_REPLY_GAP)

    # An original may become eligible partway through a window. A ready reply
    # need not wait for it. If replies are capped, the dispatcher can wait for it.
    available_at = {}
    if counts['reply'] < DAILY_REPLY_CAP:
        available_at['reply'] = earliest
    if counts['original'] < DAILY_FOLLOW_CAP:
        available_at['original'] = max(earliest, last_original + MIN_FOLLOW_GAP) if last_original else earliest
    if not available_at:
        return None
    return _decision_window(state, now, window_end, used, available_at)


def _decision_window(state, now, window_end, used, available_at, *, original_only=False):
    if window_end is not None and window_end.tzinfo is None:
        raise ValueError('The scheduling window must include a timezone')
    for slot in daily_plan(now.date().isoformat()):
        if slot['kind'] != 'reply' or slot['slot'] in used:
            continue
        ready = max(timestamp(slot['due_at']), min(available_at.values()))
        ready = _retry_ready(state, slot['slot'], ready)
        if ready >= timestamp(slot['expires_at']):
            continue
        if (window_end is None and ready > now) or (window_end is not None and ready >= window_end):
            continue
        actions = [action for action, at in available_at.items() if at <= ready]
        opportunity = {**slot, 'kind': 'decision', 'allowed_actions': actions}
        if original_only:
            opportunity['original_only'] = True
        return opportunity, ready
    return None


def next_slot(state, now, window_end=None):
    """Return (opportunity, earliest start time), never a burst of missed slots.

    Without a window, only an immediately due opportunity is returned. A dispatcher
    may provide its half-hour boundary and wait for a future opportunity inside it.
    Actual completion timestamps determine spacing and caps, including across dates.
    An unresolved publishing outcome blocks all further sends until reconciled.
    """
    if any(state.get(key) for key in ('pending', 'verification_pending', 'pause_reason')):
        return None
    if now.tzinfo is None:
        raise ValueError('The scheduling clock must include a timezone')
    now = now.astimezone(timezone.utc)
    if window_end is not None:
        if window_end.tzinfo is None:
            raise ValueError('The scheduling window must include a timezone')
        window_end = window_end.astimezone(timezone.utc)
        if window_end <= now:
            return None
    day = now.date().isoformat()
    sent = [(row, timestamp(row.get('completed_at') or row['posted_at']))
            for row in state.get('history', [])
            if row.get('completed_at') or row.get('posted_at')]
    used = {row.get('slot') for row, _ in sent}
    counts = {kind: sum(row.get('kind') == kind and at.date() == now.date()
                        for row, at in sent) for kind in CAPS}
    last_any = max((at for _, at in sent), default=None)
    # Carry-over from a retired publisher may have no rows in the new history.
    for key in ('last_completed_at', 'last_posted_at'):
        if not state.get(key):
            continue
        carry_over = timestamp(state[key])
        last_any = max(last_any, carry_over) if last_any else carry_over
    last_kind = {kind: max((at for row, at in sent if row.get('kind') == kind), default=None)
                 for kind in CAPS}
    available = []
    for slot in daily_plan(day):
        kind = slot['kind']
        if slot['slot'] in used or counts[kind] >= CAPS[kind]:
            continue
        due, expires = timestamp(slot['due_at']), timestamp(slot['expires_at'])
        ready = max(now, due)
        if last_any:
            ready = max(ready, last_any + MIN_ACTIVITY_GAP)
        if last_kind[kind]:
            gap = MIN_REPLY_GAP if kind == 'reply' else MIN_FOLLOW_GAP
            ready = max(ready, last_kind[kind] + gap)
        ready = _retry_ready(state, slot['slot'], ready)
        if ready >= expires:
            continue
        if window_end is None and ready > now:
            continue
        if window_end is not None and ready >= window_end:
            continue
        available.append((ready, due, slot))
    if not available:
        return None
    ready, _, slot = min(available, key=lambda item: (item[0], item[2]['kind'] != 'reply', item[1], item[2]['slot']))
    return slot, ready
