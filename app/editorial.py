"""Autonomous replies for Qofeno.

Every send is durably reserved first. Unknown outcomes block publication rather
than retrying. Native X permission and rate-limit failures are never bypassed.
"""
import asyncio
import html
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from httpx import NetworkError, TimeoutException
from twikit.errors import (CouldNotTweet, Forbidden, NotFound, RequestTimeout, ServerError,
                           TooManyRequests, TweetNotAvailable, UserNotFound, UserUnavailable)
from . import mentions, personality, priority, reply_writer, sources
from .memory import LedgerMemory, Memory, MemoryAccessDenied
from .metrics import post_id
from .reasoned_replies import DAILY_REASONED_GOAL, reasoned_reply_count
from .reply_freshness import INBOX_START_AT, MAX_REPLY_AGE, fresh_invitation
from .ai_budget import AIPaused, AIRequestTooLarge, active_pause, budget_scope
from .cloudflare import AIRequestTimeout, DraftError
from .reply_schedule import (HALF_HOUR, half_hour_start, next_decision, next_original, next_slot,
                             reply_ready, scheduled_remaining, timestamp)
from .store import ACCOUNT, SessionUnavailable, client, load, save_and_push, summary, utcnow

STAGE = {'name': 'startup', 'source_id': None}
THREAD_REPLY_LIMIT = 2
INBOX_BATCH_SIZE = 4
INBOX_RETRY_DELAY = timedelta(seconds=30)
MAX_INBOX_ATTEMPTS = 3
NO_DISCOVERY_CANDIDATES = 'no_candidates'


class DeliveryUnresolved(RuntimeError):
    """A previous send still needs reconciliation after multiple scheduled wakes."""


def stage(name, source_id=None):
    STAGE.update(name=name, source_id=source_id)
    print(f'Stage: {name}' + (f' source={source_id}' if source_id else ''), flush=True)


def schedule_state(state):
    published = {row.get('tweet_id'): row['posted_at'] for row in state['history']
                 if row.get('tweet_id') and row.get('verified_at') and row.get('posted_at')}
    history = [{**row, 'completed_at': published.get(row.get('tweet_id'), row['completed_at'])}
               for row in state.get('editorial_history', [])]
    known = {row.get('tweet_id') for row in history}
    history.extend(row for row in state['history'] if row.get('tweet_id') in published
                   and row['tweet_id'] not in known)
    return {**state, 'history': history,
            'pending': state.get('pending') or state.get('pending_reply') or state.get('follow_pending') or state.get('outbox')}


def reject(state, slot, reason):
    now = utcnow()
    state['last_attempts'] = {key: val for key, val in state.get('last_attempts', {}).items()
                              if val.get('at') and timestamp(val['at']) > now - timedelta(days=1)}
    attempt = {'at': now.isoformat(), 'reason': reason}
    if slot.get('inbox_id'):
        previous = state['last_attempts'].get(slot['slot'])
        attempt['count'] = previous.get('count', 1) + 1 if previous else 1
    state['last_attempts'][slot['slot']] = attempt
    save_and_push(state)
    summary(reason)


def ai_deferral_detail(exc):
    if isinstance(exc, AIRequestTooLarge):
        return (f'AIRequestTooLarge: request needs {exc.needed} estimated neurons, '
                f'{exc.remaining} available this UTC hour')
    return type(exc).__name__


def reasoned_goal_open(state, now):
    return reasoned_reply_count(state, now) < DAILY_REASONED_GOAL


def reasoned_progress(state, now):
    summary(f'Reasoned answers verified for {now.astimezone(timezone.utc).date()}: '
            f'{reasoned_reply_count(state, now)}/{DAILY_REASONED_GOAL}; included in the normal reply limits.')


def thread_evidence(source, history, observed=None):
    """Join only native conversation, reply-parent and verified edit identities."""
    links = {}
    native_roots, originals, stored_roots = set(), set(), set()
    own_replies, recipient_replies, parents, children = set(), set(), set(), set()
    recipient = post_id(source.get('author_id'))

    def join(ident, others):
        if not ident:
            return
        links.setdefault(ident, set())
        for other in others:
            if other and other != ident:
                links[ident].add(other)
                links.setdefault(other, set()).add(ident)

    def add_source(row):
        ident = post_id(row.get('id'))
        native = post_id(row.get('conversation_id'))
        aliases = [alias for value in row.get('source_ids') or () if (alias := post_id(value))]
        join(ident, [native, row.get('parent_id'), *aliases])
        children.add(ident)
        if row.get('parent_id'):
            parents.add(row['parent_id'])
        if native:
            native_roots.add(native)
        if ident and 'parent_id' in row and not row['parent_id']:
            originals.add(min([ident, *aliases], key=int))

    add_source(source)
    for row in history:
        ident = post_id(row.get('tweet_id'))
        if not ident:
            continue
        # The retired quote publisher kept an acknowledged self-reply ID on
        # its own post row. That reply belongs to our post, not the quoted post.
        child = post_id(row.get('reply_id'))
        if child and child != ident and (row.get('reply_verified_at') or row.get('reply_status') in ('verified', 'confirmed', 'published', 'sent')):
            join(ident, [child])
            own_replies.add(child)
        if not row.get('verified_at'):
            continue
        kind = row.get('kind')
        if kind in ('original', 'follow'):
            originals.add(ident)
        if kind not in ('reply', 'self_reply', 'self-reply'):
            continue
        previous = row.get('source') or {}
        add_source(previous)
        stored = row.get('thread_root')
        native = row.get('conversation_id')
        join(ident, [previous.get('id'), stored, native, row.get('parent_id'), row.get('reply_to')])
        if stored:
            stored_roots.add(stored)
        if native:
            native_roots.add(native)
        own_replies.add(ident)
        addressed = (previous.get('author_id') if kind == 'reply' else None) or row.get('recipient_id')
        if recipient and post_id(addressed) == recipient:
            recipient_replies.add(ident)
    for root, people in (observed or {}).items():
        for person, ids in people.items():
            join(root, ids)
            own_replies.update(ids)
            if recipient and person == recipient:
                recipient_replies.update(ids)

    connected = set()
    pending = [source['id']]
    while pending:
        ident = pending.pop()
        if ident not in connected:
            connected.add(ident)
            pending.extend(links.get(ident, set()) - connected)
    proven = (native_roots & connected) or (originals & connected)
    roots = proven or (stored_roots & connected) or ((parents - children - own_replies) & connected)
    root = min(roots, key=int) if roots else source.get('parent_id') or source['id']
    return root, len(recipient_replies & connected), bool(proven)


