"""Bounded discovery of recent, primary-source posts with measured reach.

Source text and card metadata are evidence, never instructions. This module does
not fetch links, infer views from screenshots, or turn missing evidence into a
passing source. Search results are checked again by the publisher before send.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .policy import has_excluded_terms


MIN_VIEWS = 100_000
MAX_AGE = timedelta(hours=24)
SEARCH_COUNT = 20
# Primary company accounts, not aggregators or individuals. A blue check alone
# does not establish authority. Keep searches small and deterministic.
AUTHOR_GROUPS = (
    ("OpenAI", "AnthropicAI", "GoogleDeepMind", "GoogleAI", "huggingface", "xAI"),
    ("opencode", "cursor_ai", "github", "vercel", "Figma", "NotionHQ"),
    ("Tesla", "BMW", "MercedesBenz", "Porsche", "NVIDIA", "Microsoft"),
)
ALLOWED_AUTHORS = frozenset(name.casefold() for group in AUTHOR_GROUPS for name in group)
_HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}\Z")
_NUMBER = re.compile(r"[0-9]+\Z")
_URL = re.compile(r"https?://\S+", re.I)
_TRACKING = frozenset({
    "ref", "referrer", "referer", "source", "src", "campaign", "fbclid",
    "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "s", "t",
})
_SOCIAL_OR_SHORT = frozenset({
    "x.com", "twitter.com", "t.co", "pic.twitter.com", "bit.ly", "bitly.com",
    "tinyurl.com", "lnkd.in", "goo.gl", "ow.ly", "buff.ly", "youtu.be",
})
_LANDINGS = frozenset({"/", "/index.html", "/index.htm", "/home", "/blog", "/news"})


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


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise SourceError("missing full source text")
    value = value.strip()
    if len(value) > 20_000 or "\u2026" in value or "..." in value:
        raise SourceError("source text is truncated or ambiguous")
    # A bare URL, media-only post, or teaser is not enough evidence for a news
    # claim. Do not promote an attached Article's preview into its full body.
    prose = _URL.sub("", value)
    if sum(char.isalpha() for char in prose) < 20:
        raise SourceError("source has insufficient factual text")
    if re.match(r"^RT\s+@", value, flags=re.I):
        raise SourceError("source is a repost")
    if has_excluded_terms(value):
        raise SourceError("source contains excluded topics")
    return value


def validate(candidate, now):
    """Validate and normalize a serialized snapshot; raise SourceError on fail."""
    if not isinstance(candidate, dict):
        raise SourceError("invalid source snapshot")
    source_id = _identifier(candidate.get("id"))
    author_id = _identifier(candidate.get("author_id"))
    author = candidate.get("author")
    if not isinstance(author, str) or not _HANDLE.fullmatch(author) or author.casefold() not in ALLOWED_AUTHORS:
        raise SourceError("source author is not allowlisted")
    source_url = f"https://x.com/{author}/status/{source_id}"
    if candidate.get("url") != source_url:
        raise SourceError("source URL does not match its identity")
    created = _utc(candidate.get("created_at"), "missing or invalid source timestamp")
    age = _utc(now, "invalid current timestamp") - created
    if age < timedelta(0) or age > MAX_AGE:
        raise SourceError("source is outside the 24-hour freshness window")
    views = _number(candidate.get("views"), "missing or invalid measured views")
    if views < MIN_VIEWS:
        raise SourceError("source has fewer than 100000 measured views")
    text = _text(candidate.get("text"))
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
    try:
        return _snapshot(tweet, now)
    except SourceError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        # Malformed optional GraphQL blocks must reject this item, not take
        # down a whole search. None of these local property reads makes a
        # request; transport and authentication exceptions are not caught.
        raise SourceError("malformed source metadata") from exc


def _snapshot(tweet, now):
    raw = getattr(tweet, "_data", {})
    if not isinstance(raw, dict):
        raise SourceError("invalid source metadata")
    legacy = raw.get("legacy") or {}
    if not isinstance(legacy, dict):
        raise SourceError("invalid source metadata")
    if (legacy.get("in_reply_to_status_id_str") or legacy.get("in_reply_to_user_id_str")
            or getattr(tweet, "in_reply_to", None)):
        raise SourceError("source is a reply")
    if legacy.get("retweeted_status_result") or legacy.get("retweeted_status") or getattr(tweet, "retweeted_tweet", None):
        raise SourceError("source is a repost")
    if (legacy.get("is_quote_status") or legacy.get("quoted_status_id_str")
            or raw.get("quoted_status_result") or getattr(tweet, "quoted_status_id", None)
            or getattr(tweet, "quote", None)):
        raise SourceError("source is a quote")
    author = getattr(tweet, "user", None)
    if author is None or getattr(author, "protected", False):
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
    # edit_tweet_ids raises KeyError in the pinned client if edit_control is
    # absent; read the raw optional block to include all available aliases.
    control = raw.get("edit_control") or {}
    aliases = control.get("edit_tweet_ids") or []
    if not isinstance(aliases, list):
        raise SourceError("invalid edit identities")
    source_id = _identifier(getattr(tweet, "id", None))
    handle = getattr(author, "screen_name", None)
    return validate({
        "id": source_id,
        "url": f"https://x.com/{handle}/status/{source_id}",
        "author": handle,
        "author_id": getattr(author, "id", None),
        "text": full_text,
        "created_at": created_at,
        "views": getattr(tweet, "view_count", None),
        "urls": list(dict.fromkeys(urls)),
        "source_ids": [source_id, *aliases],
    }, now)


async def discover(connection, state, now):
    """Run three sequential Latest searches; return unseen eligible snapshots.

    No pagination, endpoint retries, badge requirement, or volume fallback.
    Session, authentication, rate-limit, and transport failures propagate to the
    workflow rather than masquerading as a successful empty discovery.
    """
    current = _utc(now, "invalid current timestamp")
    since = (current - MAX_AGE).date().isoformat()
    used_ids = {str(value) for value in state.get("used_source_ids", [])}
    used_urls = {canonical_url(value) for value in state.get("used_urls", [])}
    used_urls.discard("")
    candidates = []
    counts = Counter()
    for group in AUTHOR_GROUPS:
        query = "(" + " OR ".join(f"from:{author}" for author in group) + f") since:{since} -filter:replies -filter:retweets"
        results = await connection.search_tweet(query, "Latest", count=SEARCH_COUNT)
        for tweet in results:
            counts["examined"] += 1
            try:
                candidate = snapshot(tweet, current)
            except SourceError:
                counts["ineligible"] += 1
                continue
            if used_ids.intersection(candidate["source_ids"]) or used_urls.intersection(candidate["urls"]):
                counts["duplicates"] += 1
                continue
            used_ids.update(candidate["source_ids"])
            used_urls.update(candidate["urls"])
            candidates.append(candidate)
    candidates.sort(key=lambda candidate: (candidate["created_at"], candidate["views"]), reverse=True)
    print(f"Source discovery: examined={counts['examined']} eligible={len(candidates)} "
          f"ineligible={counts['ineligible']} duplicates={counts['duplicates']}; "
          "40/day is a ceiling subject to source availability.")
    return candidates
