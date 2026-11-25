"""Bounded discovery of recent public posts for replies or read-only inspiration.

Source text and card metadata are evidence, never instructions. This module does
not fetch links, infer views from screenshots, or turn missing evidence into a
passing source. Search results are checked again by the publisher before send.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import ipaddress
from itertools import zip_longest
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from twikit.errors import NotFound, TweetNotAvailable

from .policy import MIN_SOURCE_VIEWS, has_excluded_terms
from .store import ACCOUNT


MAX_AGE = timedelta(hours=24)
SEARCH_COUNT = 20
ORIGINAL_SOURCE_POLICY_VERSION = 'original-inspiration-v2'
# The first two searches cover named tools. The others look for work people
# describe regardless of which account posted it or which tools they used.
TOPIC_QUERIES = (
    ('("Claude Code" OR Codex OR OpenCode OR Cursor)',
     '("GitHub Copilot" OR Windsurf OR Cline OR Aider)'),
    ('(Claude OR ChatGPT OR Gemini OR DeepSeek) coding',
     '(Anthropic OR OpenAI OR "AI model") coding'),
    ('("code review" OR debugging) (tests OR latency OR repro)',
     '("coding agent" OR refactoring) (diff OR tests OR bug)'),
)
PRACTITIONER_QUERIES = (
    '("I built" OR "I tried" OR "my project") (debugging OR "code review" OR "coding agent")',
    '("I shipped" OR "I fixed" OR "I wrote") (compiler OR debugger OR "unit tests")',
)
# Replace two existing searches without adding an endpoint request. One broad
# lane finds questions across niches; the other rotates a more open topic lane.
# X's engagement operator is only a retrieval hint: actual eligibility still
# requires measured views and never requires a like count.
QUESTION_BROAD_QUERY = (
    '("would you choose" OR "which would you" OR "biggest bottleneck" OR "main bottleneck" OR "what if") min_faves:15'
)
QUESTION_QUERIES = (
    '(AI OR LLM OR inference) ("why does" OR "what if" OR "would you")',
    '(startup OR SaaS OR pricing) ("should we" OR "which would" OR "how would")',
    '(software OR database OR coding) ("why does" OR "would you" OR "which would")',
    '(meme OR "group chat") ("why do" OR "what if" OR "how would")',
)
# Read these conversations for standalone writing. Each lane gets one search,
# so a busy AI launch cannot fill every place in the research shortlist.
ORIGINAL_QUERIES = (
    ('("AI model" OR "coding agent") (benchmark OR prompt OR demo)',
     '(debugging OR "open source") (maintainer OR changelog OR bug)'),
    ('(startup OR founder) ("first customer" OR pricing OR waitlist)',
     '(startup OR bootstrapped) (prototype OR onboarding OR "support email")'),
    ('(meme OR fandom) (caption OR remix OR screenshot)',
     '("group chat" OR "reply guy" OR shitpost) (joke OR screenshot OR thread)'),
)
ORIGINAL_LATEST = (
    '("I built" OR "we shipped") (browser OR model OR prototype)',
    '("group chat" OR meme) (sticker OR caption OR template)',
    '("first customer" OR "first sale") (demo OR invoice OR email)',
)
_HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}\Z")
_NUMBER = re.compile(r"[0-9]+\Z")
_URL = re.compile(r"https?://\S+", re.I)
_TAG = re.compile(r'(?<![\w@])@([A-Za-z0-9_]{1,15})(?![A-Za-z0-9_])')
_QUOTED_QUESTION = re.compile(
    r'"(?=[^"\n]*(?:\?|\b(?:why|how|what|which|should|would|could)\b))[^"\n]*"|'
    r'“(?=[^”\n]*(?:\?|\b(?:why|how|what|which|should|would|could)\b))[^”\n]*”|^\s*>.*$', re.I | re.M)
_QUESTION_TOPIC = re.compile(
    r'\b(?:ai|llms?|gpt\w*|chatgpt|claude|codex|gemini|deepseek|copilot|'
    r'inference|reasoning models?|language models?|machine learning|training data|'
    r'neural|tokens?|context windows?|retrieval|rag|hallucinat\w*|gpus?|'
    r'latency|throughput|compute|memory bandwidth|cach\w*|'
    r'code|coding|programming|software|developer\w*|debug\w*|compilers?|'
    r'databases?|sql|apis?|browsers?|servers?|backups?|restores?|storage|'
    r'encryption|open source|unit tests?|'
    r'code review|distributed systems?|agents?|automation|'
    r'startups?|founders?|saas|bootstrapp\w*|onboarding|retention|pricing|'
    r'product.market fit|first customers?|network effects?|'
    r'memes?|fandoms?|group chats?|internet culture|social media|'
    r'feeds?|moderation|creators?|shitposts?)\b', re.I)
_OPEN_QUESTION = re.compile(
    r'\b(?:why|how|what if|what happens|what changes|what makes|what causes|'
    r'what becomes|what remains|what matters|'
    r'what (?:is|are|would|should|could|might|does|do)|which|'
    r'where (?:is|are|does|do|would|should)|when (?:would|should|does|do))\b', re.I)
_CHOICE_QUESTION = re.compile(r'\b(?:should|would|could|can|is|are|do|does)\b', re.I)
_UNPUNCTUATED_ASK = re.compile(
    r'(?:^|[.!:,\n]\s*)(?:'
    r'(?:why|how) (?:do|does|did|is|are|was|were|can|could|would|will|should|might|must)\b|'
    r'what (?:if|would|should|could|might|does|do|is|are|makes|becomes|remains|matters|changes|happens|causes)\b|'
    r'where (?:is|are|does|do|would|should)\b|when (?:would|should|does|do)\b|'
    r'which(?:\s+[A-Za-z]+){0,4}\s+(?:would|should|could|is|are|works|matters|comes|scales)\b|'
    r'(?:would|should|could|can|do|does|is|are)\s+(?:you|we|i|a|an|the)\b|'
    r'help (?:me|us) (?:choose|decide)\b)', re.I)
_REASONING_DETAIL = re.compile(
    r'\b(?:or|if|between|versus|vs|trade.?offs?|bottleneck|instead|better|worse|'
    r'limit\w*|scale\w*|fail\w*|depend\w*|affect\w*|worth)\b', re.I)
_NOT_ANALYTICAL = re.compile(
    r'\b(?:how are you|how is everyone|what.?s up|good morning|good night|'
    r'who else|anyone else|am i the only|who needs|why bother|'
    r'what could (?:possibly )?go wrong|can we all agree|'
    r'are you (?:ready|excited)|who.?s ready|'
    r'(?:your|everyone.?s) favou?rite|'
    r'how much (?:is|does)\b[^?]*\bcost|when was\b[^?]*\b(?:released|launched)|'
    r'who (?:created|founded|owns)|latest version|'
    r'emergency|urgent(?:ly)?|suicid\w*|overdose|chest pain|'
    r'(?:my|our) (?:account|password|subscription|refund)|'
    r'(?:can.?t|cannot|unable to) (?:log|sign) in|'
    r'help me(?!\s+(?:choose|decide)\b)|anyone (?:please )?help|fix my|production is down|'
    r'promo code|coupon|giveaway|limited.time|link in (?:my |our )?bio|'
    r'join (?:my |our |the )?waitlist|sign up (?:now|today)|'
    r'try (?:my|our)\b|buy (?:my|our)\b|dm me|'
    r'(?:like|repost|retweet) (?:and|to)\b)\b', re.I)
_OTHER_ASSISTANTS = frozenset({'grok', 'chatgpt', 'claude', 'claudeai', 'gemini', 'copilot', 'askperplexity'})
_COLLECTIVE_ADDRESS = frozenset({'developers', 'builders', 'founders', 'engineers', 'folks',
                               'everyone', 'anyone', 'people', 'friends', 'team', 'chat'})
_DISCOURSE_OPENERS = frozenset({'honestly', 'so', 'actually', 'also', 'well', 'okay', 'ok',
                              'yes', 'no', 'wait', 'seriously', 'look', 'anyway', 'instead',
                              'finally', 'first', 'then', 'second', 'however', 'maybe',
                              'technically', 'basically', 'apparently', 'curiously', 'question'})
_PARENT_INVITATION = re.compile(
    r'\b(?:(?:please )?(?:answer|explain|reply to|respond to) (?:this|that|the question)|'
    r'what do you (?:think|make of)|your (?:thoughts|take)|thoughts\s*[?!])', re.I)
_TRACKING = frozenset({
    "ref", "referrer", "referer", "source", "src", "campaign", "fbclid",
    "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "s", "t",
})
_SOCIAL_OR_SHORT = frozenset({
    "x.com", "twitter.com", "t.co", "pic.twitter.com", "bit.ly", "bitly.com",
    "tinyurl.com", "lnkd.in", "goo.gl", "ow.ly", "buff.ly", "youtu.be",
})
_LANDINGS = frozenset({"/", "/index.html", "/index.htm", "/home", "/blog", "/news"})
_REJECTION_REASONS = {
    f"source has fewer than {MIN_SOURCE_VIEWS} measured views": "below_min_views",
    "missing or invalid measured views": "unknown_views",
    "source is outside the 24-hour freshness window": "outside_24h",
    "source is a reply": "reply",
    "source is a repost": "repost",
    "source is a quote": "quote",
    "source author is unavailable or protected": "unavailable_author",
    "source contains excluded topics": "excluded_topic",
    "linked metadata contains excluded topics": "excluded_topic",
    "missing full source text": "missing_text",
    "source has insufficient factual text": "insufficient_text",
    "source text is truncated or ambiguous": "truncated_text",
}


class SourceError(ValueError):
    """The source does not meet the publishing evidence rules."""


def canonical_url(url):
    """Return a stable linked-article identity, or '' for a non-specific link.

    This is a comparison key, not a claim that a remote URL was fetched. Drop
    tracking parameters and fragments, retain parameters that identify content,
    and never follow redirects or accept model-provided substitutions.
    """
    if not isinstance(url, str) or not url or len(url) > 8192:
        return ""
    if re.search(r"[\s\x00-\x1f\x7f\\]", url):
        return ""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.casefold() not in {"http", "https"} or parsed.username or parsed.password:
            return ""
        host = (parsed.hostname or "").casefold().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        if not host or "." not in host or host in _SOCIAL_OR_SHORT or host.endswith(".local"):
            return ""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return ""
        port = parsed.port
        if port not in (None, 80, 443):
            return ""
        query = sorted((key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                       if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING)
        path = parsed.path.rstrip("/") or "/"
        # Root links and collection pages are shared by unrelated announcements.
        if path.casefold() in _LANDINGS and not query:
            return ""
        return urlunsplit(("https", host, path, urlencode(query), ""))
    except (TypeError, ValueError, UnicodeError):
        return ""


def _number(value, reason, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SourceError(reason)
    text = str(value)
    if not _NUMBER.fullmatch(text) or (positive and int(text) <= 0):
        raise SourceError(reason)
    return int(text)


def _identifier(value):
    return str(_number(value, "missing or invalid source identity", positive=True))


def _utc(value, reason):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise SourceError(reason)
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise SourceError(reason) from exc


def _text(value, minimum_letters=20):
    if not isinstance(value, str) or not value.strip():
        raise SourceError("missing full source text")
    value = value.strip()
    if len(value) > 20_000 or "\u2026" in value or "..." in value:
        raise SourceError("source text is truncated or ambiguous")
    # A bare URL, media-only post, or teaser is not enough evidence for a news
    # claim. Do not promote an attached Article's preview into its full body.
    prose = _URL.sub("", value)
    if sum(char.isalpha() for char in prose) < minimum_letters:
        raise SourceError("source has insufficient factual text")
    if re.match(r"^RT\s+@", value, flags=re.I):
        raise SourceError("source is a repost")
    if has_excluded_terms(value):
        raise SourceError("source contains excluded topics")
    return value


def validate(candidate, now):
    """Validate and normalize a serialized snapshot; raise SourceError on fail."""
    if isinstance(candidate, dict) and candidate.get('origin') == 'inspiration':
        raise SourceError('Read-only original inspiration cannot be a reply source')
    return validate_fields(candidate, now, require_reach=True, minimum_letters=20)


def validate_inspiration(candidate, now):
    """Keep all source checks except reach, without granting reply eligibility."""
    if (not isinstance(candidate, dict) or candidate.get('parent_id')
            or candidate.get('origin') not in (None, 'inspiration')):
        raise SourceError('Original inspiration requires a public original')
    result = validate_fields(candidate, now, require_reach=False, minimum_letters=20)
    result['origin'] = 'inspiration'
    return result


def validate_fields(candidate, now, *, require_reach, minimum_letters):
    if not isinstance(candidate, dict):
        raise SourceError("invalid source snapshot")
    source_id = _identifier(candidate.get("id"))
    author_id = _identifier(candidate.get("author_id"))
    author = candidate.get("author")
    if not isinstance(author, str) or not _HANDLE.fullmatch(author):
        raise SourceError("missing or invalid source author handle")
    source_url = f"https://x.com/{author}/status/{source_id}"
    if candidate.get("url") != source_url:
        raise SourceError("source URL does not match its identity")
    created = _utc(candidate.get("created_at"), "missing or invalid source timestamp")
    age = _utc(now, "invalid current timestamp") - created
    if age < timedelta(0) or age > MAX_AGE:
        raise SourceError("source is outside the 24-hour freshness window")
    views = candidate.get("views")
    if require_reach or views is not None:
        views = _number(views, "missing or invalid measured views")
    if require_reach and views < MIN_SOURCE_VIEWS:
        raise SourceError(f"source has fewer than {MIN_SOURCE_VIEWS} measured views")
    text = _text(candidate.get("text"), minimum_letters)
    aliases = candidate.get("source_ids")
    if not isinstance(aliases, list) or not aliases:
        raise SourceError("missing edit identities")
    source_ids = list(dict.fromkeys(_identifier(alias) for alias in [source_id, *aliases]))
    urls = candidate.get("urls")
    if not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
        raise SourceError("invalid article identities")
    canonical = list(dict.fromkeys(canonical_url(url) for url in urls))
    if "" in canonical:
        raise SourceError("invalid article identities")
    result = dict(candidate)
    result.update(id=source_id, url=source_url, author=author, author_id=author_id,
                  text=text, created_at=created.isoformat(), views=views,
                  urls=canonical, source_ids=source_ids)
    return result


def snapshot(tweet, now):
    """Read an original Twikit post without fetching article bodies or media."""
    return checked_snapshot(tweet, now, include_parent=False, validator=validate)


def inspiration_snapshot(tweet, now):
    """Read the same complete native metadata, without requiring popularity."""
    return checked_snapshot(tweet, now, include_parent=False, validator=validate_inspiration)


def checked_snapshot(tweet, now, *, include_parent, validator):
    try:
        return _snapshot(tweet, now, include_parent, validator)
    except SourceError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        # Malformed optional GraphQL blocks must reject this item, not take
        # down a whole search. None of these local property reads makes a
        # request; transport and authentication exceptions are not caught.
        raise SourceError("malformed source metadata") from exc


def tweet_ids(tweet):
    raw = tweet._data
    source_id = _identifier(tweet.id)
    # edit_tweet_ids on the pinned client raises KeyError when edit_control is absent.
    control = raw.get("edit_control") or {}
    if not isinstance(control, dict):
        raise SourceError("invalid edit identities")
    initial = control.get("edit_control_initial") or {}
    if not isinstance(initial, dict):
        raise SourceError("invalid edit identities")
    aliases = [source_id]
    if control.get("initial_tweet_id") is not None:
        aliases.append(_identifier(control["initial_tweet_id"]))
    for versions in (control.get("edit_tweet_ids", []), initial.get("edit_tweet_ids", []),
                     raw.get("edit_history_tweet_ids", [])):
        if not isinstance(versions, list):
            raise SourceError("invalid edit identities")
        aliases.extend(_identifier(ident) for ident in versions)
    return list(dict.fromkeys(aliases))


def conversation_id(raw):
    legacy = raw.get("legacy") or {}
    value = (legacy.get("conversation_id_str") or raw.get("conversation_id_str") or
             legacy.get("conversation_id") or raw.get("conversation_id"))
    return _identifier(value) if value is not None else None


def reply_parent_id(raw):
    legacy = raw.get("legacy") or {}
    value = (legacy.get("in_reply_to_status_id_str") or raw.get("in_reply_to_status_id_str") or
             legacy.get("in_reply_to_status_id") or raw.get("in_reply_to_status_id"))
    return _identifier(value) if value is not None else None


def _snapshot(tweet, now, include_parent, validator):
    raw = getattr(tweet, "_data", {})
    if not isinstance(raw, dict):
        raise SourceError("invalid source metadata")
    legacy = raw.get("legacy") or {}
    if not isinstance(legacy, dict):
        raise SourceError("invalid source metadata")
    parent = reply_parent_id(raw) or getattr(tweet, "in_reply_to", None)
    if not include_parent and (parent or legacy.get("in_reply_to_user_id_str")):
        raise SourceError("source is a reply")
    if legacy.get("retweeted_status_result") or legacy.get("retweeted_status") or getattr(tweet, "retweeted_tweet", None):
        raise SourceError("source is a repost")
    if (legacy.get("is_quote_status") or legacy.get("quoted_status_id_str")
            or raw.get("quoted_status_result") or getattr(tweet, "quoted_status_id", None)
            or getattr(tweet, "quote", None)):
        raise SourceError("source is a quote")
    author = getattr(tweet, "user", None)
    if (author is None or getattr(author, "protected", False)
            or getattr(author, "blocked_by", False) or getattr(author, "blocking", False)
            or legacy.get("possibly_sensitive") or getattr(tweet, "possibly_sensitive", False)):
        raise SourceError("source author is unavailable or protected")
    # A long note can be complete even when legacy.full_text is truncated.
    note = ((raw.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
    note_text = note.get("text")
    if legacy.get("truncated") and not (isinstance(note_text, str) and note_text.strip()):
        raise SourceError("source text is truncated or ambiguous")
    full_text = getattr(tweet, "full_text", None)
    if not isinstance(full_text, str):
        full_text = getattr(tweet, "text", None)
    # Inspect all supplied titles as well as full text, without passing them off
    # as the source's own prose. The publisher refreshes this check before send.
    metadata = [getattr(tweet, "thumbnail_title", None)]
    article = getattr(tweet, "article", None)
    if article is not None:
        metadata.extend((getattr(article, "title", None), getattr(article, "preview_text", None)))
    entities = getattr(tweet, "urls", None) or []
    urls = []
    for entity in entities:
        if isinstance(entity, dict):
            metadata.extend((entity.get("title"), entity.get("description")))
            expanded = entity.get("unwound_url") or entity.get("expanded_url") or entity.get("url")
        elif isinstance(entity, str):
            expanded = entity
        else:
            continue
        canonical = canonical_url(expanded)
        if canonical:
            urls.append(canonical)
    if any(isinstance(value, str) and has_excluded_terms(value) for value in metadata):
        raise SourceError("linked metadata contains excluded topics")
    # Plain, unshortened links are sometimes absent from the entity list.
    if isinstance(full_text, str):
        urls.extend(canonical for url in _URL.findall(full_text)
                    if (canonical := canonical_url(url.rstrip(".,;!?)"))))
    try:
        created_at = tweet.created_at_datetime
    except (AttributeError, KeyError, ValueError, TypeError) as exc:
        raise SourceError("missing or invalid source timestamp") from exc
    aliases = tweet_ids(tweet)
    source_id = aliases[0]
    handle = getattr(author, "screen_name", None)
    fields = {
        "id": source_id,
        "url": f"https://x.com/{handle}/status/{source_id}",
        "author": handle,
        "author_id": getattr(author, "id", None),
        "text": full_text,
        "created_at": created_at,
        "views": getattr(tweet, "view_count", None),
        "urls": list(dict.fromkeys(urls)),
        "source_ids": aliases,
    }
    if include_parent:
        root = conversation_id(raw)
        if not parent and (root is None or root in aliases):
            root = min(aliases, key=int)
        fields.update(origin="mention", parent_id=parent, conversation_id=root,
                      parent_author=legacy.get("in_reply_to_screen_name") or raw.get("in_reply_to_screen_name"))
    return validator(fields, now)


def _other_addressee(text):
    """Recognize an addressed recipient without treating every mention as one."""
    prose = _QUOTED_QUESTION.sub(' ', _URL.sub('', text)).strip()
    tags = [tag.casefold() for tag in _TAG.findall(prose)]
    if _OTHER_ASSISTANTS.intersection(tags):
        return True
    leading = re.match(r'^(?:(?:hey|hi|hello|yo|ok|okay)[\s,!.:]+)?'
                       r'((?:@[A-Za-z0-9_]{1,15}[\s,]*)+)', prose, re.I)
    if leading and any(tag.casefold() != ACCOUNT for tag in _TAG.findall(leading[1])):
        return True
    # A bare name is a recipient only in a vocative, not merely a named topic.
    named = re.match(r'^(?i:hey|hi|hello|yo)[\s,]+([A-Za-z][A-Za-z0-9_]*)\b|'
                     r'^([A-Z][A-Za-z0-9_]{1,30})\s*,\s*', prose)
    if named:
        name = (named[1] or named[2]).casefold()
        if (name not in _COLLECTIVE_ADDRESS and name not in _DISCOURSE_OPENERS
                and name not in (ACCOUNT, 'qofeno') and not _QUESTION_TOPIC.fullmatch(name)):
            return True
    if re.match(r'^(?:grok|chatgpt|claude|gemini|copilot)\b[\s,:]+', prose, re.I):
        return True
    for match in _TAG.finditer(prose):
        if match[1].casefold() == ACCOUNT:
            continue
        before, after = prose[:match.start()], prose[match.end():]
        if re.match(r'\s*[,،:]?\s*(?:can|could|would|will|do|did|are|should) you\b', after, re.I):
            return True
        if re.match(r'\s*[,،:]?\s*(?:why|how|what|which|where|when)\b', after, re.I):
            return True
        if (not after.strip(' \n\t?!.,:') and ('?' in before or _UNPUNCTUATED_ASK.search(before.strip()))
                and not re.search(r'\b(?:by|from|at|about|via|with|of|to)\s*$', before, re.I)):
            return True
    return False


def _analytical_question(text):
    prose = _QUOTED_QUESTION.sub(' ', _URL.sub('', text)).strip()
    if (_other_addressee(prose) or _NOT_ANALYTICAL.search(prose)
            or not _QUESTION_TOPIC.search(prose)):
        return False
    # Punctuation is optional in public posts. Require an actual interrogative
    # clause or explicit decision invitation, not a headline such as "How AI
    # changes coding" or a statement that happens to contain "why".
    unpunctuated = '?' not in prose
    if unpunctuated and not _UNPUNCTUATED_ASK.search(_TAG.sub('', prose).strip()):
        return False
    # A quoted question, a slogan or the author's own question-and-answer hook
    # is not an open request for another participant's analysis.
    if re.search(r'[?!.:\n—–]\s*(?:because\b|here.?s\b|the answer\b|answer\s*:|a thread\b)', prose, re.I):
        return False
    if unpunctuated and re.search(r'\b(?:explained|the answer is|depends on|we found|i found)\b', prose, re.I):
        return False
    words = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", _TAG.sub('', prose))
    if len(words) < 6:
        return False
    return bool(_OPEN_QUESTION.search(prose)
                or (re.search(r'\bhelp (?:me|us) (?:choose|decide)\b', prose, re.I)
                    and _REASONING_DETAIL.search(prose))
                or (_CHOICE_QUESTION.search(prose) and _REASONING_DETAIL.search(prose)))


def is_reasoned_question(source):
    """Classify a native, attributed source; never grant publishing eligibility.

    Discovery and the final native refresh still enforce reach, privacy, age,
    opt-outs and source integrity. A mention may borrow a question only from
    its verified exact parent when the author expressly asks Qofeno to answer.
    """
    if not isinstance(source, dict) or source.get('origin') == 'inspiration':
        return False
    # The local import avoids a module initialization cycle with mentions.
    from .reply_writer import INJECTION, SourceRejected, mention_parent
    try:
        _identifier(source.get('id'))
        _identifier(source.get('author_id'))
        author = source.get('author')
        if (not isinstance(author, str) or not _HANDLE.fullmatch(author)
                or author.casefold() == ACCOUNT or author.casefold() in _OTHER_ASSISTANTS):
            return False
        text = _text(source.get('text'), minimum_letters=1)
        if len(text) > 3000 or _other_addressee(text):
            return False
        # Use the writer's existing instruction and parent-integrity checks.
        if INJECTION.search(text):
            return False
        if _analytical_question(text):
            return True
        if (source.get('origin') != 'mention' or source.get('intent') != 'invite'
                or ACCOUNT not in {tag.casefold() for tag in _TAG.findall(text)}
                or not _PARENT_INVITATION.search(text) or _NOT_ANALYTICAL.search(text)):
            return False
        from .mentions import _intent
        if _intent(text) != 'invite':
            return False
        parent = mention_parent(source)
        if not parent or _identifier(parent['id']) == source['id']:
            return False
        parent_text = _text(parent['text'], minimum_letters=1)
        return _analytical_question(parent_text)
    except (SourceError, SourceRejected, KeyError, TypeError, ValueError):
        return False


def search_plan(now, *, originals=False, reasoned_questions=False):
    """Use three Top searches and one Latest search for the current opportunity."""
    current = _utc(now, "invalid current timestamp")
    hour = int(current.timestamp() // 3600)
    since = (current - MAX_AGE).date().isoformat()
    suffix = f" since:{since} -filter:replies -filter:retweets"
    topics = ORIGINAL_QUERIES if originals else TOPIC_QUERIES
    latest = ORIGINAL_LATEST if originals else PRACTITIONER_QUERIES
    plan = [(variants[hour % len(variants)] + suffix, "Top") for variants in topics]
    if reasoned_questions and not originals:
        # A bare question-mark operator returned zero raw results in live
        # retrieval. Search ordinary phrases; assess actual questions locally.
        plan[0] = (QUESTION_BROAD_QUERY + suffix, "Top")
        plan[1] = (QUESTION_QUERIES[hour % len(QUESTION_QUERIES)] + suffix, "Top")
    plan.append((latest[hour % len(latest)] + suffix, "Latest"))
    return plan


def shortlist(candidates, now, limit=4, *, originals=False, reasoned_questions=False):
    """Select distinct authors across search topics, favoring recent measured reach.

    The views-per-hour estimate is a lifetime average from one public snapshot.
    A one-hour age floor prevents a post only seconds old from dominating.
    The first turn follows the search order that discover rotates each half hour.
    """
    current = _utc(now, "invalid current timestamp")
    groups = {}
    for candidate in candidates:
        try:
            candidate = (validate_inspiration if originals else validate)(candidate, current)
        except SourceError:
            continue
        if reasoned_questions and _other_addressee(candidate['text']):
            continue
        age_hours = (current - _utc(candidate["created_at"], "invalid source timestamp")).total_seconds() / 3600
        # Unknown views stay unknown in the snapshot. A zero ranking contribution
        # is not a measured count and cannot qualify this source for a reply.
        reach_per_hour = (candidate["views"] or 0) / max(1, age_hours)
        priority = bool(reasoned_questions and is_reasoned_question(candidate))
        groups.setdefault((priority, candidate.get("discovery_query")), []).append((reach_per_hour, candidate))

    selected = []
    authors = set()
    # Exhaust genuine questions before the normal fallback, preserving topic
    # rotation and distinct authors within each tier. The daily target is never
    # filled by relabeling an ordinary declaration as a question.
    for priority in (True, False):
        ranked_groups = [iter(sorted(group, key=lambda pair: pair[0], reverse=True))
                         for (question, _), group in groups.items() if question == priority]
        while len(selected) < limit:
            previous = len(selected)
            for group in ranked_groups:
                for _, candidate in group:
                    if candidate["author_id"] not in authors:
                        selected.append(candidate)
                        authors.add(candidate["author_id"])
                        break
                if len(selected) == limit:
                    return selected
            if len(selected) == previous:
                break
    return selected


async def discover(connection, state, now, *, originals=False, reasoned_questions=False):
    """Run four sequential topic searches; return unseen eligible snapshots.

    No pagination, endpoint retries, badge requirement, or volume fallback.
    An unavailable query does not discard successful independent queries.
    Session, authentication, rate-limit, and other transport failures propagate;
    if every query is unavailable, the final availability error also propagates.
    """
    current = _utc(now, "invalid current timestamp")
    used_ids = {str(value) for value in state.get("used_source_ids", [])}
    used_urls = {canonical_url(value) for value in state.get("used_urls", [])}
    used_urls.discard("")
    batches = []
    counts = Counter()
    rejections = Counter()
    queries = search_plan(current, originals=originals, reasoned_questions=reasoned_questions)
    unavailable = []
    for index, (query, product) in enumerate(queries, start=1):
        before = dict(counts)
        batch = []
        batches.append(batch)
        try:
            results = await connection.search_tweet(query, product, count=SEARCH_COUNT)
        except (NotFound, TweetNotAvailable) as exc:
            unavailable.append(exc)
            print(f"Source query {index} ({product}): unavailable={type(exc).__name__}; "
                  "continuing independent queries without retry")
            continue
        for tweet in results:
            counts["examined"] += 1
            try:
                candidate = (inspiration_snapshot if originals else snapshot)(tweet, current)
            except SourceError as exc:
                counts["ineligible"] += 1
                rejections[_REJECTION_REASONS.get(str(exc), "invalid_metadata")] += 1
                continue
            if reasoned_questions and _other_addressee(candidate['text']):
                counts['ineligible'] += 1
                rejections['other_addressee'] += 1
                continue
            if used_ids.intersection(candidate["source_ids"]) or used_urls.intersection(candidate["urls"]):
                counts["duplicates"] += 1
                continue
            used_ids.update(candidate["source_ids"])
            used_urls.update(candidate["urls"])
            candidate["discovery_query"] = index
            batch.append(candidate)
        batch.sort(key=lambda candidate: candidate["created_at"], reverse=True)
        print(f"Source query {index} ({product}): "
              f"examined={counts['examined'] - before.get('examined', 0)} "
              f"eligible={len(batch)} "
              f"ineligible={counts['ineligible'] - before.get('ineligible', 0)} "
              f"duplicates={counts['duplicates'] - before.get('duplicates', 0)}")
    if len(unavailable) == len(queries):
        raise unavailable[-1]
    # Rotate which topic gets the first turn each half hour. Keep every eligible
    # result here so the planner can shortlist after accounting for recent contacts.
    first = int(current.timestamp() // 1800) % len(batches)
    batches = batches[first:] + batches[:first]
    candidates = [item for round_ in zip_longest(*batches) for item in round_ if item is not None]
    if reasoned_questions:
        candidates.sort(key=is_reasoned_question, reverse=True)
    print(f"Source discovery: examined={counts['examined']} eligible={len(candidates)} "
          f"ineligible={counts['ineligible']} duplicates={counts['duplicates']}.")
    if rejections:
        print("Source exclusions: " + " ".join(f"{reason}={count}" for reason, count in sorted(rejections.items())))
    return candidates