def discovery_source(candidate, now):
    """Normalize a public original already selected by the native discovery reader."""
    if candidate.get('origin') == 'mention' or candidate.get('parent_id'):
        raise sources.SourceError('Discovery replies require a public original')
    row = sources.validate(candidate, now)
    if re.search(r'(?<![\w@])@' + re.escape(ACCOUNT) + r'\b', row['text'], re.I):
        raise sources.SourceError('An account tag must use the mention path')
    row.update(origin='discovery', parent_id=None, parent_author=None,
               conversation_id=min(row['source_ids'], key=int))
    return row


def reply_source_current(source, state, now):
    if source.get('origin') == 'mention':
        return fresh_invitation(source, now, INBOX_START_AT, state=state)
    try:
        if source.get('origin') == 'priority':
            priority.validate(source, now, state)
            # The final check also runs after the durable source reservation.
            reply_writer.check_source(source, now)
        else:
            discovery_source(source, now)
            reply_writer.check_source(source, now, state)
    except (sources.SourceError, reply_writer.SourceRejected):
        return False
    return True


def thread_root(source, history):
    return thread_evidence(source, history)[0]


def thread_reply_count(source, history, observed=None):
    return thread_evidence(source, history, observed)[1]


async def live_thread_open(state, source, post, own_id, connection, visible, *, search=False):
    observed = state.get('observed_recipient_replies', {})
    root, count, proven = thread_evidence(source, state['history'], observed)
    if not proven or not reply_source_current(source, state, utcnow()):
        return False
    native_root = source.get('conversation_id') or root
    recipient = source['author_id']
    visible.update(mentions.native_reply_ids(post, own_id, native_root, recipient))
    people = observed.get(root, {})
    recorded = set(people.get(recipient, ()))
    found = recorded | visible
    if found != recorded:
        count = thread_reply_count(source, state['history'], {**observed, root: {**people, recipient: found}})
    complete = True
    if search and count < THREAD_REPLY_LIMIT:
        unattributed = set(state.get('observed_thread_replies', {}).get(root, ()))
        for known in people.values():
            unattributed.difference_update(known)
        evidence = {'known_reply_ids': sorted(unattributed)[:2]} if unattributed else {}
        matches = await mentions.conversation_reply_ids(connection, own_id, native_root, recipient, source['author'], **evidence)
        complete = matches is not None
        if matches:
            found.update(matches)
    if found != recorded:
        state.setdefault('observed_recipient_replies', {}).setdefault(root, {})[recipient] = sorted(found)
        save_and_push(state)
        count = thread_reply_count(source, state['history'], state['observed_recipient_replies'])
    return complete and count < THREAD_REPLY_LIMIT


def eligible_author(source, state, now, own_user_id):
    author_id = source['author_id']
    if (source['author'].casefold() == ACCOUNT or author_id == own_user_id
            or mentions.opted_out(state, author_id)):
        return False
    if source.get('origin') == 'priority':
        try:
            priority.validate(source, now, state)
        except sources.SourceError:
            return False
        return thread_reply_count(source, state['history'], state.get('observed_recipient_replies')) < THREAD_REPLY_LIMIT
    if source.get('origin') == 'mention':
        if not fresh_invitation(source, now, INBOX_START_AT, state=state) or not mentions.invited(state, source):
            return False
        own_parent = (source.get('parent_author') == ACCOUNT or (own_user_id and source.get('parent_user_id') == own_user_id)
                      or (source.get('parent_context') or {}).get('author', '').casefold() == ACCOUNT)
        if own_parent and not mentions.is_own_reply(source, own_user_id):
            return False
        return thread_reply_count(source, state['history'], state.get('observed_recipient_replies')) < THREAD_REPLY_LIMIT
    return not any(row.get('kind') == 'reply'
                   and row.get('source', {}).get('author_id') == source['author_id']
                   and timestamp(row['posted_at']) > now - timedelta(hours=24)
                   for row in state['history'])


def unchanged(before, after):
    return all(before.get(key) == after.get(key) for key in
               ('id', 'author_id', 'author', 'text', 'created_at', 'urls', 'source_ids',
                'origin', 'intent', 'parent_id', 'parent_author', 'parent_user_id', 'conversation_id',
                'parent_context'))


def rejection_key(candidate):
    return reply_writer.fingerprint(candidate['text'] + candidate['id'])


def reply_rejection_active(candidate, entry, now):
    if not entry or timestamp(entry['until']) <= now:
        return False
    if candidate.get('origin') != 'mention' or candidate.get('intent') != 'invite':
        return True
    # Older releases deferred these direct invitations to the next UTC hour.
    # The hourly research allowance no longer applies to their writer/reviewer.
    reason = entry.get('reason', '')
    if (reason == 'AI decision exceeds hourly headroom'
            or re.fullmatch(r'request needs \d+ estimated neurons, \d+ available this hour', reason)):
        return False
    if entry.get('not_before') and timestamp(entry['not_before']) > now:
        return True
    version = reply_writer.EDITORIAL_POLICY_VERSION
    if entry.get('kind') == 'draft_format':
        return (entry.get('retried_version') == version
                or (entry.get('policy_version') == version and timestamp(entry['retry_at']) > now))
    # Old ledgers stored both two-hour missing-source and six-hour writer
    # decisions as just an expiry. Only the unambiguous six-hour window,
    # with the specific source feature addressed by v10, gets reconsidered.
    if set(entry) == {'until'} and timestamp(entry['until']) > now + timedelta(hours=2):
        return not reply_writer.has_spelled_quantity(candidate['text'])
    return True


def draft_format_hold(candidate, now, retried_version=None, *, retry_delay=timedelta(minutes=10)):
    return {'kind': 'draft_format', 'policy_version': reply_writer.EDITORIAL_POLICY_VERSION,
            'retried_version': retried_version, 'retry_at': (now + retry_delay).isoformat(),
            'until': (timestamp(candidate['created_at']) + sources.MAX_AGE + timedelta(seconds=1)).isoformat()}


def delivery_open(state):
    return not any(state.get(key) for key in ('pending', 'pending_reply', 'verification_pending',
                                               'pause_reason', 'follow_pending', 'outbox'))


def publication_hold(state, now):
    if not delivery_open(state):
        return 'Publication is paused for reconciliation of a previous send.'
    holds = []
    if state.get('x_pause_until'):
        until = timestamp(state['x_pause_until'])
        if until > now:
            holds.append(f'X rate-limit cooldown until {until.isoformat()}.')
    pause = active_pause(state, now)
    if pause:
        holds.append(f'AI paused until {pause.until.isoformat()}: {pause.reason}')
    return ' '.join(holds)


