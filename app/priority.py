"""Read new originals from the explicitly configured priority account.

The publication ledger owns the activation boundary, queue and permanent sent IDs.
Nothing here sends a post or treats an unsolicited original as a direct invitation.
"""
from datetime import timedelta

from . import sources
from .reply_freshness import post_created_at
from .store import summary

HANDLE = 'zaheer0525'
STATE_KEY = 'priority_account'
PAGE_LIMIT = 5


def validate(candidate, now, state=None):
    if not isinstance(candidate, dict) or candidate.get('parent_id') or candidate.get('origin') == 'mention':
        raise sources.SourceError('Priority replies require a public original')
    row = sources.validate_fields(candidate, now, require_reach=False, minimum_letters=1)
    if row['author'].casefold() != HANDLE:
        raise sources.SourceError('Priority source does not match the exact configured handle')
    if state is not None:
        account = state.get(STATE_KEY) or {}
        if account.get('handle') != HANDLE or row['author_id'] != account.get('user_id'):
            raise sources.SourceError('Priority source does not match the bound account')
        activated = sources._utc(account.get('activated_at'), 'Missing priority activation boundary')
        try:
            editions = [post_created_at(ident) for ident in row['source_ids']]
        except (ValueError, OverflowError):
            raise sources.SourceError('Invalid priority edit identity timestamp') from None
        if (sources._utc(row['created_at'], 'Invalid priority post timestamp') <= activated
                or min(editions) <= activated
                or set(row['source_ids']).intersection(account.get('baseline_ids', []))):
            raise sources.SourceError('Priority source predates activation')
        if max(editions) > now:
            raise sources.SourceError('Priority source edit is in the future')
    row.pop('intent', None)
    row.update(origin='priority', parent_id=None, parent_author=None,
               conversation_id=min(row['source_ids'], key=int))
    return row


def snapshot(tweet, now):
    return sources.checked_snapshot(tweet, now, include_parent=False, validator=validate)


async def discover(connection, state, now):
    """Return queued fresh originals; persist the mutated ledger before any send.

    Resolve the exact handle every wake, but never silently rebind a saved user ID.
    Timeline pagination uses Result.next(), which preserves Twifork's overflow buffer.
    """
    previous = state.get(STATE_KEY)
    user = await connection.get_user_by_screen_name(HANDLE)
    user_id = str(getattr(user, 'id', ''))
    if (str(getattr(user, 'screen_name', '')).casefold() != HANDLE
            or not user_id.isascii() or not user_id.isdigit() or int(user_id) <= 0
            or getattr(user, 'protected', False) or getattr(user, 'blocked_by', False)
            or getattr(user, 'blocking', False)):
        raise sources.SourceError('Exact priority account lookup was unavailable or not public')
    if previous is not None and (previous.get('handle') != HANDLE or previous.get('user_id') != user_id):
        raise sources.SourceError('Priority handle identity changed; saved binding was preserved')
    summary(f'Priority @{HANDLE}: exact account resolved; reading native original-post timeline.')
    page = await connection.get_user_tweets(user_id, 'Tweets', count=40)
    baseline_ids, observed_times, current, seen = set(), [], [], set()
    excluded = examined = 0
    # A full day is the existing freshness policy. Reading to the earlier polling
    # boundary keeps missed wakes recoverable; queued originals are not lost to caps.
    cutoff = max(now - timedelta(hours=24), sources._utc(previous['last_checked_at'], 'Invalid priority poll boundary')) if previous else now
    truncated = False
    for index in range(PAGE_LIMIT):
        if not page:
            break
        page_times = []
        for post in page:
            examined += 1
            try:
                ids = sources.tweet_ids(post)
                created = sources._utc(post.created_at_datetime, 'Invalid priority timeline timestamp')
                page_times.append(created)
                observed_times.append(created)
                baseline_ids.update(ids)
            except (sources.SourceError, AttributeError, TypeError, ValueError):
                excluded += 1
                continue
            if set(ids).intersection(seen):
                continue
            seen.update(ids)
            try:
                current.append(snapshot(post, now))
            except sources.SourceError:
                excluded += 1
        # Initial activation deliberately does not read or enqueue a backlog.
        # A pinned old post is not a stopping signal while newer posts fill a page.
        if previous is None or (page_times and max(page_times) <= cutoff):
            break
        if index == PAGE_LIMIT - 1:
            truncated = True
            break
        page = await page.next()
    if previous is None:
        state[STATE_KEY] = {'handle': HANDLE, 'user_id': user_id, 'activated_at': now.isoformat(),
                           'baseline_ids': sorted(baseline_ids),
                           'baseline_latest_at': max(observed_times).isoformat() if observed_times else None,
                           'last_checked_at': now.isoformat(), 'pending': []}
        summary(f'Priority @{HANDLE}: activated at {now.isoformat()}; baselined {len(baseline_ids)} existing identities; new eligible=0 (no historical replies).')
        return []
    pending, pending_ids = [], set()
    used_ids, used_urls = set(state['used_source_ids']), set(state['used_urls'])
    # A newly fetched edit supersedes the queued snapshot. Its older aliases still
    # identify the same post for the activation boundary and permanent dedupe.
    for candidate in [*current, *previous.get('pending', [])]:
        try:
            row = validate(candidate, now, state)
        except sources.SourceError:
            continue
        aliases = set(row['source_ids'])
        if aliases.intersection(used_ids | pending_ids) or set(row['urls']).intersection(used_urls):
            continue
        pending.append(row)
        pending_ids.update(aliases)
    pending.sort(key=lambda row: (row['created_at'], int(row['id'])))
    old_ids = {ident for row in previous.get('pending', []) for ident in row['source_ids']}
    new_count = sum(not set(row['source_ids']).intersection(old_ids) for row in pending)
    # Do not advance the scan boundary if its bounded read was incomplete.
    state[STATE_KEY] = {**previous, 'pending': pending,
                       'last_checked_at': previous['last_checked_at'] if truncated else now.isoformat()}
    summary(f'Priority @{HANDLE}: examined={examined}, excluded={excluded}, new eligible={new_count}, queued={len(pending)}, scan_limited={str(truncated).lower()}.')
    return pending
