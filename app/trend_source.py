from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from twikit import Client

TREND_COUNT = 8
TWEETS_PER_SEARCH = 20
MAX_TWEET_AGE = timedelta(hours=24)
SOURCE_HISTORY_LIMIT = 100

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

    @property
    def url(self) -> str:
        return f"https://x.com/{self.username}/status/{self.tweet_id}"

    @property
    def engagement_score(self) -> tuple[int, int, int, int, float]:
        return (
            self.view_count,
            self.favorite_count,
            self.retweet_count,
            self.reply_count,
            self.created_at.timestamp(),
        )


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


def _username(tweet: object) -> str:
    user = getattr(tweet, "user", None)
    return str(getattr(user, "screen_name", "") or "").lstrip("@").strip()


def _topic_allowed(trend: str, text: str) -> bool:
    haystack = f"{trend} {text}".lower()
    if any(keyword in haystack for keyword in BLOCKED_TOPIC_KEYWORDS):
        return False
    return any(keyword in haystack for keyword in ALLOWED_TOPIC_KEYWORDS)


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
    )


async def find_trending_source(client: Client, used_source_ids: Iterable[str]) -> SourceTweet:
    used_ids = {str(value).strip() for value in used_source_ids if str(value).strip()}
    now = datetime.now(timezone.utc)

    try:
        trends = await client.get_trends("trending", count=TREND_COUNT, retry=False)
    except Exception as exc:
        raise TrendSourceError(f"Unable to read X trends: {exc}") from exc

    if not trends:
        raise TrendSourceError("X returned no current trends.")

    candidates: dict[str, SourceTweet] = {}

    for trend in trends[:TREND_COUNT]:
        name = str(getattr(trend, "name", "") or "").strip()
        if not name or not _topic_allowed(name, ""):
            continue
        trend_volume = _as_int(getattr(trend, "tweets_count", 0))

        for product in ("Top", "Latest"):
            try:
                results = await client.search_tweet(
                    name,
                    product,
                    count=TWEETS_PER_SEARCH,
                )
            except Exception as exc:
                print(f"Skipping trend {name!r} ({product}): {exc}")
                continue

            for tweet in results:
                if not _is_candidate(tweet, now, used_ids, name):
                    continue
                source = _build_source(tweet, name, trend_volume)
                candidates[source.tweet_id] = source

    if not candidates:
        raise TrendSourceError(
            "No eligible recent AI, technology, sports, or major-company post was found."
        )

    ranked = sorted(
        candidates.values(),
        key=lambda candidate: candidate.engagement_score,
        reverse=True,
    )
    selected = ranked[0]

    print(
        "Selected source: "
        f"@{selected.username} / {selected.tweet_id} | "
        f"trend={selected.trend!r} | views={selected.view_count:,} | "
        f"likes={selected.favorite_count:,} | RTs={selected.retweet_count:,} | "
        f"replies={selected.reply_count:,}"
    )
    return selected