def matches_publication(pending, post):
    user = getattr(post, 'user', None)
    text = getattr(post, 'full_text', None) or getattr(post, 'text', '')
    if not isinstance(text, str):
        return False
    expected_parent = pending.get('source', {}).get('id') if pending['kind'] == 'reply' else None
    actual_parent = str(getattr(post, 'in_reply_to', '') or '') or None
    raw = getattr(post, '_data', {}) or {}
    legacy = raw.get('legacy', {}) or {}
    def copy_identity(value):
        return unicodedata.normalize('NFC', html.unescape(value))
    body = copy_identity(text)
    expected_body = copy_identity(pending['text'])
    # The native display range distinguishes X's automatic thread recipients
    # from handles typed by the author. It must refer to this exact full_text.
    if pending['kind'] == 'reply' and legacy.get('display_text_range') is not None:
        body = copy_identity(mentions._reply_text(text, legacy))
    elif pending['kind'] == 'reply' and body != expected_body and body.endswith(' ' + expected_body):
        # Without native bounds, accept only the source author followed by
        # known participants, immediately before the complete reserved text.
        source = pending['source']
        parent = '@' + source['author'].casefold()
        prose = re.sub(r'https?://\S+', '', source['text'], flags=re.I)
        tagged = re.findall(r'(?<![\w/@])@[A-Za-z0-9_]{1,15}(?![A-Za-z0-9_])', prose)
        allowed = {parent, *(mention.casefold() for mention in tagged)}
        context = source.get('parent_context') or {}
        if context.get('author_id') == pending['author_id'] and context.get('author', '').casefold() == ACCOUNT:
            allowed.add('@' + ACCOUNT)
        prefix = body[:-(len(expected_body) + 1)]
        recipients = prefix.lower().split(' ')
        if (prefix.isascii() and recipients[0] == parent and len(recipients) == len(set(recipients))
                and all(recipient in allowed for recipient in recipients)):
            body = expected_body
    return (str(getattr(user, 'id', '')) == pending['author_id']
            and str(getattr(user, 'screen_name', '')).casefold() == ACCOUNT
            and actual_parent == expected_parent and body == expected_body
            and not (getattr(post, 'quoted_status_id', None) or getattr(post, 'quote', None)
                     or legacy.get('is_quote_status') or getattr(post, 'retweeted_tweet', None)))


async def recover_missing_id(state, connection):
    pending = state['outbox']
    reserved = timestamp(pending['reserved_at'])
    now = utcnow()
    # The job can run for at most 32 minutes. Allow for rounded X timestamps.
    earliest = reserved - timedelta(seconds=2)
    latest = min(now + timedelta(seconds=2), reserved + timedelta(minutes=32))
    feed = 'Replies' if pending['kind'] == 'reply' else 'Tweets'
    # A nearby verified post helps distinguish an actual timeline covering the
    # incident from an empty or truncated response. Pinned rows can be old.
    anchor = next((row['tweet_id'] for row in reversed(state['history'])
                   if row.get('verified_at') and row.get('tweet_id') and row.get('posted_at')
                   and (feed == 'Replies' or row.get('kind') in ('follow', 'original'))
                   and timestamp(row['posted_at']) < reserved), None)
    matches = set()
    covered = False
    exhausted = False
    try:
        page = await connection.get_user_tweets(pending['author_id'], feed, count=40)
        for index in range(3):
            if not page:
                exhausted = True
                break
            for post in page:
                user = getattr(post, 'user', None)
                if (str(getattr(user, 'id', '')) != pending['author_id']
                        or str(getattr(user, 'screen_name', '')).casefold() != ACCOUNT):
                    continue
                post_id = str(getattr(post, 'id', ''))
                if not post_id.isascii() or not post_id.isdigit() or int(post_id) <= 0:
                    continue
                created = getattr(post, 'created_at_datetime', None)
                if not isinstance(created, datetime) or created.tzinfo is None:
                    continue
                if post_id == anchor and created < reserved:
                    covered = True
                if earliest <= created <= latest and matches_publication(pending, post):
                    matches.add(post_id)
            if covered or index == 2:
                break
            page = await page.next()
    except Exception as exc:
        summary(f'Own timeline read failed ({type(exc).__name__}). Reservation remains pending.')
        return False
    if len(matches) == 1 and (covered or exhausted):
        pending['tweet_id'] = matches.pop()
        save_and_push(state)
        return True
    if (not matches and covered and pending.get('send_error') == 'CouldNotTweet'
            and now >= reserved + timedelta(minutes=32)):
        state.setdefault('delivery_rejections', []).append(
            {**pending, 'error': 'CouldNotTweet', 'outcome': 'unconfirmed', 'checked_at': now.isoformat()})
        state['outbox'] = None
        save_and_push(state)
        summary('X returned CouldNotTweet. No matching publication appeared in the checked timeline. '
                'Archived the unconfirmed attempt and kept its duplicate protections.')
        return True
    summary('Could not identify one matching publication. Reservation remains pending; no resend.')
    return False


async def verify_delivery(state, connection):
    pending = state.get('outbox')
    if not pending:
        return True
    if not pending.get('tweet_id'):
        stage('check own timeline for reserved publication', pending.get('source', {}).get('id'))
        if not await recover_missing_id(state, connection):
            return False
        if state['outbox'] is None:
            return True
    stage('verify published post', pending.get('source', {}).get('id'))
    try:
        post = await connection.get_tweet_by_id(pending['tweet_id'])
    except Exception as exc:
        summary(f'Post ID recorded; readback is pending ({type(exc).__name__}). No resend.')
        return False
    if str(getattr(post, 'id', '')) != pending['tweet_id'] or not matches_publication(pending, post):
        state['pause_reason'] = 'Published post does not match the reserved author, text or reply parent'
        save_and_push(state)
        summary(state['pause_reason'])
        user = getattr(post, 'user', None)
        legacy = (getattr(post, '_data', {}) or {}).get('legacy', {}) or {}
        summary('Public readback: ' + json.dumps({
            'id': str(getattr(post, 'id', '')), 'author_id': str(getattr(user, 'id', '')),
            'author': str(getattr(user, 'screen_name', '')), 'parent': getattr(post, 'in_reply_to', None),
            'text': getattr(post, 'full_text', None) or getattr(post, 'text', ''),
            'quote_id': getattr(post, 'quoted_status_id', None),
            'is_quote': bool(legacy.get('is_quote_status') or getattr(post, 'quote', None)),
            'is_repost': bool(getattr(post, 'retweeted_tweet', None)),
        }, ensure_ascii=False))
        return False
    now = utcnow()
    # Reconciliation can happen much later than publication. Prefer the verified
    # server timestamp so caps and the 30-minute gap track actual published posts.
    created = getattr(post, 'created_at_datetime', None)
    if (isinstance(created, datetime) and created.tzinfo is not None
            and timestamp(pending['reserved_at']) - timedelta(seconds=2) <= created <= now + timedelta(seconds=2)):
        completed = created.astimezone(timezone.utc).isoformat()
    else:
        completed = now.isoformat()
    row = {**pending, 'posted_at': completed, 'completed_at': completed, 'verified_at': now.isoformat()}
    state['history'].append(row)
    state['editorial_history'].append({key: row[key] for key in ('slot', 'kind', 'completed_at', 'tweet_id')})
    state['last_posted_at'] = row['posted_at']
    state['outbox'] = None
    if state.get('pause_reason') == 'Published post does not match the reserved author, text or reply parent':
        state.pop('pause_reason')
    save_and_push(state)
    summary(f'{row["kind"].capitalize()} verified: https://x.com/{ACCOUNT}/status/{row["tweet_id"]}')
    if row.get('kind') == 'reply' and row.get('reply_style') == 'reasoned_answer':
        reasoned_progress(state, now)
    return True


