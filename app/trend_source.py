from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Iterable

from twikit import Client

SEARCHES_PER_RUN = 5
OFFICIAL_BRAND_SEARCHES_PER_RUN = 10
AI_TECH_SEARCHES_PER_RUN = 8
TWEETS_PER_SEARCH = 15
MAX_TWEET_AGE = timedelta(hours=18)
SOURCE_HISTORY_LIMIT = 100

# Fallback discovery queries for when X's Trends feed is unavailable or has no
# eligible AI/tech/gaming/sports topics. Official Trends results are preferred
# for the manual-review lane; neither source is ever used for auto-publishing.
SIGNAL_SEARCHES = (
    "ChatGPT",
    "OpenAI",
    "Claude AI",
    "Gemini AI",
    "AI agents",
    "AI coding",
    "developer tools",
    "startup AI",
    "founder AI",
    "CEO AI",
    "CTO AI",
    "NVIDIA",
    "Apple",
    "gaming",
    "NBA",
    "football",
    "Formula 1",
    "tennis",
)


# Keep trend discovery focused on the requested subjects. A source/topic must
# contain an allowed signal, while political/current-affairs signals are rejected.
ALLOWED_TOPIC_KEYWORDS = (
    "artificial intelligence",
    "ai",
    "machine learning",
    "llm",
    "llms",
    "large language model",
    "foundation model",
    "chatgpt",
    "openai",
    "claude",
    "anthropic",
    "gemini",
    "google",
    "deepmind",
    "microsoft",
    "copilot",
    "apple",
    "iphone",
    "ipad",
    "macbook",
    "samsung",
    "galaxy",
    "meta",
    "nvidia",
    "amd",
    "intel",
    "tesla",
    "amazon",
    "aws",
    "azure",
    "android",
    "ios",
    "technology",
    "tech",
    "developer",
    "coding",
    "programming",
    "github",
    "cloud computing",
    "cloud",
    "data center",
    "gpu",
    "cpu",
    "graphics card",
    "processor",
    "api",
    "open source",
    "robotics",
    "neural network",
    "inference",
    "cybersecurity",
    "software",
    "hardware",
    "robot",
    "robotics",
    "chip",
    "semiconductor",
    "gaming",
    "playstation",
    "xbox",
    "nintendo",
    "esports",
    "football",
    "soccer",
    "cricket",
    "nba",
    "nfl",
    "nhl",
    "mlb",
    "formula 1",
    "f1",
    "tennis",
    "wimbledon",
    "ufc",
    "boxing",
    "olympics",
    "champions league",
    "premier league",
    "world cup",
    "sports",
    "sport",
    "nike",
    "adidas",
    "spotify",
    "music",
    "netflix",
    "entertainment",
    "design",
)

BLOCKED_TOPIC_KEYWORDS = (
    "election",
    "elections",
    "president",
    "presidential",
    "prime minister",
    "parliament",
    "congress",
    "senate",
    "government",
    "governor",
    "minister",
    "politician",
    "politics",
    "political",
    "vote",
    "voting",
    "ballot",
    "campaign",
    "democrat",
    "republican",
    "labour",
    "conservative party",
    "liberal party",
    "maga",
    "war",
    "military",
    "geopolitics",
)


class TrendSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceTweet:
    tweet_id: str
    text: str
    username: str
    created_at: datetime
    trend: str
    trend_volume: int
    view_count: int
    favorite_count: int
    retweet_count: int
    reply_count: int
    author_followers: int
    author_verified: bool
    author_professional: bool
    source_type: str = "x_top_search"

    @property
    def url(self) -> str:
        return f"https://x.com/{self.username}/status/{self.tweet_id}"

    @property
    def engagement_score(self) -> tuple[float, float, float]:
        # Favor live conversation while giving established professional accounts
        # a meaningful but not overwhelming discovery advantage.
        score = (
            1.0 * math.log1p(self.view_count)
            + 1.2 * math.log1p(self.favorite_count)
            + 2.2 * math.log1p(self.retweet_count)
            + 3.0 * math.log1p(self.reply_count)
            + 1.35 * math.log1p(self.author_followers)
            + (1.75 if self.author_verified else 0.0)
            + (2.0 if self.author_professional else 0.0)
        )
        conversation = math.log1p(
            self.reply_count + (2 * self.retweet_count) + self.favorite_count
        )
        authority = math.log1p(self.author_followers) + (
            2.0 if self.author_professional else 0.0
        )
        return (score, conversation, self.created_at.timestamp())


