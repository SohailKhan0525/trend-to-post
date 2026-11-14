"""One source-grounded reply per 30 minutes, plus two own-profile prompts.

Every send is durably reserved first. Unknown outcomes block publication rather
than retrying. Native X permission and rate-limit failures are never bypassed.
"""
import asyncio
import html
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from twikit.errors import Forbidden, NotFound, TooManyRequests, TweetNotAvailable
from . import follow_writer, reply_writer, sources
from .ai_budget import AIPaused, AIRequestTooLarge, active_pause, budget_scope
from .cloudflare import AIRequestTimeout, DraftError, selected_model
from .reply_schedule import next_slot, timestamp
from .store import ACCOUNT, SessionUnavailable, client, load, save_and_push, summary, utcnow

STAGE = {'name': 'startup', 'source_id': None}


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
                              if key.startswith(now.date().isoformat())}
    state['last_attempts'][slot['slot']] = {'at': now.isoformat(), 'reason': reason}
    save_and_push(state)
    summary(reason)


def eligible_author(source, state, now):
    if source['author'].casefold() == ACCOUNT:
        return False
    return not any(row.get('kind') == 'reply'
                   and row.get('source', {}).get('author_id') == source['author_id']
                   and timestamp(row['posted_at']) > now - timedelta(hours=24)
                   for row in state['history'])


def unchanged(before, after):
    return all(before.get(key) == after.get(key) for key in
               ('id', 'author_id', 'author', 'text', 'created_at', 'urls', 'source_ids'))


def rejection_key(candidate):
    return reply_writer.fingerprint(candidate['text'] + candidate['id'])


def delivery_open(state):
    return not any(state.get(key) for key in ('pending', 'pending_reply', 'verification_pending',
                                               'pause_reason', 'follow_pending', 'outbox'))