async def send_once(state, slot, draft, connection, user, source=None):
    if slot.get('kind') != 'reply' or not isinstance(source, dict) or not post_id(source.get('id')):
        summary('Replies only: a verified parent post is required. No send.')
        return
    if str(getattr(user, 'screen_name', '')).casefold() != ACCOUNT or not str(getattr(user, 'id', '')).isdigit():
        raise SessionUnavailable()
    if not delivery_open(state):
        summary('Publication paused for reconciliation; no send.')
        return
    # Recheck after generation/refresh, including daily caps and spacing.
    now = utcnow()
    if slot.get('scheduled_at'):
        start = timestamp(slot['scheduled_at'])
        valid = (scheduled_remaining(schedule_state(state), slot['kind'], now, start) > 0
                 and (source['id'] == slot.get('inbox_id')
                      or (slot.get('discovery_reply') and source['id'] == slot.get('discovery_id'))
                      or (slot.get('priority_reply') and source['id'] == slot.get('priority_id'))))
    elif slot.get('inbox_id'):
        valid = (source['id'] == slot['inbox_id']
                 and reply_ready(schedule_state(state), now) and now < timestamp(slot['expires_at']))
    elif 'allowed_actions' in slot:
        due = next_decision(schedule_state(state), now)
        if due and slot['kind'] not in due[0]['allowed_actions']:
            due = None
        valid = due and due[0]['slot'] == slot['slot']
    else:
        due = next_slot(schedule_state(state), now)
        valid = due and due[0]['slot'] == slot['slot']
    if not valid:
        reject(state, slot, 'Opportunity changed during research; no send.')
        return
    if source.get('origin') == 'mention':
        mentions.validate(source, utcnow())
    elif source.get('origin') == 'priority' and slot.get('priority_reply') and slot.get('scheduled_at'):
        priority.validate(source, utcnow(), state)
        reply_writer.check_source(source, utcnow(), state)
    elif slot.get('discovery_reply') and slot.get('scheduled_at'):
        discovery_source(source, utcnow())
        reply_writer.check_source(source, utcnow(), state)
    else:
        reject(state, slot, 'The source is not eligible for this reply opportunity.')
        return
    if (not thread_evidence(source, state['history'], state.get('observed_recipient_replies'))[2]
            or thread_reply_count(source, state['history'], state.get('observed_recipient_replies')) >= THREAD_REPLY_LIMIT
            or not eligible_author(source, state, utcnow(), str(user.id))
            or set(source['source_ids']) & set(state['used_source_ids'])
            or set(source['urls']) & set(state['used_urls'])):
        reject(state, slot, 'Source or author was already used; no send.')
        return
    reserved = {**draft, 'slot': slot['slot'], 'kind': slot['kind'], 'author_id': str(user.id),
                'reserved_at': utcnow().isoformat(), 'editorial_version': 'qofeno-replies-2026-11-25'}
    reserved['source'] = source
    reserved['thread_root'] = thread_root(source, state['history'])
    state['used_source_ids'] = sorted(set(state['used_source_ids'] + source['source_ids']))
    state['used_urls'] = sorted(set(state['used_urls'] + source['urls']))
    state['used_events'] = sorted(set(state['used_events'] + [reply_writer.fingerprint(draft['event_key'])]))
    state['outbox'] = reserved
    state['used_texts'] = sorted(set(state['used_texts'] + [reply_writer.fingerprint(draft['text'])]))
    save_and_push(state)  # Remote reservation must succeed before the public side effect.
    send_time = utcnow()
    if not reply_source_current(source, state, send_time):
        state['outbox'] = None
        reject(state, slot, 'The incoming post is outside the live reply window; no reply sent.')
        return
    if send_time >= timestamp(slot['expires_at']):
        state['outbox'] = None
        reject(state, slot, 'Reservation completed after the opportunity expired; no send.')
        return
    sent_at = send_time.isoformat()
    stage('send reply', source['id'])
    try:
        post = await connection.create_tweet(text=draft['text'], reply_to=source['id'])
    except (Forbidden, NotFound, TweetNotAvailable) as exc:
        # Explicit native rejection. Keep burned identities, do not change
        # credentials/transport or try to bypass conversation restrictions.
        state.setdefault('delivery_rejections', []).append({**reserved, 'error': type(exc).__name__})
        state['outbox'] = None
        reject(state, slot, f'X rejected this {slot["kind"]} ({type(exc).__name__}); skipped, no bypass.')
        return
    except TooManyRequests as exc:
        state.setdefault('delivery_rejections', []).append({**reserved, 'error': 'TooManyRequests'})
        state['outbox'] = None
        now = utcnow()
        until = now + timedelta(minutes=30)
        reset = exc.rate_limit_reset
        if reset is not None and reset > now.timestamp():
            until = datetime.fromtimestamp(reset, timezone.utc)
        if state.get('x_pause_until'):
            until = max(until, timestamp(state['x_pause_until']))
        state['x_pause_until'] = until.isoformat()
        reject(state, slot, f'X rate limit reached. Publication paused until {until.isoformat()}.')
        return
    except CouldNotTweet as exc:
        # The pinned client raises this exact type on a GraphQL error with
        # no tweet result. DuplicateTweet can refer to an existing post.
        if type(exc) is not CouldNotTweet:
            raise
        daily_limit = re.search(r'\b(?:daily (?:\w+ ){0,3}(?:limit|quota)|(?:limit|quota) (?:per|for the) day)\b',
                                str(exc), re.I)
        cooldown = timedelta(days=1) if daily_limit else timedelta(minutes=30)
        until = utcnow() + cooldown
        if state.get('x_pause_until'):
            until = max(until, timestamp(state['x_pause_until']))
        reserved['send_error'] = 'CouldNotTweet'
        state['x_pause_until'] = until.isoformat()
        reject(state, slot, f'X returned a posting error without an ID. Reserved for verification. Next check after {until.isoformat()}.')
        return
    # Other exceptions may have occurred after acceptance: retain the reservation.
    ident = str(getattr(post, 'id', ''))
    if not ident.isascii() or not ident.isdigit() or int(ident) <= 0:
        raise ValueError('Post response has no valid ID; reservation remains')
    state['outbox']['tweet_id'] = ident
    state['outbox']['sent_at'] = sent_at
    save_and_push(state)
    await verify_delivery(state, connection)