def _as_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _tweet_id(tweet: object) -> str:
    value = getattr(tweet, "id", None)
    return str(value or "").strip()


def _tweet_datetime(tweet: object) -> datetime | None:
    value = getattr(tweet, "created_at_datetime", None)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    raw = str(getattr(tweet, "created_at", "") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)



PROFESSIONAL_PROFILE_KEYWORDS = (
    "founder", "cofounder", "co-founder", "ceo", "cto", "chief", "operator",
    "investor", "venture", "vc", "engineer", "developer", "researcher",
    "scientist", "designer", "product", "startup", "entrepreneur", "builder",
    "analyst", "journalist", "writer", "coach", "athlete", "official",
)


def _author_profile(tweet: object) -> tuple[int, bool, bool]:
    user = getattr(tweet, "user", None)
    followers = _as_int(
        getattr(user, "followers_count", None)
        or getattr(user, "followers", None)
    )
    verified = bool(
        getattr(user, "verified", False)
        or getattr(user, "is_verified", False)
        or getattr(user, "is_blue_verified", False)
    )
    bio = str(
        getattr(user, "description", None)
        or getattr(user, "bio", None)
        or ""
    ).lower()
    normalized = " ".join(
        "".join(ch.lower() if ch.isalnum() else " " for ch in bio).split()
    )
    professional = any(
        _keyword_matches(normalized, keyword)
        for keyword in PROFESSIONAL_PROFILE_KEYWORDS
    )
    return followers, verified, professional

def _username(tweet: object) -> str:
    user = getattr(tweet, "user", None)
    return str(getattr(user, "screen_name", "") or "").lstrip("@").strip()


def _keyword_matches(text: str, keyword: str) -> bool:
    words = [
        "".join(ch.lower() if ch.isalnum() else " " for ch in text).split(),
        "".join(ch.lower() if ch.isalnum() else " " for ch in keyword).split(),
    ]
    haystack, needle = words
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(haystack[i : i + width] == needle for i in range(len(haystack) - width + 1))


def _topic_allowed(trend: str, text: str) -> bool:
    haystack = f"{trend} {text}"
    if any(_keyword_matches(haystack, keyword) for keyword in BLOCKED_TOPIC_KEYWORDS):
        return False
    return any(_keyword_matches(haystack, keyword) for keyword in ALLOWED_TOPIC_KEYWORDS)


def _is_candidate(
    tweet: object,
    now: datetime,
    used_ids: set[str],
    trend_name: str,
    require_views: bool = True,
) -> bool:
    tweet_id = _tweet_id(tweet)
    if not tweet_id or tweet_id in used_ids:
        return False

    text = str(getattr(tweet, "text", "") or "").strip()
    if not text or not _topic_allowed(trend_name, text):
        return False

    if getattr(tweet, "in_reply_to", None):
        return False
    if getattr(tweet, "retweeted_tweet", None):
        return False
    if getattr(tweet, "is_quote_status", False):
        return False
    if getattr(tweet, "possibly_sensitive", False):
        return False

    language = str(getattr(tweet, "lang", "") or "").lower().strip()
    if language and language != "en":
        return False

    created_at = _tweet_datetime(tweet)
    if created_at is None:
        return False

    age = now - created_at.astimezone(timezone.utc)
    if age < timedelta(minutes=-5) or age > MAX_TWEET_AGE:
        return False

    if require_views and _as_int(getattr(tweet, "view_count", 0)) <= 0:
        return False

    if not _username(tweet):
        return False

    return True


