"""Public invitations and opt-outs directed to Qofeno, read through Twikit."""
from datetime import timedelta
from itertools import islice
import re

from httpx import NetworkError, TimeoutException
from twikit.errors import NotFound, RequestTimeout, ServerError, TweetNotAvailable

from . import sources
from .reply_writer import INJECTION
from .store import ACCOUNT


TOKEN = r'(?<![\w@])@' + re.escape(ACCOUNT) + r'(?![A-Za-z0-9_])'
GREETING = r'(?:hey|hi|hello|howdy|yo|hola|namaste|नमस्ते|你好|こんにちは)'
LEADING = re.compile(r'^\s*(?:(' + GREETING + r')[\s,!:.—-]+)?' + TOKEN, re.I)
TRAILING = re.compile(r'(?:^|[\s,])' + TOKEN + r'[\s?!.,:]*$', re.I)
TAG = re.compile(TOKEN, re.I)
PING = re.compile(TOKEN + r"(?:[\s.,!?！？。、؟]|👋[🏻-🏿]?|\ufe0f)*", re.I)
REFERENCE_PREFIX = re.compile(r"\b(?:cc|via|from|about|at|by|follow|mention(?:ed)?|tag(?:ged)?|"
                              r"credits? to|thanks to|shoutout to|check out)\s*[:,-]?\s*$", re.I)
QUOTED = re.compile(r'“[^”]*(?:”|$)|"[^"]*(?:"|$)|「[^」]*(?:」|$)|(?<!\w)‘[^’]*(?:’|$)|^\s*>.*$', re.M)
HYPOTHETICAL = re.compile(r"\b(?:if|imagine|suppose|hypothetically)\b[^.!?]*$", re.I)
THIRD_PERSON = re.compile(r"^(?:['’]s\b|(?:just\s+)?(?:said|says|wrote|writes|posted|posts|published|"
                          r"retweeted|likes|works|has|was)\b|is\s+(?!(?:this|that|there|it|anyone|someone)\b))", re.I)
ACCOUNT_TAG = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{1,15})(?![A-Za-z0-9_])", re.I)
INVITE_NAMED = re.compile(r"(?:^|[.!?]\s+)(?:(?:let['’]?s hear from|(?:i|we) (?:want|would like) to hear from)"
                          r"\s+" + TOKEN + r"(?=\s|[.!?,]|$)|(?:i|we) (?:want|would like) to (?:see|hear) what "
                          + TOKEN + r"\s+(?:thinks?|says?|has to say)\b)", re.I)
INTRODUCED_AI = re.compile(r"\b(?:an? )?(?:ai|bot|agent|assistant)(?: (?:called|named))?\s+" + TOKEN, re.I)
INVITE_RESPONSE = re.compile(r"(?:^|[.!?]\s+)(?:i|we) (?:want|would like) to (?:see|hear) what "
                             r"(?:it|he|she|they) (?:says?|thinks?|has to say)\b", re.I)
CONTROL = re.compile(r"(?:please\s+)?(stop(?:\s+(?:replying|responding)(?:\s+to\s+(?:me|my posts))?)?|"
                     r"(?:do\s+not|don['’]?t)\s+(?:reply|respond)(?:\s+to\s+me)?|opt[ -]?out|"
                     r"opt me out|no more replies|leave me alone|unsubscribe|resume(?:\s+replies)?|"
                     r"opt[ -]?in|start\s+replying)(?:\s+please)?[.!]*", re.I)
ASK = re.compile(r"^(?:please\b|(?:can|could|would|will|do|did|are|should)\s+you\b|"
                 r"(?:what|why|when|where|how|which|who)\b|"
                 r"(?:tell|help|give|show)\s+(?:me|us)\b|(?:explain|recommend|suggest|reply|answer|roast)\b|thoughts on\b|any (?:ideas|thoughts|tips)\b)", re.I)
DIRECT_ENDING = re.compile(r"^(?:thoughts(?: on\b.*)?|any thoughts|opinions)[?!.,\s]*$|"
                           r"\b(?:what(?:['’]s| is) your (?:take|view)|what do you (?:think|make of|recommend)|"
                           r"your thoughts|can you|could you|would you|please (?:answer|reply)|tell me|help me)\b", re.I)