def refreshed_reply_source(post, candidate, state, now):
    if candidate.get('origin') == 'mention':
        return mentions.snapshot(post, now)
    if candidate.get('origin') == 'priority':
        return priority.validate(priority.snapshot(post, now), now, state)
    return discovery_source(sources.snapshot(post, now), now)


async def publish_reply(state, slot, connection, user, *, selected=None, memory=None, angle=''):
    own_id = str(user.id)
    now = utcnow()
    candidates = [selected] if selected is not None else await read_inbox(state, connection, now, own_id)
    rejections = state['reply_rejections'] = {
        key: value for key, value in state.get('reply_rejections', {}).items()
        if timestamp(value['until']) > now}
    candidates = [candidate for candidate in candidates
                  if (candidate.get('origin') == 'mention' or (slot.get('discovery_reply') and slot.get('scheduled_at'))
                      or (candidate.get('origin') == 'priority' and slot.get('priority_reply') and slot.get('scheduled_at')))
                  and eligible_author(candidate, state, now, own_id)
                  and not reply_rejection_active(candidate, rejections.get(rejection_key(candidate)), now)
                  and not set(candidate['source_ids']).intersection(state['used_source_ids'])
                  and not set(candidate['urls']).intersection(state['used_urls'])]
    if reasoned_goal_open(state, now):
        candidates.sort(key=lambda row: not sources.is_reasoned_question(row))
    deferred = 0
    for _ in range(min(4, len(candidates))):
        candidate = candidates.pop(0)
        key = rejection_key(candidate)
        if utcnow() >= timestamp(slot['expires_at']):
            reject(state, slot, 'Reply opportunity ended during research.')
            return
        try:
            stage('refresh source before writing', candidate['id'])
            post = await connection.get_tweet_by_id(candidate['id'])
            fresh = refreshed_reply_source(post, candidate, state, utcnow())
            visible = set()
            if candidate.get('parent_context'):
                fresh = await mentions.thread_context(connection, fresh, utcnow(), own_user_id=own_id, observed_replies=visible)
            if not unchanged(candidate, fresh) or not await live_thread_open(state, fresh, post, own_id, connection, visible, search=True):
                continue
            stage('write contextual reply', candidate['id'])
            if key in rejections:
                # Persist consumption before the model call. A crash or a repeated
                # wake cannot grant a second attempt under the same policy.
                rejections[key] = draft_format_hold(fresh, utcnow(), reply_writer.EDITORIAL_POLICY_VERSION)
                save_and_push(state)
            if not reply_source_current(fresh, state, utcnow()):
                continue
            # The five-per-day goal changes discovery preference, not the
            # ability to answer a fresh substantive question addressed to us.
            reasoned = (sources.is_reasoned_question(fresh)
                        and (reasoned_goal_open(state, utcnow()) or fresh.get('origin') == 'mention'))
            with budget_scope(state, save_and_push, utcnow, direct_mention=bool(slot.get('inbox_id') or slot.get('priority_id'))):
                draft = reply_writer.generate(fresh, state, utcnow(), memory=memory, angle=angle,
                                              **({'reasoned_answer': True} if reasoned else {}))
            stage('refresh source before sending', candidate['id'])
            post = await connection.get_tweet_by_id(candidate['id'])
            final = refreshed_reply_source(post, candidate, state, utcnow())
            if fresh.get('parent_context'):
                final = await mentions.thread_context(connection, final, utcnow(), own_user_id=own_id, observed_replies=visible)
            if not unchanged(fresh, final) or not await live_thread_open(state, final, post, own_id, connection, visible):
                continue
        except (AIRequestTimeout, AIRequestTooLarge) as exc:
            if isinstance(exc, AIRequestTooLarge):
                until = exc.until
                reason = f'request needs {exc.needed} estimated neurons, {exc.remaining} available this hour'
                # The source is included in both model calls. Prefer a shorter
                # complete source among the remaining eligible candidates.
                candidates.sort(key=lambda item: len(item['text'].encode('utf-8')))
            else:
                until = utcnow() + (INBOX_RETRY_DELAY if slot.get('inbox_id') else timedelta(minutes=30))
                reason = 'Cloudflare timed out before publication'
            if key in rejections:
                rejections[key]['not_before'] = until.isoformat()
            else:
                rejections[key] = {'until': until.isoformat(), 'reason': reason}
            save_and_push(state)
            summary(f'Deferred source {candidate["id"]} until {until.isoformat()}: {reason}.')
            deferred += 1
            continue
        except (sources.SourceError, NotFound, TweetNotAvailable):
            rejections[key] = {'kind': 'unavailable', 'until': (utcnow() + timedelta(hours=2)).isoformat()}
            summary(f'Skipped unavailable or changed source {candidate["id"]}.')
            continue
        except (NetworkError, TimeoutException, RequestTimeout, ServerError) as exc:
            if not slot.get('inbox_id'):
                raise
            # Only source checks and generation run inside this try block.
            # The actual send runs below it and retains its durable reservation on errors.
            summary(f'Temporary X read failure for invitation {candidate["id"]} ({type(exc).__name__}).')
            continue
        except reply_writer.RecoverableDraftError as exc:
            delay = INBOX_RETRY_DELAY if slot.get('inbox_id') else timedelta(minutes=10)
            rejections[key] = draft_format_hold(candidate, utcnow(), rejections.get(key, {}).get('retried_version'),
                                                retry_delay=delay)
            summary(f'Draft format rejected for {candidate["id"]}: {str(exc)[:240]}')
            continue
        except DraftError as exc:
            rejections[key] = {'kind': 'review', 'policy_version': reply_writer.EDITORIAL_POLICY_VERSION,
                               'until': (utcnow() + timedelta(hours=6)).isoformat()}
            summary(f'Source {candidate["id"]} did not pass reply review: {str(exc)[:240]}')
            continue
        return await send_once(state, slot, draft, connection, user, final)
    if deferred:
        reject(state, slot, f'No reply sent in this check. Deferred {deferred} source(s) for AI availability or budget.')
    else:
        reject(state, slot, 'No fresh eligible conversation passed this check. No reply sent.')


