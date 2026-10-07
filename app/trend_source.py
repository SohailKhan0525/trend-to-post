from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Iterable

from twikit import Client

SEARCHES_PER_RUN = 5
TWEETS_PER_SEARCH = 15
MAX_TWEET_AGE = timedelta(hours=18)
SOURCE_HISTORY_LIMIT = 100

# These are discovery queries, not trend targets. They deliberately cover
# technical, product, builder, and sports conversations where established
# professional accounts are likely to participate.
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


# Keep the bot focused on the requested subjects. A trend must contain at least
# one allowed signal, while political/current-affairs signals are explicitly rejected.
ALLOWED_TOPIC_KEYWORDS = (
    "artificial intelligence",
    "ai",
    "machine learning",
    "llm",
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


def _is_candidate(tweet: object, now: datetime, used_ids: set[str], trend_name: str) -> bool:
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

    if _as_int(getattr(tweet, "view_count", 0)) <= 0:
        return False

    if not _username(tweet):
        return False

    return True


def _build_source(tweet: object, trend_name: str, trend_volume: int) -> SourceTweet:
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
    )


async def find_trending_source(client: Client, used_source_ids: Iterable[str]) -> SourceTweet:
    used_ids = {str(value).strip() for value in used_source_ids if str(value).strip()}
    now = datetime.now(timezone.utc)

    # X's automation rules prohibit automatically posting because a topic is
    # trending. Use a rotating set of live subject searches instead and optimize
    # for high-signal conversations that can earn organic distribution.
    rotation = int(now.timestamp() // 3600) % len(SIGNAL_SEARCHES)
    queries = [
        SIGNAL_SEARCHES[(rotation + offset) % len(SIGNAL_SEARCHES)]
        for offset in range(SEARCHES_PER_RUN)
    ]

    candidates: dict[str, SourceTweet] = {}
    for query in queries:
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
            source = _build_source(tweet, query, 0)
            candidates[source.tweet_id] = source

    if not candidates:
        raise TrendSourceError(
            "No eligible recent AI, technology, gaming, sports, or major-company "
            "conversation was found in the current discovery window."
        )

    ranked = sorted(
        candidates.values(),
        key=lambda candidate: candidate.engagement_score,
        reverse=True,
    )
    selected = ranked[0]

    print(
        "Selected high-signal source: "
        f"@{selected.username} / {selected.tweet_id} | "
        f"query={selected.trend!r} | views={selected.view_count:,} | "
        f"likes={selected.favorite_count:,} | RTs={selected.retweet_count:,} | "
        f"replies={selected.reply_count:,} | "
        f"followers={selected.author_followers:,} | "
        f"verified={selected.author_verified} | "
        f"professional={selected.author_professional}"
    )
    return selected

