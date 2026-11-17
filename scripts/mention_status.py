"""Inspect the live X inbox and D1 ledger without posting or changing preferences."""
import asyncio
from collections import Counter
import html
import os
import re
import unicodedata

from twikit.errors import NotFound, TweetNotAvailable

from app import editorial, mentions, sources, store
from app.reply_freshness import INBOX_START_AT, MAX_REPLY_AGE, invitation_hold
from app.reply_schedule import DAILY_REPLY_CAP, timestamp
from scripts.azure_status import read_snapshot, report


def eligibility(row, state, now, own_id):
    if row['intent'] != 'invite':
        return 'preference command'
    if set(row['source_ids']).intersection(state['used_source_ids']):
        return 'previously reserved source'
    hold = invitation_hold(row, now)
    if hold:
        return 'before reply cutoff' if hold == 'source predates reply activation' else hold
    if set(row['urls']).intersection(state['used_urls']):
        return 'previously used link'
    if mentions.opted_out(state, row['author_id']):
        return 'author opted out'
    if not editorial.eligible_author(row, state, now, own_id):
        count = editorial.thread_reply_count(row, state['history'], state.get('observed_recipient_replies'))
        if count >= editorial.THREAD_REPLY_LIMIT:
            return f'this person reached {editorial.THREAD_REPLY_LIMIT} replies in this conversation'
        return 'conversation ineligible'
    if editorial.inbox_reviewed(state, row):
        return 'already reviewed'
    deferred = state.get('reply_rejections', {}).get(editorial.rejection_key(row))
    if editorial.reply_rejection_active(row, deferred, now):
        return 'source temporarily deferred'
    if deferred and timestamp(deferred['until']) > now:
        return 'eligible for review (bounded retry)'
    return 'eligible for review'


def previously_handled(post_id, state):
    def matches(record):
        source = (record or {}).get('source') or {}
        return source.get('id') == post_id or post_id in source.get('source_ids', ())

    if any(row.get('verified_at') and matches(row) for row in state['history']):
        return 'reply verified on X'
    if matches(state.get('outbox')):
        return 'delivery pending verification'
    if any(matches(row) for row in state.get('delivery_rejections', ())):
        return 'previous delivery rejected or unconfirmed'
    if post_id in state['used_source_ids']:
        return 'previously reserved source'
    return None


async def inspect_reserved_reply(connection, pending):
    if pending.get('kind') != 'reply' or not re.fullmatch(r'[1-9][0-9]{0,19}', str(pending.get('tweet_id', ''))):
        return
    try:
        post = await connection.get_tweet_by_id(pending['tweet_id'])
    except (NotFound, TweetNotAvailable):
        store.summary('Reserved X reply is unavailable for readback.')
        return
    body = getattr(post, 'full_text', None) or getattr(post, 'text', '')
    native = (getattr(post, '_data', {}) or {}).get('legacy') or {}

    def identity(text):
        return unicodedata.normalize('NFC', html.unescape(text))

    visible = mentions._reply_text(body, native)
    exact_author = str(getattr(getattr(post, 'user', None), 'id', '')) == pending.get('author_id')
    exact_parent = str(getattr(post, 'in_reply_to', '') or '') == pending['source']['id']
    wanted = identity(pending['text'])
    store.summary(f'Reserved X reply readback: ID matches={str(post.id) == pending["tweet_id"]}, '
                  f'author matches={exact_author}, parent matches={exact_parent}, '
                  f'full text matches={identity(body) == wanted}, visible text matches={identity(visible) == wanted}, '
                  f'native text range present={"display_text_range" in native}, '
                  f'reservation verified={editorial.matches_publication(pending, post)}.')


async def inspect_inbox(connection, state, now, post_id):
    user = await connection.get_user_by_screen_name(store.ACCOUNT)
    own_id = str(user.id)
    if not own_id.isascii() or not own_id.isdigit() or user.screen_name.casefold() != store.ACCOUNT:
        raise store.SessionUnavailable()
    await inspect_reserved_reply(connection, state.get('outbox') or {})
    rows = await mentions.discover(connection, now, used_source_ids=(), own_history=state['history'], own_user_id=own_id)
    # Evaluate commands exactly as the publisher would, entirely in this local copy.
    state = {**state, 'mention_preferences': dict(state.get('mention_preferences', {}))}
    mentions.apply_controls(rows, state)
    counts = Counter(eligibility(row, state, now, own_id) for row in rows)
    eligible = counts['eligible for review'] + counts['eligible for review (bounded retry)']
    store.summary(f'Current inbox sample: {len(rows)} matched, {eligible} eligible for review, '
                  f'{counts["previously reserved source"]} previously reserved.')
    for category, count in sorted(counts.items()):
        store.summary(f'Inbox classification: {category} = {count}')
    if post_id:
        result = previously_handled(post_id, state)
        if not result:
            visible = next((r for r in rows if post_id in r['source_ids']), None)
            if visible:
                result = 'present in inbox sample; ' + eligibility(visible, state, now, own_id)
            else:
                try:
                    post = await connection.get_tweet_by_id(post_id)
                    row = mentions.snapshot(post, now)
                    if post_id not in row['source_ids']:
                        raise sources.SourceError('unmatched identity')
                    row = await mentions.thread_context(connection, row, now)
                    result = 'absent from inbox sample; direct lookup: ' + eligibility(row, state, now, own_id)
                except (NotFound, TweetNotAvailable):
                    result = 'absent from inbox sample; X did not return the post'
                except sources.SourceError as exc:
                    checks = {
                        'no matching invitation or preference command': 'no direct invitation in the public text',
                        'source is outside the 24-hour freshness window': 'older than the inbox window',
                        'source contains excluded topics': 'topic excluded',
                        'source author is unavailable or protected': 'author unavailable or protected',
                        'source is a repost': 'repost',
                        'source is a quote': 'quote',
                    }
                    result = 'absent from inbox sample; ' + checks.get(str(exc), 'post failed freshness, visibility or invitation checks')
        store.summary(f'Requested mention: {result}.')


async def main():
    post_id = os.environ.get('MENTION_POST_ID', '').strip()
    if post_id and not re.fullmatch(r'[1-9][0-9]{0,19}', post_id):
        raise ValueError('Expected a numeric X post ID.')
    active, state = read_snapshot()
    if state is None:
        raise RuntimeError('D1 has no active ledger.')
    now = store.utcnow()
    earliest = max(INBOX_START_AT, now - MAX_REPLY_AGE)
    store.summary(f'Live mention freshness check at {now.isoformat()}. Earliest eligible incoming post: {earliest.isoformat()}.')
    replies = sum(row.get('kind') == 'reply' and timestamp(row['posted_at']).date() == now.date()
                  for row in state['history'] if row.get('verified_at') and row.get('posted_at'))
    store.summary(f'Verified replies this UTC day: {replies}/{DAILY_REPLY_CAP}.')
    for line in report(state, now):
        store.summary(line)
    if state.get('x_pause_until') and timestamp(state['x_pause_until']) > now:
        store.summary('Inbox read deferred for the recorded X cooldown.')
        if post_id:
            store.summary('Requested mention: ' + (previously_handled(post_id, state) or 'unknown during cooldown') + '.')
    else:
        connection = store.client()
        try:
            await inspect_inbox(connection, state, now, post_id)
        finally:
            await connection.http.aclose()
            if connection._curl_session is not None:
                await connection._curl_session.close()
    return 0 if active else 1


if __name__ == '__main__':
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f'Mention diagnosis unavailable ({type(exc).__name__}).')
        raise SystemExit(1) from None