async def publish_original(state, slot, connection, user, plan, memory_context):
    # Older callers can still hold original slots from before the cutover.
    summary('Standalone posting is disabled. No draft or send.')


def ledger_after_denial(history):
    # The git ledger already holds the verified publications used to rebuild D1.
    # This projection has no independent writes and cannot confirm a publication.
    summary('::warning::Cloudflare denied D1 access. Using verified publication history for this wake. D1 will be retried.')
    memory = LedgerMemory()
    memory.sync(history)
    return memory


async def read_inbox(state, connection, now, own_user_id=None, *, poll=False):
    stage('read mentions and reply preferences')
    incoming = await mentions.discover(connection, now, used_source_ids=state['used_source_ids'],
                                       own_history=state['history'], own_user_id=own_user_id,
                                       **({'poll': True} if poll else {}))
    if mentions.apply_controls(incoming, state):
        save_and_push(state)
    return incoming


async def check_inbox():
    """Deployment check. Reads X and honors preference commands, never sends."""
    state, now = load(), utcnow()
    if state.get('x_pause_until') and timestamp(state['x_pause_until']) > now:
        summary(f'Mention inbox check deferred until {state["x_pause_until"]} for the X cooldown.')
        return
    rows = await read_inbox(state, client(), now)
    summary(f'Mention inbox check completed. {sum(mentions.invited(state, row) for row in rows)} current invitations.')


def inbox_shortlist(rows, own_id, *, prefer_reasoned_answers=False):
    """Give new callers and ongoing threads a seat each when distinct people are waiting."""
    def priority(row):
        text = row['text']
        tag = mentions.TAG.search(text)
        body = text[tag.end():].lstrip(' ,:—-') if tag else text
        asked = bool(mentions.ASK.match(body) or re.search('[?？؟]', text))
        reasoned = prefer_reasoned_answers and sources.is_reasoned_question(row)
        return reasoned, asked, timestamp(row['created_at']), int(row['id'])

    ordered = sorted(rows, key=priority, reverse=True)
    followups = [row for row in ordered if mentions.is_own_reply(row, own_id)]
    new_mentions = [row for row in ordered if not mentions.is_own_reply(row, own_id)]
    for mention in new_mentions:
        followup = next((row for row in followups if row['author_id'] != mention['author_id']), None)
        if followup:
            return [mention, followup]
    if not ordered:
        return []
    first = ordered[0]
    second = next((row for row in ordered if row['author_id'] != first['author_id']), None)
    return [first, second] if second else [first]


def candidate_eligible(candidate, state, now, own_id):
    rejection = state.get('reply_rejections', {}).get(rejection_key(candidate))
    return (eligible_author(candidate, state, now, own_id)
            and not set(candidate['source_ids']).intersection(state['used_source_ids'])
            and not set(candidate['urls']).intersection(state['used_urls'])
            and not reply_rejection_active(candidate, rejection, now))


def inbox_opportunity(row):
    created = timestamp(row['created_at'])
    version = personality.invitation_version(row)
    return {'slot': f'{created.date().isoformat()}-inbox-{row["id"]}-{version[:16]}',
            'inbox_id': row['id'], 'kind': 'decision', 'allowed_actions': ['reply'],
            'expires_at': (created + sources.MAX_AGE).isoformat()}


def inbox_reviewed(state, row):
    decision = state.get('decision_slots', {}).get(inbox_opportunity(row)['slot'], {})
    return bool(decision.get('direct_reviewed'))


def record_silence(state, slot, now, plan):
    days = {now.date().isoformat(), (now.date() - timedelta(days=1)).isoformat()}
    decisions = {k: v for k, v in state.get('decision_slots', {}).items() if k[:10] in days}
    decisions[slot['slot']] = {'action': 'silent', 'at': now.isoformat()}
    if slot.get('allowed_actions') == ['original']:
        decisions[slot['slot']]['original_source_policy'] = sources.ORIGINAL_SOURCE_POLICY_VERSION
    if slot.get('inbox_id'):
        decisions[slot['slot']]['direct_reviewed'] = True
    for key in ('offered_invitations', 'summon_authors'):
        if plan.get(key):
            decisions[slot['slot']][key] = plan[key]
    state['decision_slots'] = decisions
    save_and_push(state)


