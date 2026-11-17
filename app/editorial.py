"""Autonomous public conversations and original posts for Qofeno.

Every send is durably reserved first. Unknown outcomes block publication rather
than retrying. Native X permission and rate-limit failures are never bypassed.
"""
import asyncio
import html
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from twikit.errors import CouldNotTweet, Forbidden, NotFound, TooManyRequests, TweetNotAvailable
from . import mentions, personality, reply_writer, sources
from .memory import LedgerMemory, Memory, MemoryAccessDenied
from .ai_budget import AIPaused, AIRequestTooLarge, active_pause, budget_scope
from .cloudflare import AIRequestTimeout, DraftError, selected_model
from .reply_schedule import RECHECK_GAP, next_decision, next_original, next_slot, reply_ready, timestamp
from .store import ACCOUNT, SessionUnavailable, client, load, save_and_push, summary, utcnow

STAGE = {'name': 'startup', 'source_id': None}
THREAD_TURNS_PER_PERSON = 3
INBOX_BATCH_SIZE = 4


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
    return {**state, 'history': history,
            'pending': state.get('pending') or state.get('pending_reply') or state.get('follow_pending') or state.get('outbox')}


def reject(state, slot, reason):
    now = utcnow()
    state['last_attempts'] = {key: val for key, val in state.get('last_attempts', {}).items()
                              if val.get('at') and timestamp(val['at']) > now - timedelta(days=1)}
    state['last_attempts'][slot['slot']] = {'at': now.isoformat(), 'reason': reason}
    save_and_push(state)
    summary(reason)


def thread_root(source, history):
    """Keep a stable thread ID across nested replies, including older ledger entries."""
    if source.get('conversation_id'):
        return source['conversation_id']
    parent_id = source.get('parent_id')
    if not parent_id:
        return source['id']
    # New sends persist the root. Walk parents for old records whose native
    # conversation ID wasn't saved until this version of the inbox.
    own_posts = {row['tweet_id']: row for row in history if row.get('verified_at') and row.get('tweet_id')}
    seen = set()
    while parent_id in own_posts and parent_id not in seen:
        seen.add(parent_id)
        parent = own_posts[parent_id]
        previous = parent.get('source') or {}
        explicit = parent.get('thread_root') or previous.get('conversation_id')
        if explicit:
            return explicit
        older = previous.get('parent_id') or previous.get('id')
        if not older:
            break
        parent_id = older
    return parent_id


def eligible_author(source, state, now, own_user_id):
    author_id = source['author_id']
    if (source['author'].casefold() == ACCOUNT or author_id == own_user_id
            or mentions.opted_out(state, author_id)):
        return False
    if source.get('origin') == 'mention':
        if not mentions.invited(state, source):
            return False
        own_parent = (source.get('parent_author') == ACCOUNT or (own_user_id and source.get('parent_user_id') == own_user_id)
                      or (source.get('parent_context') or {}).get('author', '').casefold() == ACCOUNT)
        if own_parent and not mentions.is_own_reply(source, own_user_id):
            return False
        history = state['history']
        root = thread_root(source, history)
        cutoff = now - timedelta(days=1)
        turns = sum(1 for row in history if row.get('kind') == 'reply' and row.get('verified_at')
                    and row.get('source', {}).get('author_id') == author_id
                    and timestamp(row['posted_at']) > cutoff
                    and (row.get('thread_root') or thread_root(row['source'], history)) == root)
        return turns < THREAD_TURNS_PER_PERSON
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


