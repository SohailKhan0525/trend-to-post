"""One source-grounded reply per 30 minutes, plus two own-profile prompts.

Every send is durably reserved first. Unknown outcomes block publication rather
than retrying. Native X permission and rate-limit failures are never bypassed.
"""
import asyncio
import html
import unicodedata
from datetime import timedelta

from twikit.errors import Forbidden, NotFound, TooManyRequests, TweetNotAvailable
from . import follow_writer, reply_writer, sources
from .ai_budget import AIPaused, active_pause, budget_scope
from .cloudflare import DraftError, selected_model
from .reply_schedule import next_slot, timestamp
from .store import ACCOUNT, SessionUnavailable, client, load, save_and_push, summary, utcnow

STAGE = {'name': 'startup', 'source_id': None}


def stage(name, source_id=None):
    STAGE.update(name=name, source_id=source_id)
    print(f'Stage: {name}' + (f' source={source_id}' if source_id else ''), flush=True)


def schedule_state(state):
    return {**state, 'history': state.get('editorial_history', []),
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
    if (str(getattr(post, 'id', '')) != pending['tweet_id']
            or str(getattr(user, 'id', '')) != pending['author_id']
            or str(getattr(user, 'screen_name', '')).casefold() != ACCOUNT
            or actual_parent != expected_parent
            or getattr(post, 'quoted_status_id', None) or getattr(post, 'quote', None)
            or legacy.get('is_quote_status') or getattr(post, 'retweeted_tweet', None)
            or copy_identity(text) != copy_identity(pending['text'])):
        state['pause_reason'] = 'Published post does not match the reserved author, text or reply parent'
        save_and_push(state)
        summary(state['pause_reason'])
        return False
    now = utcnow().isoformat()
    row = {**pending, 'posted_at': pending.get('sent_at', pending['reserved_at']), 'completed_at': now, 'verified_at': now}
    state['history'].append(row)
    state['editorial_history'].append({key: row[key] for key in ('slot', 'kind', 'completed_at', 'tweet_id')})
    state['last_posted_at'] = row['posted_at']
    state['outbox'] = None
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
    for candidate in candidates[:4]:
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
        except (sources.SourceError, NotFound, TweetNotAvailable):
            rejections[rejection_key(candidate)] = {'until': (utcnow() + timedelta(hours=2)).isoformat()}
            summary(f'Skipped unavailable or changed source {candidate["id"]}.')
            continue
        except DraftError as exc:
            rejections[rejection_key(candidate)] = {'until': (utcnow() + timedelta(hours=6)).isoformat()}
            summary(f'Source {candidate["id"]} did not pass reply review: {str(exc)[:240]}')
            continue
        return await send_once(state, slot, draft, connection, user, final)
    reject(state, slot, 'No fresh, distinct 10,000+ view source passed this check. No reply sent.')


async def publish_follow(state, slot, connection, user):
    stage('write own follow post')
    with budget_scope(state, save_and_push, utcnow):
        draft = follow_writer.generate(state, utcnow())
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
    stage('verify authenticated account')
    user = await connection.user()
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