def _build_source(
    tweet: object,
    trend_name: str,
    trend_volume: int,
    source_type: str = "x_top_search",
) -> SourceTweet:
    created_at = _tweet_datetime(tweet)
    if created_at is None:
        raise TrendSourceError("Tweet did not contain a usable creation time.")

    author_followers, author_verified, author_professional = _author_profile(tweet)

    return SourceTweet(
        tweet_id=_tweet_id(tweet),
        text=str(getattr(tweet, "text", "") or "").strip(),
        username=_username(tweet),
        created_at=created_at.astimezone(timezone.utc),
        trend=trend_name,
        trend_volume=max(0, trend_volume),
        view_count=_as_int(getattr(tweet, "view_count", 0)),
        favorite_count=_as_int(getattr(tweet, "favorite_count", 0)),
        retweet_count=_as_int(getattr(tweet, "retweet_count", 0)),
        reply_count=_as_int(getattr(tweet, "reply_count", 0)),
        author_followers=author_followers,
        author_verified=author_verified,
        author_professional=author_professional,
        source_type=source_type,
    )


async def find_trending_source(
    client: Client,
    used_source_ids: Iterable[str],
    excluded_trend_names: Iterable[str] = (),
) -> SourceTweet:
    """Find an allowed recent X conversation, preferring eligible official Trends topics."""
    used_ids = {str(value).strip() for value in used_source_ids if str(value).strip()}
    excluded_names = {" ".join(str(value).casefold().split()) for value in excluded_trend_names if str(value).strip()}
    now = datetime.now(timezone.utc)

    trend_queries: list[tuple[str, int, str]] = []
    get_trends = getattr(client, "get_trends", None)
    if callable(get_trends):
        try:
            live_trends = await get_trends("trending", count=50, retry=False)
            for item in live_trends or []:
                name = str(getattr(item, "name", "") or "").strip()
                if (
                    not name
                    or " ".join(name.casefold().split()) in excluded_names
                    or not _topic_allowed(name, name)
                ):
                    continue
                volume = _as_int(getattr(item, "tweets_count", 0))
                trend_queries.append((name, volume, "x_trend"))
        except Exception as exc:
            # Twikit/X occasionally returns no trend payload. Fall back to live topical searches.
            print(f"Official X Trends unavailable; using recent topical X searches: {exc}")

    rotation = int(now.timestamp() // 3600) % len(SIGNAL_SEARCHES)
    available_fallbacks = [
        SIGNAL_SEARCHES[(rotation + offset) % len(SIGNAL_SEARCHES)]
        for offset in range(len(SIGNAL_SEARCHES))
        if " ".join(SIGNAL_SEARCHES[(rotation + offset) % len(SIGNAL_SEARCHES)].casefold().split())
        not in excluded_names
    ]
    if not available_fallbacks:
        available_fallbacks = list(SIGNAL_SEARCHES)
    fallback_queries = [
        (query, 0, "x_top_search")
        for query in available_fallbacks[:SEARCHES_PER_RUN]
    ]
    batches: list[list[tuple[str, int, str]]] = []
    if trend_queries:
        batches.append(trend_queries[:SEARCHES_PER_RUN])
    batches.append(fallback_queries)

    for batch_index, queries in enumerate(batches):
        candidates: dict[str, SourceTweet] = {}
        for query, volume, source_type in queries:
            try:
                results = await client.search_tweet(
                    query,
                    "Top",
                    count=TWEETS_PER_SEARCH,
                )
            except Exception as exc:
                message = str(exc)
                print(f"Skipping search {query!r}: {message}")
                if "429" in message or "rate limit" in message.lower():
                    raise TrendSourceError(
                        "X search is currently rate limited; stopping before more requests."
                    ) from exc
                continue

            for tweet in results:
                if not _is_candidate(tweet, now, used_ids, query):
                    continue
                source = _build_source(tweet, query, volume, source_type)
                candidates[source.tweet_id] = source

        if not candidates:
            continue

        ranked = sorted(
            candidates.values(),
            key=lambda candidate: candidate.engagement_score,
            reverse=True,
        )
        selected = ranked[0]
        print(
            "Selected source for manual trend draft: "
            f"@{selected.username} / {selected.tweet_id} | "
            f"source_type={selected.source_type} | topic={selected.trend!r} | "
            f"views={selected.view_count:,} | likes={selected.favorite_count:,} | "
            f"RTs={selected.retweet_count:,} | replies={selected.reply_count:,} | "
            f"followers={selected.author_followers:,} | "
            f"verified={selected.author_verified} | "
            f"professional={selected.author_professional}"
        )
        return selected

    raise TrendSourceError(
        "No eligible recent AI, technology, gaming, sports, or major-company "
        "conversation was found in official allowed trends or current topical searches."
    )

# A curated set of first-party company accounts. The quote-post lane searches
# these accounts directly instead of reading or targeting X's Trending Topics.
OFFICIAL_BRAND_SOURCE_ACCOUNTS = (
    ("OpenAI", "OpenAI AI software"),
    ("AnthropicAI", "Anthropic Claude AI software"),
    ("Google", "Google AI Android technology"),
    ("Microsoft", "Microsoft AI Windows Xbox software"),
    ("Apple", "Apple iPhone software technology"),
    ("NVIDIA", "NVIDIA GPU AI technology"),
    ("AMD", "AMD GPU technology"),
    ("Intel", "Intel hardware technology"),
    ("Meta", "Meta AI technology"),
    ("Sony", "Sony PlayStation gaming technology"),
    ("NintendoAmerica", "Nintendo gaming"),
    ("Xbox", "Xbox gaming technology"),
    ("PlayStation", "PlayStation gaming technology"),
    ("Steam", "Steam gaming"),
    ("EpicGames", "Epic Games gaming technology"),
    ("Adobe", "Adobe design software"),
    ("Canva", "Canva design software"),
    ("Figma", "Figma design software"),
    ("NotionHQ", "Notion productivity software"),
    ("GitHub", "GitHub developer software"),
    ("vercel", "Vercel developer technology"),
    ("Cloudflare", "Cloudflare software technology"),
    ("SlackHQ", "Slack workplace software"),
    ("Discord", "Discord gaming software"),
    ("Spotify", "Spotify music technology"),
    ("Netflix", "Netflix entertainment technology"),
    ("Nike", "Nike sports products"),
    ("adidas", "adidas sports products"),
)


async def find_official_brand_source(
    client: Client,
    used_source_ids: Iterable[str],
    excluded_handles: Iterable[str] = (),
) -> SourceTweet:
    """Select a recent post authored by a curated official brand account.

    It intentionally does not query X's Trends endpoint. Posts are quote-posted
    only when they are recent, original, English, topic-relevant, and authored
    by the exact first-party handle from the allowlist.
    """
    import random

    used_ids = {str(value).strip() for value in used_source_ids if str(value).strip()}
    excluded = {str(value).strip().lstrip("@").casefold() for value in excluded_handles if str(value).strip()}
    eligible = [
        item for item in OFFICIAL_BRAND_SOURCE_ACCOUNTS
        if item[0].casefold() not in excluded
    ]
    if not eligible:
        raise TrendSourceError("All official brand source accounts have already been used today.")

    random.shuffle(eligible)
    now = datetime.now(timezone.utc)
    candidates: dict[str, SourceTweet] = {}
    attempted = 0

    for handle, topic in eligible:
        if attempted >= OFFICIAL_BRAND_SEARCHES_PER_RUN:
            break
        attempted += 1
        try:
            results = await client.search_tweet(f"from:{handle}", "Latest", count=TWEETS_PER_SEARCH)
        except Exception as exc:
            message = str(exc)
            print(f"Skipping official-brand source search for @{handle}: {message}")
            if "429" in message or "rate limit" in message.lower():
                raise TrendSourceError(
                    "X search is currently rate limited; stopping before more requests."
                ) from exc
            continue

        for tweet in results or []:
            if _username(tweet).casefold() != handle.casefold():
                continue
            # Preserve the account text-only requirement even when the source tweet embeds media.
            if getattr(tweet, "media", None):
                continue
            if not _is_candidate(tweet, now, used_ids, topic, require_views=False):
                continue
            source = _build_source(
                tweet,
                trend_name=f"{topic} — @{handle}",
                trend_volume=0,
                source_type="official_brand_post",
            )
            candidates[source.tweet_id] = source

    if not candidates:
        raise TrendSourceError(
            "No eligible recent post was found from the approved first-party brand accounts."
        )

    ranked = sorted(
        candidates.values(),
        key=lambda candidate: candidate.engagement_score,
        reverse=True,
    )
    selected = ranked[0]
    print(
        "Selected official brand source for automatic quote post: "
        f"@{selected.username}/{selected.tweet_id} | "
        f"topic={selected.trend!r} | views={selected.view_count:,} | "
        f"likes={selected.favorite_count:,} | replies={selected.reply_count:,}"
    )
    return selected



# A source must contain a concrete AI/technology signal in its own text;
# broad legacy topic keywords (sports, entertainment, etc.) are insufficient.
AI_TECH_TOPIC_KEYWORDS = (
    "artificial intelligence", "machine learning", "large language model",
    "foundation model", "chatgpt", "openai", "claude", "anthropic", "gemini",
    "deepmind", "llm", "llms", "copilot", "ai", "software", "developer tools",
    "coding", "programming", "github", "cloud computing", "cloud", "data center",
    "gpu", "cpu", "graphics card", "processor", "semiconductor", "chip",
    "api", "open source", "robotics", "neural network", "inference",
    "cybersecurity", "cyber security", "microsoft", "nvidia", "amd", "intel",
    "apple", "google", "meta", "aws", "azure", "android", "iphone", "macbook",
    "software engineering", "database", "python", "javascript", "linux",
)


def _ai_tech_topic_allowed(text: str) -> bool:
    if not text or any(_keyword_matches(text, term) for term in BLOCKED_TOPIC_KEYWORDS):
        return False
    return any(_keyword_matches(text, term) for term in AI_TECH_TOPIC_KEYWORDS)


# This lane discovers text-only AI/technology conversation through direct
# keyword searches. It intentionally never reads X's Trending Topics endpoint.
AI_TECH_SIGNAL_SEARCHES = (
    "ChatGPT",
    "OpenAI AI",
    "Claude AI",
    "Gemini AI",
    "AI agents",
    "AI coding",
    "LLM",
    "machine learning",
    "AI model",
    "artificial intelligence",
    "NVIDIA AI",
    "GPU computing",
    "developer tools",
    "coding assistant",
    "open source AI",
    "AI infrastructure",
    "robotics software",
    "cloud computing",
)


async def find_ai_tech_source(
    client: Client,
    used_source_ids: Iterable[str],
    excluded_usernames: Iterable[str] = (),
) -> SourceTweet:
    """Find a recent, original, text-only AI/technology post for a quote-post."""
    import random

    used_ids = {str(value).strip() for value in used_source_ids if str(value).strip()}
    excluded = {
        str(value).strip().lstrip("@").casefold()
        for value in excluded_usernames if str(value).strip()
    }
    queries = list(AI_TECH_SIGNAL_SEARCHES)
    random.shuffle(queries)
    now = datetime.now(timezone.utc)
    candidates: dict[str, SourceTweet] = {}

    for query in queries[:AI_TECH_SEARCHES_PER_RUN]:
        try:
            results = await client.search_tweet(query, "Latest", count=TWEETS_PER_SEARCH)
        except Exception as exc:
            message = str(exc)
            print(f"Skipping AI/tech search {query!r}: {message}")
            if "429" in message or "rate limit" in message.lower():
                raise TrendSourceError(
                    "X search is currently rate limited; stopping before more requests."
                ) from exc
            continue

        for tweet in results or []:
            if _username(tweet).casefold() in excluded:
                continue
            # The authored quote commentary is text-only; avoid sourcing an image/video post.
            if getattr(tweet, "media", None):
                continue
            text = str(getattr(tweet, "text", "") or "").strip()
            if not _ai_tech_topic_allowed(text):
                continue
            if not _is_candidate(tweet, now, used_ids, query, require_views=False):
                continue
            source = _build_source(
                tweet,
                trend_name=f"AI/technology: {query}",
                trend_volume=0,
                source_type="ai_tech_search",
            )
            candidates[source.tweet_id] = source

    if not candidates:
        raise TrendSourceError(
            "No eligible recent text-only AI/technology post was found in direct keyword searches."
        )

    ranked = sorted(
        candidates.values(),
        key=lambda candidate: candidate.engagement_score,
        reverse=True,
    )
    selected = ranked[0]
    print(
        "Selected AI/technology source for automatic quote-post: "
        f"@{selected.username}/{selected.tweet_id} | "
        f"query={selected.trend!r} | views={selected.view_count:,} | "
        f"likes={selected.favorite_count:,} | replies={selected.reply_count:,}"
    )
    return selected