def draft_format_hold(candidate, now, retried_version=None):
    return {'kind': 'draft_format', 'policy_version': reply_writer.EDITORIAL_POLICY_VERSION,
            'retried_version': retried_version, 'retry_at': (now + timedelta(minutes=10)).isoformat(),
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


def upcoming_opportunity(state, now, scheduler=next_original):
    scheduled = schedule_state(state)
    midnight = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    found = scheduler(scheduled, now, midnight) or scheduler(scheduled, midnight, midnight + timedelta(days=1))
    if not found:
        return 'No eligible original research opportunity in the current or following UTC day.'
    _, ready = found
    return f'Next eligible research opportunity for an original starts at {ready.isoformat()}.'


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
    return True


async def send_once(state, slot, draft, connection, user, source=None):
    if str(getattr(user, 'screen_name', '')).casefold() != ACCOUNT or not str(getattr(user, 'id', '')).isdigit():
        raise SessionUnavailable()
    if not delivery_open(state):
        summary('Publication paused for reconciliation; no send.')
        return
    # Recheck after generation/refresh, including daily caps and spacing.
    now = utcnow()
    if slot.get('inbox_id'):
        valid = (slot['kind'] == 'reply' and source and source['id'] == slot['inbox_id']
                 and reply_ready(schedule_state(state), now) and now < timestamp(slot['expires_at']))
    elif 'allowed_actions' in slot:
        scheduler = next_original if slot.get('original_only') else next_decision
        due = scheduler(schedule_state(state), now)
        if due and slot['kind'] not in due[0]['allowed_actions']:
            due = None
        valid = due and due[0]['slot'] == slot['slot']
    else:
        due = next_slot(schedule_state(state), now)
        valid = due and due[0]['slot'] == slot['slot']
    if not valid:
        reject(state, slot, 'Opportunity changed during research; no send.')
        return
    if slot['kind'] == 'reply':
        if source is None:
            raise ValueError('A reply requires a verified source')
        if source.get('origin') != 'mention':
            reject(state, slot, 'The source did not invite Qofeno to reply.')
            return
        mentions.validate(source, utcnow())
        if (not eligible_author(source, state, utcnow(), str(user.id))
                or set(source['source_ids']) & set(state['used_source_ids'])
                or set(source['urls']) & set(state['used_urls'])):
            reject(state, slot, 'Source or author was already used; no send.')
            return
    elif source is not None:
        raise ValueError('A standalone post cannot have a reply source')
    reserved = {**draft, 'slot': slot['slot'], 'kind': slot['kind'], 'author_id': str(user.id),
                'reserved_at': utcnow().isoformat()}
    if source:
        reserved['source'] = source
        reserved['thread_root'] = thread_root(source, state['history'])
        state['used_source_ids'] = sorted(set(state['used_source_ids'] + source['source_ids']))
        state['used_urls'] = sorted(set(state['used_urls'] + source['urls']))
    state['used_events'] = sorted(set(state['used_events'] + [reply_writer.fingerprint(draft['event_key'])]))
    state['outbox'] = reserved
    state['used_texts'] = sorted(set(state['used_texts'] + [reply_writer.fingerprint(draft['text'])]))
    save_and_push(state)  # Remote reservation must succeed before the public side effect.
    if utcnow() >= timestamp(slot['expires_at']):
        state['outbox'] = None
        reject(state, slot, 'Reservation completed after the opportunity expired; no send.')
        return
    sent_at = utcnow().isoformat()
    stage('send ' + slot['kind'], source['id'] if source else None)
    try:
        if source:
            post = await connection.create_tweet(text=draft['text'], reply_to=source['id'])
        else:
            post = await connection.create_tweet(text=draft['text'])
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
        if slot['kind'] == 'reply':
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


async def publish_reply(state, slot, connection, user, *, selected=None, memory=None, angle=''):
    own_id = str(user.id)
    now = utcnow()
    candidates = [selected] if selected is not None else await read_inbox(state, connection, now, own_id)
    rejections = state['reply_rejections'] = {
        key: value for key, value in state.get('reply_rejections', {}).items()
        if timestamp(value['until']) > now}
    candidates = [candidate for candidate in candidates if candidate.get('origin') == 'mention'
                  and eligible_author(candidate, state, now, own_id)
                  and not reply_rejection_active(candidate, rejections.get(rejection_key(candidate)), now)
                  and not set(candidate['source_ids']).intersection(state['used_source_ids'])
                  and not set(candidate['urls']).intersection(state['used_urls'])]
    deferred = 0
    for _ in range(min(4, len(candidates))):
        candidate = candidates.pop(0)
        key = rejection_key(candidate)
        if utcnow() >= timestamp(slot['expires_at']):
            reject(state, slot, 'Reply opportunity ended during research.')
            return
        try:
            stage('refresh source before writing', candidate['id'])
            fresh = mentions.snapshot(await connection.get_tweet_by_id(candidate['id']), utcnow())
            if candidate.get('parent_context'):
                fresh = await mentions.thread_context(connection, fresh, utcnow())
            if not unchanged(candidate, fresh):
                continue
            stage('write contextual reply', candidate['id'])
            if key in rejections:
                # Persist consumption before the model call. A crash or a repeated
                # wake cannot grant a second attempt under the same policy.
                rejections[key] = draft_format_hold(fresh, utcnow(), reply_writer.EDITORIAL_POLICY_VERSION)
                save_and_push(state)
            with budget_scope(state, save_and_push, utcnow):
                draft = reply_writer.generate(fresh, state, utcnow(), memory=memory, angle=angle)
            stage('refresh source before sending', candidate['id'])
            final = mentions.snapshot(await connection.get_tweet_by_id(candidate['id']), utcnow())
            if fresh.get('parent_context'):
                final = await mentions.thread_context(connection, final, utcnow())
            if not unchanged(fresh, final):
                continue
        except (AIRequestTimeout, AIRequestTooLarge) as exc:
            if isinstance(exc, AIRequestTooLarge):
                until = exc.until
                reason = f'request needs {exc.needed} estimated neurons, {exc.remaining} available this hour'
                # The source is included in both model calls. Prefer a shorter
                # complete source among the remaining eligible candidates.
                candidates.sort(key=lambda item: len(item['text'].encode('utf-8')))
            else:
                until = utcnow() + timedelta(minutes=30)
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
        except reply_writer.RecoverableDraftError as exc:
            rejections[key] = draft_format_hold(candidate, utcnow(), rejections.get(key, {}).get('retried_version'))
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
    stage('write original post')
    try:
        with budget_scope(state, save_and_push, utcnow):
            draft = personality.original(plan, memory_context, state, utcnow())
    except AIRequestTimeout:
        reject(state, slot, 'Cloudflare timed out preparing the original post. Deferred before sending.')
        return
    except AIRequestTooLarge as exc:
        reject(state, slot, f'Deferred original: request needs {exc.needed} estimated neurons, {exc.remaining} available this hour.')
        return
    await send_once(state, slot, draft, connection, user)


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


def inbox_shortlist(rows, own_id):
    """Give new callers and ongoing threads a seat each when distinct people are waiting."""
    def priority(row):
        text = row['text']
        tag = mentions.TAG.search(text)
        body = text[tag.end():].lstrip(' ,:—-') if tag else text
        asked = bool(mentions.ASK.match(body) or re.search('[?？؟]', text))
        return asked, timestamp(row['created_at']), int(row['id'])

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
    return (inbox_opportunity(row)['slot'] in state.get('decision_slots', {})
            or (row['id'], personality.invitation_version(row)) in personality.declined_invitations(state))


def record_silence(state, slot, now, plan):
    days = {now.date().isoformat(), (now.date() - timedelta(days=1)).isoformat()}
    decisions = {k: v for k, v in state.get('decision_slots', {}).items() if k[:10] in days}
    decisions[slot['slot']] = {'action': 'silent', 'at': now.isoformat()}
    for key in ('offered_invitations', 'summon_authors'):
        if plan.get(key):
            decisions[slot['slot']][key] = plan[key]
    state['decision_slots'] = decisions
    save_and_push(state)


async def decide(state, slot, connection, user, *, incoming=None):
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
    selected = inbox_shortlist([row for row in incoming if row['intent'] == 'invite'
                                and candidate_eligible(row, state, now, own_id)], own_id)
    invited = [row if row.get('parent_context') else await mentions.thread_context(connection, row, now)
               for row in selected]
    invited_authors = {row['author_id'] for row in invited}
    candidates = []
    # A topic search can inform an original. It can never supply a reply target.
    if 'original' in slot['allowed_actions']:
        stage('discover public conversations')
        try:
            candidates = await sources.discover(connection, state, now)
        except (NotFound, TweetNotAvailable):
            summary('Topic discovery unavailable for this opportunity.')
    # Never feed an incidental tag or opted-out request back as a trending post.
    passive = [c for c in candidates if c['author_id'] not in invited_authors and candidate_eligible(c, state, now, own_id)
               and not re.search(r'(?<![\w@])@' + re.escape(ACCOUNT) + r'\b', c['text'], re.I)]
    shortlist = invited + sources.shortlist(passive, now, limit=4 - len(invited))
    summary(f'Considering {len(invited)} direct invitations and {len(shortlist) - len(invited)} discovered conversations.')
    try:
        context = memory.context(shortlist)
    except MemoryAccessDenied:
        memory = ledger_after_denial(state['history'])
        context = memory.context(shortlist)
    stage('choose original, reply or silence')
    with budget_scope(state, save_and_push, utcnow):
        plan = personality.choose(shortlist, context, state, utcnow(), slot['allowed_actions'])
    action = plan['action']
    if action == 'silent':
        record_silence(state, slot, utcnow(), plan)
        summary('Qofeno chose silence for this opportunity.')
        return
    if action not in slot['allowed_actions']:
        raise DraftError('Decision exceeds the current publishing limits')
    before = len(state['history'])
    chosen_slot = {**slot, 'kind': action}
    if action == 'reply':
        # Search results may inform a standalone thought. Only incoming invitations
        # can be a reply destination, even if the planner returns a discovered ID.
        source = next((row for row in invited if row['id'] == plan['source_id']), None)
        if source is None:
            raise DraftError('Reply decision must name an eligible invitation')
        await publish_reply(state, chosen_slot, connection, user, selected=source, memory=context, angle=plan['angle'])
    else:
        await publish_original(state, chosen_slot, connection, user, plan, context)
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


async def process_inbox(state, connection):
    now = utcnow()
    if not reply_ready(schedule_state(state), now):
        return
    own_id = next((row['author_id'] for row in reversed(state['history'])
                   if row.get('verified_at') and row.get('author_id')), None)
    rows = await read_inbox(state, connection, now, own_id, poll=True)
    invitations = [row for row in rows if row['intent'] == 'invite' and candidate_eligible(row, state, now, own_id)]
    first = inbox_shortlist(invitations, own_id)
    user = None
    considered = 0
    for row in first + [row for row in invitations if row not in first]:
        now = utcnow()
        if considered >= INBOX_BATCH_SIZE or not reply_ready(schedule_state(state), now):
            break
        # Earlier decisions in this batch can use an alias, consume a thread
        # turn or leave an unresolved send. Recheck on the updated ledger.
        if not candidate_eligible(row, state, now, own_id):
            continue
        if row.get('parent_id') and not row.get('parent_context'):
            row = await mentions.thread_context(connection, row, now)
        slot = inbox_opportunity(row)
        if now >= timestamp(slot['expires_at']) or inbox_reviewed(state, row):
            continue
        attempt = state.get('last_attempts', {}).get(slot['slot'])
        if attempt and timestamp(attempt['at']) + RECHECK_GAP > now:
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
            reject(state, slot, f'Insufficient AI headroom for this invitation until {exc.until.isoformat()}.')
        except AIPaused as exc:
            summary(f'AI deferred until {exc.until.isoformat()}: {exc.reason}')
            break
        except DraftError as exc:
            record_silence(state, slot, now, {})
            summary(f'No approved decision for invitation {row["id"]}: {str(exc)[:240]}')


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
    if event == 'inbox':
        try:
            await process_inbox(state, connection)
        except TooManyRequests as exc:
            now = utcnow()
            reset = getattr(exc, 'rate_limit_reset', None)
            until = datetime.fromtimestamp(reset, timezone.utc) if reset and reset > now.timestamp() else now + timedelta(minutes=15)
            state['x_pause_until'] = until.isoformat()
            save_and_push(state)
            summary(f'X rate-limited inbox access. Next check after {until.isoformat()}.')
        return
    user = await posting_account(connection)
    window = None
    if event == 'repository_dispatch':
        window = start.replace(minute=start.minute // 30 * 30, second=0, microsecond=0) + timedelta(minutes=30)
    summary('Autonomous Qofeno decision. Workers AI model ' + selected_model())
    state = load()
    now = utcnow()
    hold = publication_hold(state, now)
    if hold:
        summary(hold)
        return
    scheduler = next_decision if event in ('push', 'repository_dispatch') else next_original
    found = scheduler(schedule_state(state), now, window)
    if not found:
        summary(upcoming_opportunity(state, now, scheduler))
        return
    slot, ready = found
    if ready > now:
        summary(f'Waiting until {ready.isoformat()} to begin research for a decision.')
    while (wait := (ready - utcnow()).total_seconds()) > 0:
        await asyncio.sleep(min(wait, 30))
    if utcnow() >= timestamp(slot['expires_at']):
        return
    try:
        await decide(state, slot, connection, user)
    except AIRequestTimeout:
        reject(state, slot, 'Workers AI timed out while preparing the decision. No new send.')
    except AIPaused as exc:
        reject(state, slot, f'AI deferred until {exc.until.isoformat()}: {exc.reason}')
    except DraftError as exc:
        reject(state, slot, f'No approved decision or wording: {str(exc)[:240]}')


async def inspect():
    state = load()
    now = utcnow()
    status = publication_hold(state, now) or upcoming_opportunity(state, now)
    inbox = 'open' if reply_ready(schedule_state(state), now) else 'held'
    summary(f'{status} Independent mention and thread-reply checks: {inbox}. '
            f'Confirmed publications: {len(state["history"])}. '
            f'Pending send: {bool(state.get("outbox"))}. No model or send calls.')