INBOX_COUNT = 40
INBOX_REFRESH_LIMIT = 6
# These failures can be isolated to a single optional read. Authentication,
# authorization and rate-limit exceptions must reach the account-level governor.
READ_UNAVAILABLE = (NotFound, TweetNotAvailable, RequestTimeout, ServerError, NetworkError,
                    TimeoutException, KeyError, IndexError)
DETAIL_UNAVAILABLE = (*READ_UNAVAILABLE, sources.SourceError)



def is_direct_ping(text):
    return isinstance(text, str) and PING.fullmatch(text.strip()) is not None


def _intent(text, parent_author=None, parent_id=None):
    if is_direct_ping(text):
        return 'invite'
    prose = QUOTED.sub(' ', sources._URL.sub('', text)).strip()
    # An introduction can expressly give the named AI the floor. A generic
    # credit, somebody else's quoted invitation, or a pronoun with multiple
    # tagged accounts is insufficient to establish whom the author invited.
    tags = [tag.casefold() for tag in ACCOUNT_TAG.findall(prose)]
    if len(tags) > 2:
        return None
    introduced = INTRODUCED_AI.search(prose)
    author_introduced = introduced and not HYPOTHETICAL.search(prose[:introduced.start()])
    if set(tags) == {ACCOUNT} and (INVITE_NAMED.search(prose) or
                                   (author_introduced and INVITE_RESPONSE.search(prose, introduced.end()))):
        return 'invite'
    leading = LEADING.match(prose)
    trailing = TRAILING.search(prose)
    own_thread = bool(parent_id and parent_author in (None, ACCOUNT))
    if leading:
        body = prose[leading.end():].strip(' \n\t,:—-')
        if leading.group(1) and not body.strip('!.?👋 '):
            return 'invite'
        if THIRD_PERSON.match(body) and not ASK.match(body):
            return None
    elif trailing:
        body = prose[:trailing.start()].strip(' ,')
        if REFERENCE_PREFIX.search(body):
            return None
    else:
        tag = TAG.search(prose)
        if tag:
            before, after = prose[:tag.start()].rstrip(), prose[tag.end():]
            if REFERENCE_PREFIX.search(before):
                return None
            body = after.lstrip(' \n\t,:—-')
            vocative = (before.endswith((',', '.', '!', '?', '。', '！', '？'))
                        or re.search(r'\b(?:hey|hi|hello|yo|ok|okay)$', before, re.I))
            if not (vocative or ASK.match(body)) or THIRD_PERSON.match(body):
                return None
        elif own_thread:
            body = prose
        else:
            return None
    command = CONTROL.fullmatch(body)
    if command:
        return 'resume' if command.group(1).lower().startswith(('resume', 'opt in', 'opt-in', 'optin', 'start')) else 'stop'
    if re.fullmatch(GREETING + r'(?:\s+there)?[!.,?👋\s]*', body, re.I):
        return 'invite'
    if not any(c.isalpha() for c in body) or body.startswith('@'):
        return None
    if own_thread:
        # A response to our actual post can be a statement, a correction or a joke.
        # The parent must be independently verified before a tagless reply is eligible.
        return 'invite'
    if trailing and not leading:
        return 'invite' if DIRECT_ENDING.search(body) else None
    return 'invite'


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
    parent_user_id = row.get('parent_user_id')
    conversation_id = row.get('conversation_id')
    if parent_user_id is not None:
        parent_user_id = sources._identifier(parent_user_id)
        if not parent_id:
            raise sources.SourceError('missing native reply parent')
    if conversation_id is not None:
        conversation_id = sources._identifier(conversation_id)
    intent = _intent(row['text'], parent_author, parent_id)
    if intent is None or ('intent' in row and row['intent'] != intent):
        raise sources.SourceError('no matching invitation or preference command')
    return {**row, 'parent_id': parent_id, 'parent_author': parent_author, 'parent_user_id': parent_user_id,
            'conversation_id': conversation_id, 'intent': intent}


def snapshot(tweet, now):
    def child_fields(fields, at):
        raw = tweet._data
        native = raw.get('legacy') or {}
        fields['parent_user_id'] = native.get('in_reply_to_user_id_str') or raw.get('in_reply_to_user_id_str')
        fields['conversation_id'] = native.get('conversation_id_str') or raw.get('conversation_id_str')
        return validate(fields, at)

    return sources.checked_snapshot(tweet, now, include_parent=True, validator=child_fields)


