"""Public invitations and opt-outs directed to Qofeno, read through Twikit."""
import re

from twikit.errors import NotFound, TweetNotAvailable

from . import sources
from .reply_writer import INJECTION
from .store import ACCOUNT


TOKEN = r'@' + re.escape(ACCOUNT) + r'(?![A-Za-z0-9_])'
GREETING = r'(?:hey|hi|hello|howdy|yo|hola|namaste|नमस्ते|你好|こんにちは)'
LEADING = re.compile(r'^\s*(?:(' + GREETING + r')[\s,!:.—-]+)?' + TOKEN, re.I)
TRAILING = re.compile(r'(?:^|[\s,])' + TOKEN + r'[\s?!.,:]*$', re.I)
CONTROL = re.compile(r"(?:please\s+)?(stop(?:\s+(?:replying|responding)(?:\s+to\s+(?:me|my posts))?)?|"
                     r"(?:do\s+not|don['’]?t)\s+(?:reply|respond)(?:\s+to\s+me)?|opt[ -]?out|"
                     r"opt me out|no more replies|leave me alone|unsubscribe|resume(?:\s+replies)?|"
                     r"opt[ -]?in|start\s+replying)(?:\s+please)?[.!]*", re.I)
ASK = re.compile(r"^(?:please\b|(?:can|could|would|will|do|did|are|should)\s+you\b|"
                 r"(?:what|why|when|where|how|which|who)\b|"
                 r"(?:tell|help|give|show)\s+(?:me|us)\b|(?:explain|recommend|suggest|reply|answer|roast)\b|thoughts on\b)", re.I)
DIRECT_ENDING = re.compile(r"^(?:thoughts(?: on\b.*)?|any thoughts|opinions)[?!.,\s]*$|"
                           r"\b(?:what(?:['’]s| is) your (?:take|view)|what do you (?:think|make of|recommend)|"
                           r"your thoughts|can you|could you|would you|please (?:answer|reply)|tell me|help me)\b", re.I)
INBOX_COUNT = 40
INBOX_REFRESH_LIMIT = 6


def _intent(text, parent_author):
    # Ignore mentions inside URLs and email addresses. An inbox item or tag on
    # its own is not evidence the author is addressing the account.
    prose = sources._URL.sub('', text).strip()
    leading = LEADING.match(prose)
    trailing = TRAILING.search(prose)
    if leading:
        body = prose[leading.end():].strip(' \n\t,:—-')
        greeting = bool(leading.group(1))
    elif parent_author == ACCOUNT:
        body = prose
        greeting = False
    elif trailing:
        body = prose[:trailing.start()].strip(' ,')
        greeting = False
    else:
        return None
    command = CONTROL.fullmatch(body)
    if command:
        return 'resume' if command.group(1).lower().startswith(('resume', 'opt in', 'opt-in', 'optin', 'start')) else 'stop'
    if re.fullmatch(GREETING + r'(?:\s+there)?[!.,?👋\s]*', body, re.I) or (greeting and not body.strip('!.?👋 ')):
        return 'invite'
    if not any(c.isalpha() for c in body) or body.startswith('@'):
        return None
    if trailing and not leading and parent_author != ACCOUNT:
        return 'invite' if DIRECT_ENDING.search(body) else None
    if ASK.search(body) or any(mark in body for mark in '?？؟'):
        return 'invite'
    return None


def validate(candidate, now):
    if not isinstance(candidate, dict) or candidate.get('origin') != 'mention':
        raise sources.SourceError('missing mention origin')
    row = sources.validate_fields(candidate, now, require_reach=False, minimum_letters=1)
    if row['author'].casefold() == ACCOUNT or INJECTION.search(row['text']):
        raise sources.SourceError('own mention or unsafe instruction')
    parent_id = row.get('parent_id')
    if parent_id is not None:
        parent_id = sources._identifier(parent_id)
    parent_author = row.get('parent_author')
    if parent_author is not None:
        if not parent_id or not isinstance(parent_author, str) or not sources._HANDLE.fullmatch(parent_author):
            raise sources.SourceError('invalid mention parent')
        parent_author = parent_author.casefold()
    intent = _intent(row['text'], parent_author)
    if intent is None or ('intent' in row and row['intent'] != intent):
        raise sources.SourceError('no matching invitation or preference command')
    return {**row, 'parent_id': parent_id, 'parent_author': parent_author, 'intent': intent}