async def verify_delivery(state, connection):
    pending = state.get('outbox')
    if not pending:
        return True
    if not pending.get('tweet_id'):
        summary('A send has an unknown outcome. It will not be resent; manual reconciliation is required.')
        return False
    stage('verify published post', pending.get('source', {}).get('id'))
    try:
        post = await connection.get_tweet_by_id(pending['tweet_id'])
    except Exception as exc:
        summary(f'Post ID recorded; readback is pending ({type(exc).__name__}). No resend.')
        return False
    user = getattr(post, 'user', None)
    text = getattr(post, 'full_text', None) or getattr(post, 'text', '')
    expected_parent = pending.get('source', {}).get('id') if pending['kind'] == 'reply' else None
    actual_parent = str(getattr(post, 'in_reply_to', '') or '') or None
    raw = getattr(post, '_data', {}) or {}
    legacy = raw.get('legacy', {}) or {}
    def copy_identity(value):
        return unicodedata.normalize('NFC', html.unescape(value))
    body = copy_identity(text)
    expected_body = copy_identity(pending['text'])
    # X may prepend the source author and other accounts tagged in the source.
    # Only remove a complete recipient prefix immediately before our exact text.
    if pending['kind'] == 'reply' and body != expected_body and body.endswith(' ' + expected_body):
        source = pending['source']
        parent = '@' + source['author'].casefold()
        prose = re.sub(r'https?://\S+', '', source['text'], flags=re.I)
        mentions = re.findall(r'(?<![\w/@])@[A-Za-z0-9_]{1,15}(?![A-Za-z0-9_])', prose)
        allowed = {parent, *(mention.casefold() for mention in mentions)}
        prefix = body[:-(len(expected_body) + 1)]
        recipients = prefix.lower().split(' ')
        if (prefix.isascii() and recipients[0] == parent and len(recipients) == len(set(recipients))
                and all(recipient in allowed for recipient in recipients)):
            body = expected_body
    if (str(getattr(post, 'id', '')) != pending['tweet_id']
            or str(getattr(user, 'id', '')) != pending['author_id']
            or str(getattr(user, 'screen_name', '')).casefold() != ACCOUNT
            or actual_parent != expected_parent
            or getattr(post, 'quoted_status_id', None) or getattr(post, 'quote', None)
            or legacy.get('is_quote_status') or getattr(post, 'retweeted_tweet', None)
            or body != expected_body):
        state['pause_reason'] = 'Published post does not match the reserved author, text or reply parent'
        save_and_push(state)
        summary(state['pause_reason'])
        summary('Public readback: ' + json.dumps({
            'id': str(getattr(post, 'id', '')), 'author_id': str(getattr(user, 'id', '')),
            'author': str(getattr(user, 'screen_name', '')), 'parent': actual_parent,
            'text': text, 'quote_id': getattr(post, 'quoted_status_id', None),
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
    due = next_slot(schedule_state(state), utcnow())
    if not due or due[0]['slot'] != slot['slot']:
        reject(state, slot, 'Opportunity changed during research; no send.')
        return
    if slot['kind'] == 'reply':
        if source is None:
            raise ValueError('A reply requires a verified source')
        sources.validate(source, utcnow())
        if (not eligible_author(source, state, utcnow())
                or source['author_id'] == str(user.id)
                or set(source['source_ids']) & set(state['used_source_ids'])
                or set(source['urls']) & set(state['used_urls'])):
            reject(state, slot, 'Source or author was already used; no send.')
            return
    elif source is not None:
        raise ValueError('An own follow post must not have a reply source')
    reserved = {**draft, 'slot': slot['slot'], 'kind': slot['kind'], 'author_id': str(user.id),
                'reserved_at': utcnow().isoformat()}
    if source:
        reserved['source'] = source
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
    except TooManyRequests:
        state.setdefault('delivery_rejections', []).append({**reserved, 'error': 'TooManyRequests'})
        state['outbox'] = None
        state['x_pause_until'] = (utcnow() + timedelta(minutes=30)).isoformat()
        reject(state, slot, 'X rate limit reached; publication paused for at least 30 minutes.')
        return
    # Other exceptions may have occurred after acceptance: retain the reservation.
    ident = str(getattr(post, 'id', ''))
    if not ident.isascii() or not ident.isdigit() or int(ident) <= 0:
        raise ValueError('Post response has no valid ID; reservation remains')
    state['outbox']['tweet_id'] = ident
    state['outbox']['sent_at'] = sent_at
    save_and_push(state)
    await verify_delivery(state, connection)


async def publish_reply(state, slot, connection, user):
    stage('discover reply sources')
    candidates = await sources.discover(connection, state, utcnow())
    now = utcnow()
    rejections = state['reply_rejections'] = {
        key: value for key, value in state.get('reply_rejections', {}).items()
        if timestamp(value['until']) > now}
    candidates = [candidate for candidate in candidates if eligible_author(candidate, state, now)
                  and rejection_key(candidate) not in rejections]
    deferred = 0
    for _ in range(min(4, len(candidates))):
        candidate = candidates.pop(0)
        if utcnow() >= timestamp(slot['expires_at']):
            reject(state, slot, 'Reply opportunity ended during research.')
            return
        try:
            stage('refresh source before writing', candidate['id'])
            fresh = sources.snapshot(await connection.get_tweet_by_id(candidate['id']), utcnow())
            if not unchanged(candidate, fresh):
                continue
            stage('write contextual reply', candidate['id'])
            with budget_scope(state, save_and_push, utcnow):
                draft = reply_writer.generate(fresh, state, utcnow())
            stage('refresh source before sending', candidate['id'])
            final = sources.snapshot(await connection.get_tweet_by_id(candidate['id']), utcnow())
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
            rejections[rejection_key(candidate)] = {'until': until.isoformat(), 'reason': reason}
            save_and_push(state)
            summary(f'Deferred source {candidate["id"]} until {until.isoformat()}: {reason}.')
            deferred += 1
            continue
        except (sources.SourceError, NotFound, TweetNotAvailable):
            rejections[rejection_key(candidate)] = {'until': (utcnow() + timedelta(hours=2)).isoformat()}
            summary(f'Skipped unavailable or changed source {candidate["id"]}.')
            continue
        except DraftError as exc:
            rejections[rejection_key(candidate)] = {'until': (utcnow() + timedelta(hours=6)).isoformat()}
            summary(f'Source {candidate["id"]} did not pass reply review: {str(exc)[:240]}')
            continue
        return await send_once(state, slot, draft, connection, user, final)
    if deferred:
        reject(state, slot, f'No reply sent in this check. Deferred {deferred} source(s) for AI availability or budget.')
    else:
        reject(state, slot, 'No fresh, distinct 10,000+ view source passed this check. No reply sent.')


async def publish_follow(state, slot, connection, user):
    stage('write own follow post')
    try:
        with budget_scope(state, save_and_push, utcnow):
            draft = follow_writer.generate(state, utcnow())
    except AIRequestTimeout:
        reject(state, slot, 'Cloudflare timed out preparing the follow post. Deferred before sending.')
        return
    except AIRequestTooLarge as exc:
        reject(state, slot, f'Deferred follow post: request needs {exc.needed} estimated neurons, {exc.remaining} available this hour.')
        return
    await send_once(state, slot, draft, connection, user)


async def run(event):
    start = utcnow()
    state = load()
    connection = client()
    if not await verify_delivery(state, connection):
        return
    if not delivery_open(state):
        summary('A previous publication requires reconciliation. No new send.')
        return
    stage('resolve configured posting account')
    # Public profile metadata identifies the configured target, not the logged-in
    # session. Native CreateTweet authenticates the existing user-provided secrets;
    # readback must confirm its actual author before any later publication.
    # Do not make posting depend on the unsupported v1.1 settings endpoint.
    user = await connection.get_user_by_screen_name(ACCOUNT)
    if str(getattr(user, 'screen_name', '')).casefold() != ACCOUNT:
        raise SessionUnavailable()
    window = None
    if event == 'repository_dispatch':
        window = start.replace(minute=start.minute // 30 * 30, second=0, microsecond=0) + timedelta(minutes=30)
    summary('Mode: at most one contextual reply per 30 minutes + two own follow posts/day. Workers AI: ' + selected_model())
    for _ in range(3 if window else 1):
        state = load()
        if not delivery_open(state):
            return
        if state.get('x_pause_until') and timestamp(state['x_pause_until']) > utcnow():
            summary('X publication rate-limit cooldown is active.')
            return
        paused = active_pause(state, utcnow())
        if paused:
            summary(f'AI paused until {paused.until.isoformat()}: {paused.reason}')
            return
        found = next_slot(schedule_state(state), utcnow(), window)
        if not found:
            summary('No eligible reply or own-post opportunity in this wake.')
            return
        slot, ready = found
        while (wait := (ready - utcnow()).total_seconds()) > 0:
            await asyncio.sleep(min(wait, 30))
        if utcnow() >= timestamp(slot['expires_at']):
            continue
        try:
            if slot['kind'] == 'reply':
                await publish_reply(state, slot, connection, user)
            else:
                await publish_follow(state, slot, connection, user)
        except AIPaused as exc:
            reject(state, slot, f'AI paused until {exc.until.isoformat()}: {exc.reason}')
            return
        except DraftError as exc:
            reject(state, slot, f'No approved wording: {str(exc)[:240]}')


async def inspect():
    state = load()
    found = next_slot(schedule_state(state), utcnow())
    summary(f'Next due opportunity: {found[0] if found else "none"}. '
            f'Confirmed publications: {len(state["history"])}. '
            f'Pending send: {bool(state.get("outbox"))}. No model or send calls.')