async def decide(state, slot, connection, user, *, incoming=None):
    if 'reply' not in slot.get('allowed_actions', ()):
        summary('Standalone publishing is disabled. No original decision or draft requested.')
        return
    # Retired mixed callers may still supply original as an option. The live
    # planner receives only reply/silence, independently of workflow settings.
    slot = {**slot, 'allowed_actions': ['reply']}
    stage('sync verified publications to D1')
    try:
        memory = Memory.connect()
        memory.sync(state['history'])
    except MemoryAccessDenied:
        memory = ledger_after_denial(state['history'])
    now = utcnow()
    own_id = str(user.id)
    if incoming is None:
        if 'reply' in slot['allowed_actions']:
            try:
                incoming = await read_inbox(state, connection, now, own_id)
            except (NotFound, TweetNotAvailable):
                summary('Mention inbox unavailable for this opportunity.')
                incoming = []
        else:
            incoming = []
    prefer_reasoned = reasoned_goal_open(state, now)
    selected = inbox_shortlist([row for row in incoming if row['intent'] == 'invite'
                                and candidate_eligible(row, state, now, own_id)], own_id,
                               prefer_reasoned_answers=prefer_reasoned)
    invited = [row if row.get('parent_context') else await mentions.thread_context(connection, row, now)
               for row in selected]
    invited_authors = {row['author_id'] for row in invited}
    candidates = []
    # Scheduled discovery can join a public conversation; inbox-only checks remain focused.
    discovery_replies = bool(slot.get('discovery_reply') and slot.get('scheduled_at'))
    if discovery_replies:
        stage('discover public conversations')
        try:
            candidates = await sources.discover(connection, state, now, originals=False,
                                               **({'reasoned_questions': True} if prefer_reasoned else {}))
        except (NotFound, TweetNotAvailable):
            summary('Topic discovery unavailable for this opportunity.')
    # Never feed an incidental tag or opted-out request back as a trending post.
    passive = [c for c in candidates if c['author_id'] not in invited_authors and candidate_eligible(c, state, now, own_id)
               and not re.search(r'(?<![\w@])@' + re.escape(ACCOUNT) + r'\b', c['text'], re.I)]
    discovered = sources.shortlist(passive, now, limit=4 - len(invited), originals=False,
                                   **({'reasoned_questions': True} if prefer_reasoned else {}))
    if discovery_replies:
        discovered = [discovery_source(row, now) for row in discovered]
    shortlist = invited + discovered
    summary(f'Considering {len(invited)} direct invitations and {len(shortlist) - len(invited)} discovered conversations.')
    if discovery_replies and prefer_reasoned and not shortlist:
        # An empty search is not an editorial rejection. Keep the opportunity
        # open for fresher sources on the next wake, without searching twice now.
        summary('No eligible discovery candidates while the daily answer goal is open. A later wake may check fresh sources.')
        return NO_DISCOVERY_CANDIDATES
    try:
        context = memory.context(shortlist)
    except MemoryAccessDenied:
        memory = ledger_after_denial(state['history'])
        context = memory.context(shortlist)
    if slot.get('inbox_id'):
        stage('check direct invitation', slot['inbox_id'])
        exact = next((row for row in invited if row['id'] == slot['inbox_id']), None)
        plan = personality.direct_reply(exact, state, utcnow()) if exact else {'action': 'silent'}
    else:
        stage('choose reply or silence')
        with budget_scope(state, save_and_push, utcnow):
            options = {'allow_discovery_replies': True} if discovery_replies else {}
            if discovery_replies and prefer_reasoned:
                questions = sum(sources.is_reasoned_question(row) for row in discovered)
                summary(f'Eligible substantive questions in this shortlist: {questions}/{len(discovered)}.')
                if questions:
                    options['prefer_reasoned_answers'] = True
            plan = personality.choose(shortlist, context, state, utcnow(), slot['allowed_actions'], **options)
    action = plan['action']
    if action == 'silent':
        record_silence(state, slot, utcnow(), plan)
        summary('Qofeno chose silence for this opportunity.')
        return
    if action not in slot['allowed_actions']:
        raise DraftError('Decision exceeds the current publishing limits')
    before = len(state['history'])
    chosen_slot = {**slot, 'kind': action}
    permitted = invited + discovered if discovery_replies else invited
    source = next((row for row in permitted if row['id'] == plan['source_id']), None)
    if source is None:
        raise DraftError('Reply decision must name an eligible invitation')
    if source.get('origin') == 'discovery':
        chosen_slot['discovery_id'] = source['id']
    await publish_reply(state, chosen_slot, connection, user, selected=source, memory=context, angle=plan['angle'])
    if len(state['history']) > before:
        stage('remember verified publication')
        try:
            memory.sync(state['history'])
        except MemoryAccessDenied:
            ledger_after_denial(state['history'])


async def posting_account(connection):
    stage('resolve configured posting account')
    # CreateTweet authenticates the supplied session. Readback verifies that
    # the actual author matches this public account before any later send.
    user = await connection.get_user_by_screen_name(ACCOUNT)
    if str(getattr(user, 'screen_name', '')).casefold() != ACCOUNT or not str(getattr(user, 'id', '')).isdigit():
        raise SessionUnavailable()
    return user


async def process_inbox(state, connection, *, scheduled_at=None):
    now = utcnow()
    if not reply_ready(schedule_state(state), now):
        return
    own_id = next((row['author_id'] for row in reversed(state['history'])
                   if row.get('verified_at') and row.get('author_id')), None)
    rows = await read_inbox(state, connection, now, own_id, poll=True)
    invitations = [row for row in rows if row['intent'] == 'invite' and candidate_eligible(row, state, now, own_id)]
    first = inbox_shortlist(invitations, own_id, prefer_reasoned_answers=reasoned_goal_open(state, now))
    user = None
    considered = 0
    for row in first + [row for row in invitations if row not in first]:
        now = utcnow()
        if considered >= INBOX_BATCH_SIZE or not reply_ready(schedule_state(state), now):
            break
        if scheduled_at and not scheduled_remaining(schedule_state(state), 'reply', now, scheduled_at):
            break
        # Earlier decisions in this batch can use an alias, consume a thread
        # turn or leave an unresolved send. Recheck on the updated ledger.
        if not candidate_eligible(row, state, now, own_id):
            continue
        if row.get('parent_id') and not row.get('parent_context'):
            row = await mentions.thread_context(connection, row, now)
        slot = inbox_opportunity(row)
        if scheduled_at:
            slot.update(scheduled_at=scheduled_at.isoformat(), expires_at=(scheduled_at + HALF_HOUR).isoformat())
        if now >= timestamp(slot['expires_at']) or inbox_reviewed(state, row):
            continue
        attempt = state.get('last_attempts', {}).get(slot['slot'])
        if attempt and (attempt.get('count', 1) >= MAX_INBOX_ATTEMPTS
                        or timestamp(attempt['at']) + INBOX_RETRY_DELAY > now):
            continue
        if user is None:
            user = await posting_account(connection)
            own_id = str(user.id)
        considered += 1
        try:
            await decide(state, slot, connection, user, incoming=[row])
        except AIRequestTimeout:
            reject(state, slot, 'Workers AI timed out considering this invitation. Deferred before sending.')
        except AIRequestTooLarge as exc:
            state.setdefault('reply_rejections', {})[rejection_key(row)] = {
                'until': exc.until.isoformat(), 'reason': 'AI decision exceeds hourly headroom'}
            reject(state, slot, f'Invitation deferred until {exc.until.isoformat()}: {ai_deferral_detail(exc)}.')
        except AIPaused as exc:
            summary(f'AI deferred until {exc.until.isoformat()}: {exc.reason}')
            break
        except DraftError as exc:
            record_silence(state, slot, now, {})
            summary(f'No approved decision for invitation {row["id"]}: {str(exc)[:240]}')