def _parent_fields(candidate, now):
    if candidate.get('author', '').casefold() == ACCOUNT:
        # People can add a new reply to an older Qofeno post. Its timestamp must
        # still be valid and in the past. The child's 24-hour window is unchanged.
        created = sources._utc(candidate['created_at'], 'invalid parent timestamp')
        now = min(now, created)
    row = sources.validate_fields(candidate, now, require_reach=False, minimum_letters=1)
    if INJECTION.search(row['text']) or len(row['text']) > 3000:
        raise sources.SourceError('parent is unsuitable for conversation context')
    return row


def _requires_own_parent(row):
    if not row.get('parent_id'):
        return False
    directed = _intent(row['text']) == row['intent']
    # Honor explicitly addressed opt-outs even if the surrounding post was deleted.
    if row['intent'] != 'invite':
        return not directed
    return not directed or row.get('parent_author') == ACCOUNT


def is_own_reply(row, own_user_id=None):
    parent = row.get('parent_context') or {}
    return (bool(row.get('parent_id')) and row.get('id') != row['parent_id'] and parent.get('id') == row['parent_id']
            and str(parent.get('author', '')).casefold() == ACCOUNT
            and str(parent.get('author_id', '')).isdigit() and row.get('author_id') != parent['author_id']
            and (own_user_id is None or parent['author_id'] == str(own_user_id)))


def _attach_parent(child, parent):
    if (child['parent_id'] == parent['id'] and child['parent_author'] in (None, parent['author'].casefold())
            and child['parent_user_id'] in (None, parent['author_id'])):
        context = {key: parent[key] for key in ('id', 'author', 'text')}
        if parent['author'].casefold() == ACCOUNT:
            context['author_id'] = parent['author_id']
        child['parent_context'] = context
    return child


async def thread_context(connection, candidate, now):
    """Refresh the exact native parent. Discard previous proof if the parent disappears."""
    row = validate(candidate, now)
    row.pop('parent_context', None)
    if not row['parent_id'] or (row['intent'] != 'invite' and not _requires_own_parent(row)):
        return row
    try:
        tweet = await connection.get_tweet_by_id(row['parent_id'])
        parent = sources.checked_snapshot(tweet, now, include_parent=True, validator=_parent_fields)
    except DETAIL_UNAVAILABLE:
        return row
    return _attach_parent(row, parent)


def _recent_parents(history, now):
    # Keep one recent original and one recent reply in view, even on a day with
    # far more outgoing replies. Rotate through a small pool of recent parents.
    groups = [[], []]
    for row in reversed(history):
        if not row.get('verified_at') or row.get('kind') not in ('original', 'reply', 'follow'):
            continue
        try:
            age = now - sources._utc(row.get('posted_at'), 'invalid parent date')
            ident = sources._identifier(row.get('tweet_id'))
        except sources.SourceError:
            continue
        group = groups[1 if row['kind'] == 'reply' else 0]
        if timedelta(0) <= age <= timedelta(days=7) and len(group) < 4 and ident not in group:
            group.append(ident)
        if all(len(part) == 4 for part in groups):
            break
    tick = int(now.timestamp()) // 1800
    selected = [g[tick % len(g)] for g in groups if g]
    if len(selected) == 1:
        group = next(g for g in groups if g)
        if len(group) > 1:
            selected.append(group[(tick + 1) % len(group)])
    return selected


async def _own_children(connection, history, now, own_user_id, spent):
    children = []
    for ident in _recent_parents(history, now):
        try:
            tweet = await connection.get_tweet_by_id(ident)
            parent = sources.checked_snapshot(tweet, now, include_parent=True, validator=_parent_fields)
        except DETAIL_UNAVAILABLE:
            continue
        if parent['id'] != ident or parent['author'].casefold() != ACCOUNT:
            continue
        if own_user_id is not None and parent['author_id'] != str(own_user_id):
            continue
        try:
            replies = list(islice(getattr(tweet, 'replies', None) or (), INBOX_COUNT))
        except (*READ_UNAVAILABLE, TypeError):
            continue
        for reply in replies:
            try:
                child = _attach_parent(snapshot(reply, now), parent)
            except sources.SourceError:
                continue
            if is_own_reply(child, own_user_id) and (child['intent'] != 'invite' or not spent.intersection(child['source_ids'])):
                children.append(child)
    return children