def snapshot(tweet, now):
    return sources.checked_snapshot(tweet, now, include_parent=True, validator=validate)


def _parent_fields(candidate, now):
    row = sources.validate_fields(candidate, now, require_reach=False, minimum_letters=4)
    if INJECTION.search(row['text']) or len(row['text']) > 3000:
        raise sources.SourceError('parent is unsuitable for conversation context')
    return row


async def thread_context(connection, candidate, now):
    """Read only the native parent of a validated invitation; never follow an arbitrary URL."""
    row = validate(candidate, now)
    row.pop('parent_context', None)
    if row['intent'] != 'invite' or not row['parent_id']:
        return row
    try:
        tweet = await connection.get_tweet_by_id(row['parent_id'])
        parent = sources.checked_snapshot(tweet, now, include_parent=True, validator=_parent_fields)
    except (NotFound, TweetNotAvailable, sources.SourceError):
        return row
    if parent['id'] == row['parent_id'] and (not row['parent_author'] or
                                              row['parent_author'] == parent['author'].casefold()):
        row['parent_context'] = {key: parent[key] for key in ('id', 'author', 'text')}
    return row


async def discover(connection, now, *, used_source_ids=()):
    """Read one native mentions page. Search by exact handle on a missing endpoint."""
    try:
        notices = await connection.get_notifications('Mentions', count=INBOX_COUNT)
    except (NotFound, TweetNotAvailable):
        since = (now - sources.MAX_AGE).date().isoformat()
        tweets = await connection.search_tweet(f'@{ACCOUNT} since:{since} -filter:retweets', 'Latest', count=INBOX_COUNT)
        native = False
    else:
        tweets = [notice.tweet for notice in notices if getattr(notice, 'tweet', None) is not None]
        native = True
    rows = {}
    spent = set(used_source_ids)
    for tweet in tweets:
        try:
            row = snapshot(tweet, now)
        except sources.SourceError:
            continue
        if row['intent'] != 'invite' or not spent.intersection(row['source_ids']):
            rows[row['id']] = row
    result = sorted(rows.values(), key=_order, reverse=True)
    if native:
        # v1.1's notification wrappers omit some edit and user privacy fields.
        # Pull public TweetDetail before passing an invitation to any model.
        invitations = [r for r in result if r['intent'] == 'invite'][:INBOX_REFRESH_LIMIT]
        result = [r for r in result if r['intent'] != 'invite']
        for candidate in invitations:
            try:
                fresh = snapshot(await connection.get_tweet_by_id(candidate['id']), now)
            except (NotFound, TweetNotAvailable, sources.SourceError):
                continue
            if fresh['author_id'] == candidate['author_id'] and candidate['id'] in fresh['source_ids']:
                result.append(fresh)
        result.sort(key=_order, reverse=True)
    print(f'Mention inbox: examined={len(tweets)} invitations={sum(row["intent"] == "invite" for row in result)} '
          f'preference commands={sum(row["intent"] != "invite" for row in result)}.', flush=True)
    return result


def _order(row):
    return (sources._utc(row['created_at'], 'invalid mention timestamp'), int(row['id']))


def apply_controls(rows, state):
    """Honor the latest explicit author command, even if an older page is replayed."""
    changed = False
    for row in rows:
        if row['intent'] not in ('stop', 'resume'):
            continue
        preferences = state.setdefault('mention_preferences', {})
        previous = preferences.get(row['author_id'])
        if previous and _order(previous) >= _order(row):
            continue
        preferences[row['author_id']] = {key: row[key] for key in ('intent', 'id', 'created_at')}
        changed = True
    return changed


def opted_out(state, author_id):
    preference = state.get('mention_preferences', {}).get(str(author_id))
    return bool(preference and preference['intent'] == 'stop')


def invited(state, row):
    preference = state.get('mention_preferences', {}).get(row['author_id'])
    return (row['intent'] == 'invite' and not opted_out(state, row['author_id'])
            and (not preference or _order(row) > _order(preference)))