async def process_priority(state, connection, *, scheduled_at):
    stage('read priority account originals')
    rows = await priority.discover(connection, state, utcnow())
    save_and_push(state)  # Bind activation and keep the queue before generation or sends.
    if not rows or not scheduled_remaining(schedule_state(state), 'reply', utcnow(), scheduled_at):
        return
    user = await posting_account(connection)
    # Apply newly received stop/start preferences before any priority reply.
    await read_inbox(state, connection, utcnow(), str(user.id), poll=True)
    eligible = [row for row in rows if candidate_eligible(row, state, utcnow(), str(user.id))]
    if reasoned_goal_open(state, utcnow()):
        eligible.sort(key=lambda row: not sources.is_reasoned_question(row))
    summary(f'Priority @{priority.HANDLE}: eligible after preferences/dedupe={len(eligible)}; shared reply allowance={scheduled_remaining(schedule_state(state), "reply", utcnow(), scheduled_at)}.')
    if not eligible:
        return
    try:
        memory = Memory.connect()
        memory.sync(state['history'])
        context = memory.context(eligible[:INBOX_BATCH_SIZE])
    except MemoryAccessDenied:
        memory = ledger_after_denial(state['history'])
        context = memory.context(eligible[:INBOX_BATCH_SIZE])
    for row in eligible[:INBOX_BATCH_SIZE]:
        now = utcnow()
        if not scheduled_remaining(schedule_state(state), 'reply', now, scheduled_at):
            break
        slot = {'slot': f'{scheduled_at.date()}:priority:{row["id"]}', 'kind': 'reply',
                'priority_reply': True, 'priority_id': row['id'],
                'scheduled_at': scheduled_at.isoformat(),
                'expires_at': (scheduled_at + HALF_HOUR).isoformat()}
        before = len(state['history'])
        try:
            await publish_reply(state, slot, connection, user, selected=row, memory=context)
        except AIPaused as exc:
            # This is a global budget/provider hold. Keep all queued originals.
            reject(state, slot, f'Priority reply deferred: {ai_deferral_detail(exc)}.')
            break
        except (AIRequestTimeout, DraftError) as exc:
            # Most source failures are handled by publish_reply itself. If a
            # final validation escapes, hold only this source and let later
            # queued originals use the remaining shared allowance.
            until = utcnow() + (timedelta(minutes=30) if isinstance(exc, AIRequestTimeout) else timedelta(hours=6))
            state.setdefault('reply_rejections', {})[rejection_key(row)] = {
                'kind': 'timeout' if isinstance(exc, AIRequestTimeout) else 'review',
                'policy_version': reply_writer.EDITORIAL_POLICY_VERSION, 'until': until.isoformat()}
            reject(state, slot, f'Priority source {row["id"]} deferred until {until.isoformat()}: {type(exc).__name__}.')
            continue
        if len(state['history']) > before:
            try:
                memory.sync(state['history'])
                context = memory.context(eligible[:INBOX_BATCH_SIZE])
            except MemoryAccessDenied:
                memory = ledger_after_denial(state['history'])
                context = memory.context(eligible[:INBOX_BATCH_SIZE])


async def scheduled_wake(state, connection, now):
    start = half_hour_start(now)
    reasoned_progress(state, now)
    if not state.get('actions_inbox_since'):
        # Switching to a half-hour cron must not reopen a backlog that already
        # expired under the previous publisher's five-minute policy.
        state['actions_inbox_since'] = (now - MAX_REPLY_AGE).isoformat()
        save_and_push(state)
    try:
        await process_priority(state, connection, scheduled_at=start)
    except (sources.SourceError, Forbidden, NotFound, TweetNotAvailable, UserNotFound, UserUnavailable,
            NetworkError, TimeoutException, RequestTimeout, ServerError) as exc:
        if not delivery_open(state):
            raise
        # Print only the error class; native payloads can contain session details.
        summary(f'Priority @{priority.HANDLE} lookup/timeline unavailable ({type(exc).__name__}); saved account and queue preserved.')
    if scheduled_remaining(schedule_state(state), 'reply', now, start):
        try:
            await process_inbox(state, connection, scheduled_at=start)
        except (NetworkError, TimeoutException, RequestTimeout, ServerError) as exc:
            # An uncertain X write has a persistent outbox. Reconcile it on
            # the next wake before publishing anything else.
            if not delivery_open(state):
                raise
            summary(f'The reply inbox was unavailable ({type(exc).__name__}).')
    # Priority accounts and direct invitations retain first use of the shared budget.
    # Silence closes this discovery slot, and each verified send consumes the shared cap.
    for index in range(2):
        now = utcnow()
        if not scheduled_remaining(schedule_state(state), 'reply', now, start):
            break
        # Targeted question searches have their own stable silence decision.
        # A prior general search cannot close a newly introduced source pool;
        # every pool still shares the verified reply/outbox allowance above.
        lane = 'actions-discovery-reasoned-v3' if reasoned_goal_open(state, now) else 'actions-discovery'
        slot_id = f'{start.date()}:{lane}:{2 * start.hour + start.minute // 30}:{index}'
        if slot_id in state.get('decision_slots', {}):
            break
        slot = {'slot': slot_id, 'kind': 'decision', 'allowed_actions': ['reply'],
                'discovery_reply': True, 'scheduled_at': start.isoformat(),
                'expires_at': (start + HALF_HOUR).isoformat()}
        user = await posting_account(connection)
        try:
            outcome = await decide(state, slot, connection, user, incoming=[])
        except (AIRequestTimeout, AIPaused, DraftError) as exc:
            reject(state, slot, f'Discovery reply deferred: {ai_deferral_detail(exc)}.')
            break
        if outcome == NO_DISCOVERY_CANDIDATES or slot_id in state.get('decision_slots', {}):
            break


async def run(event):
    start = utcnow()
    state = load()
    if state.get('x_pause_until') and timestamp(state['x_pause_until']) > start:
        summary(f'X cooldown until {state["x_pause_until"]}.')
        return
    connection = client()
    if not await verify_delivery(state, connection):
        pending = state.get('outbox')
        if pending and start - timestamp(pending['reserved_at']) >= timedelta(hours=1):
            raise DeliveryUnresolved(f'X delivery for slot {pending["slot"]} remains unverified. See the reconciliation result above.')
        return
    if not delivery_open(state):
        summary('A previous publication requires reconciliation. No new send.')
        return
    if event == 'original':
        summary('Standalone publishing is disabled. Legacy original mode reconciled existing delivery only.')
        return
    if event not in ('inbox', 'scheduled', 'push', 'repository_dispatch', 'workflow_dispatch'):
        summary('Unsupported publishing mode; no new send.')
        return
    try:
        if event == 'inbox':
            await process_inbox(state, connection)
        else:
            await scheduled_wake(state, connection, start)
    except TooManyRequests as exc:
        now = utcnow()
        reset = getattr(exc, 'rate_limit_reset', None)
        until = datetime.fromtimestamp(reset, timezone.utc) if reset and reset > now.timestamp() else now + timedelta(minutes=15)
        state['x_pause_until'] = until.isoformat()
        save_and_push(state)
        summary(f'X rate-limited inbox access. Next check after {until.isoformat()}.')


async def inspect():
    state = load()
    now = utcnow()
    status = publication_hold(state, now) or 'Replies-only publishing is enabled; standalone posts are disabled.'
    inbox = 'open' if reply_ready(schedule_state(state), now) else 'held'
    summary(f'{status} Independent mention and thread-reply checks: {inbox}. '
            f'Confirmed publications: {len(state["history"])}. '
            f'Pending send: {bool(state.get("outbox"))}. No model or send calls.')
