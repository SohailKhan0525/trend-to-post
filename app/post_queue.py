from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from twikit import Client

from .gemini import GeminiError, generate_quote
from .trend_source import (
    SOURCE_HISTORY_LIMIT,
    SourceTweet,
    TrendSourceError,
    find_trending_source,
)

STATE_PATH = Path("state/post_queue.json")
POSTS_PER_DAY = 10
GEMINI_CALLS_PER_DAY = 10
MIN_POST_INTERVAL_MINUTES = 144


class QueueError(RuntimeError):
    pass


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _new_state() -> dict:
    return {
        "day_key": _today(),
        "daily_count": 0,
        "gemini_call_count": 0,
        "last_posted_at": None,
        "last_source_tweet_id": None,
        "last_tweet_id": None,
        "posted_source_tweet_ids": [],
        "skipped_source_tweet_ids": [],
    }


def _history(value: object) -> list[str]:
    if not isinstance(value, list):
        raise QueueError("Source history must be a list.")
    return [str(x).strip() for x in value if str(x).strip()][-SOURCE_HISTORY_LIMIT:]


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return _new_state()

    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QueueError(f"Invalid state JSON: {exc}") from exc

    if not isinstance(state, dict):
        raise QueueError("State must be a JSON object.")

    today = _today()
    if str(state.get("day_key", "")).strip() != today:
        state["day_key"] = today
        state["daily_count"] = 0
        state["gemini_call_count"] = 0

    try:
        state["daily_count"] = int(state.get("daily_count", 0))
        state["gemini_call_count"] = int(state.get("gemini_call_count", 0))
    except (TypeError, ValueError) as exc:
        raise QueueError("State counters must be integers.") from exc

    if not 0 <= state["daily_count"] <= POSTS_PER_DAY:
        raise QueueError(f"State daily_count is outside 0..{POSTS_PER_DAY}.")
    if not 0 <= state["gemini_call_count"] <= GEMINI_CALLS_PER_DAY:
        raise QueueError(
            f"State gemini_call_count is outside 0..{GEMINI_CALLS_PER_DAY}."
        )

    state["posted_source_tweet_ids"] = _history(
        state.get("posted_source_tweet_ids", [])
    )
    state["skipped_source_tweet_ids"] = _history(
        state.get("skipped_source_tweet_ids", [])
    )
    return state


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(STATE_PATH)


def _client(auth_token: str, ct0: str) -> Client:
    client = Client("en-US", impersonate="chrome124")
    client.set_cookies({"auth_token": auth_token, "ct0": ct0})
    return client


def _source_dict(source: SourceTweet) -> dict:
    return {
        "tweet_id": source.tweet_id,
        "text": source.text,
        "username": source.username,
        "created_at": source.created_at.isoformat(),
        "url": source.url,
        "trend": source.trend,
        "trend_volume": source.trend_volume,
        "view_count": source.view_count,
        "favorite_count": source.favorite_count,
        "retweet_count": source.retweet_count,
        "reply_count": source.reply_count,
    }


def _interval_ok(state: dict) -> bool:
    value = state.get("last_posted_at")
    if not value:
        return True

    try:
        last = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise QueueError(f"Invalid last_posted_at: {value}") from exc

    remaining = MIN_POST_INTERVAL_MINUTES * 60 - int(
        (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds()
    )
    if remaining > 0:
        print(
            "Post interval guard: next post allowed in about "
            f"{(remaining + 59) // 60} minute(s)."
        )
        return False
    return True


async def post_next(
    auth_token: str,
    ct0: str,
    gemini_api_key: str | None = None,
) -> bool:
    if not auth_token or not ct0:
        raise QueueError("X_AUTH_TOKEN and X_CT0 are required.")

    if not (
        (gemini_api_key or "").strip()
        or os.environ.get("GEMINI_API_KEY_BACKUP", "").strip()
    ):
        raise QueueError(
            "GEMINI_API_KEY or GEMINI_API_KEY_BACKUP is required."
        )

    state = _load_state()

    # Hard application-level cap: never create more than 10 posts in one UTC day.
    if state["daily_count"] >= POSTS_PER_DAY:
        print(f"Daily post limit reached: {POSTS_PER_DAY}.")
        return False

    # Keep Gemini traffic bounded as well. With one Gemini call per candidate,
    # this prevents skipped candidates from turning into an unbounded quota drain.
    if state["gemini_call_count"] >= GEMINI_CALLS_PER_DAY:
        print(f"Daily Gemini request limit reached: {GEMINI_CALLS_PER_DAY}.")
        return False

    if not _interval_ok(state):
        return False

    client = _client(auth_token, ct0)
    if not await client.is_logged_in():
        raise QueueError("X session is not logged in; auth cookies may be expired.")

    used = state["posted_source_tweet_ids"] + state["skipped_source_tweet_ids"]

    try:
        source = await find_trending_source(client, used)
        payload = _source_dict(source)

        # One Gemini request does both grounding/research and quote drafting.
        # Count successful Gemini calls so repeated workflow dispatches cannot
        # exhaust the provider quota by processing unlimited candidates.
        draft = generate_quote(payload)
        state["gemini_call_count"] += 1
        _save_state(state)
    except (TrendSourceError, GeminiError) as exc:
        raise QueueError(str(exc)) from exc

    if not draft.should_quote:
        skipped = state["skipped_source_tweet_ids"]
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print("Gemini rejected the source; it is skipped on later runs.")
        return False

    text = draft.quote_text.strip()
    if not text or len(text) > 280:
        raise QueueError("Gemini returned invalid quote-post text length.")

    print(f"Quote text ({len(text)}/280): {text}")
    print(f"Quote-post source: {source.url}")

    try:
        tweet = await client.create_tweet(text=text, attachment_url=source.url)
    except Exception as exc:
        # The Gemini call remains counted, but daily_count is only incremented
        # after X confirms the post. This avoids claiming a post that did not happen.
        raise QueueError(f"X post failed: {exc}") from exc

    posted = state["posted_source_tweet_ids"]
    posted.append(source.tweet_id)
    state["posted_source_tweet_ids"] = posted[-SOURCE_HISTORY_LIMIT:]
    state.update(
        {
            "day_key": _today(),
            "daily_count": int(state["daily_count"]) + 1,
            "last_posted_at": datetime.now(timezone.utc).isoformat(),
            "last_source_tweet_id": source.tweet_id,
            "last_tweet_id": str(getattr(tweet, "id", "") or ""),
        }
    )
    _save_state(state)

    print(
        f"Posted quote #{state['daily_count']}/{POSTS_PER_DAY}; "
        f"Gemini calls {state['gemini_call_count']}/{GEMINI_CALLS_PER_DAY}."
    )
    return True