async def discover(connection, now, *, used_source_ids=(), own_history=(), own_user_id=None):
    """Merge one native page and one Latest search; either can omit a direct invitation."""
    since = (now - sources.MAX_AGE).date().isoformat()
    query = f'(@{ACCOUNT} OR to:{ACCOUNT}) since:{since} -filter:retweets'
    # Endpoint unavailability and partial Twifork response maps are local to
    # each read. Authentication, account locks and rate limits still propagate.
    try:
        notices = await connection.get_notifications('Mentions', count=INBOX_COUNT)
        native = [n.tweet for n in islice(notices, INBOX_COUNT) if getattr(n, 'tweet', None) is not None]
        native_failed = False
    except READ_UNAVAILABLE as exc:
        print(f'Mention notifications unavailable ({type(exc).__name__}).', flush=True)
        native, native_failed = [], True
    try:
        found = list(islice(await connection.search_tweet(query, 'Latest', count=INBOX_COUNT), INBOX_COUNT))
    except READ_UNAVAILABLE as exc:
        if native_failed and not _recent_parents(own_history, now):
            raise
        print(f'Mention search unavailable ({type(exc).__name__}).', flush=True)
        found = []

    spent = set(used_source_ids)

    def candidates(tweets):
        rows = {}
        for tweet in tweets:
            try:
                row = snapshot(tweet, now)
            except sources.SourceError:
                continue
            if own_user_id is not None and row['author_id'] == str(own_user_id):
                continue
            if row['intent'] != 'invite' or not spent.intersection(row['source_ids']):
                rows[row['id']] = row
        return sorted(rows.values(), key=_order, reverse=True)

    result = candidates(found)
    search_ids = {alias for row in result for alias in row['source_ids']}
    native_only = [row for row in candidates(native) if not search_ids.intersection(row['source_ids'])]
    result.extend(row for row in native_only if row['intent'] != 'invite')
    invitations = [row for row in native_only if row['intent'] == 'invite']
    # Reserve a hydration slot for each route. A busy thread should not hide
    # a standalone ping, and a busy mention page should not hide a response.
    first_mention = next((row for row in invitations if not row['parent_id']), None)
    first_thread = next((row for row in invitations if row['parent_id'] and
                         row['parent_author'] in (None, ACCOUNT)), None)
    priority = [row for row in (first_mention, first_thread) if row]
    priority.extend(row for row in invitations if row not in (first_mention, first_thread))
    for candidate in priority[:INBOX_REFRESH_LIMIT]:
        # v1.1 notification wrappers omit some edit and user privacy fields.
        # GraphQL search already supplies these for overlapping sources.
        try:
            fresh = snapshot(await connection.get_tweet_by_id(candidate['id']), now)
        except DETAIL_UNAVAILABLE:
            continue
        if (fresh['author_id'] == candidate['author_id'] and candidate['id'] in fresh['source_ids']
                and not spent.intersection(fresh['source_ids'])):
            result.append(fresh)
    verified = {}
    for row in result:
        if _requires_own_parent(row):
            row = await thread_context(connection, row, now)
            if not is_own_reply(row, own_user_id):
                continue
        verified[row['id']] = row
    for row in await _own_children(connection, own_history, now, own_user_id, spent):
        verified[row['id']] = row
    result = []
    seen = set()
    for row in sorted(verified.values(), key=_order, reverse=True):
        if not seen.intersection(row['source_ids']):
            result.append(row)
            seen.update(row['source_ids'])
    print(f'Mention inbox: examined={len(native) + len(found)} notifications={len(native)} latest={len(found)} '
          f'invitations={sum(row["intent"] == "invite" for row in result)} '
          f'preference commands={sum(row["intent"] != "invite" for row in result)}.', flush=True)
    return result


def _order(row):
    return (sources._utc(row['created_at'], 'invalid mention timestamp'), int(row['id']))


def apply_controls(rows, state):
    """Honor the latest explicit author command, even if an older page is replayed."""
    changed = False
    for row in rows:
        if row['intent'] not in ('stop', 'resume') or (_requires_own_parent(row) and not is_own_reply(row)):
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
            and (not _requires_own_parent(row) or is_own_reply(row))
            and (not preference or _order(row) > _order(preference)))
