from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from twikit import Client

from .gemini import GeminiError, research_source, write_quote
from .trend_source import (
    SOURCE_HISTORY_LIMIT,
    SourceTweet,
    TrendSourceError,
    find_trending_source,
)

STATE_PATH = Path("state/post_queue.json")
POSTS_PER_DAY = 10
MIN_POST_INTERVAL = 144  # minutes; external cron can poll more often.


class QueueError(RuntimeError):
    pass


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _new_state(day_key: str) -> dict:
    return {
        "day_key": day_key,
        "daily_count": 0,
        "last_posted_at": None,
        "last_source_tweet_id": None,
        "last_tweet_id": None,
        "posted_source_tweet_ids": [],
        "skipped_source_tweet_ids": [],
    }


def _clean_history(value: object) -> list[str]:
    if not isinstance(value, list):
        raise QueueError("Source history must be a list.")
    return [
        str(item).strip()
        for item in value
        if str(item).strip()
    ][-SOURCE_HISTORY_LIMIT:]


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return _new_state(_today_key())

    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QueueError(f"State file is invalid JSON: {exc}") from exc

    if not isinstance(state, dict):
        raise QueueError("State file must be a JSON object.")

    # The old fixed-queue/image system is intentionally discarded. Any old
    # next_index state starts the new trend-based state cleanly.
    if "next_index" in state:
        return _new_state(_today_key())

    day_key = str(state.get("day_key", "")).strip() or _today_key()
    if day_key != _today_key():
        state["day_key"] = _today_key()
        state["daily_count"] = 0

    try:
        state["daily_count"] = int(state.get("daily_count", 0))
    except (TypeError, ValueError) as exc:
        raise QueueError("State daily_count must be an integer.") from exc

    if not 0 <= state["daily_count"] <= POSTS_PER_DAY:
        raise QueueError("State daily_count is outside the allowed 0..10 range.")

    state["posted_source_tweet_ids"] = _clean_history(
        state.get("posted_source_tweet_ids", [])
    )
    state["skipped_source_tweet_ids"] = _clean_history(
        state.get("skipped_source_tweet_ids", [])
    )
    return state


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = STATE_PATH.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "
",
        encoding="utf-8",
    )
    temp_path.replace(STATE_PATH)


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


def _validate_interval(state: dict) -> bool:
    last_posted_at = state.get("last_posted_at")
    if not last_posted_at:
        return True

    try:
        last_posted = datetime.fromisoformat(str(last_posted_at))
    except ValueError as exc:
        raise QueueError(f"Invalid last_posted_at in state: {last_posted_at}") from exc

    elapsed = datetime.now(timezone.utc) - last_posted.astimezone(timezone.utc)
    remaining = MIN_POST_INTERVAL * 60 - int(elapsed.total_seconds())
    if remaining > 0:
        minutes = (remaining + 59) // 60
        print(f"Post interval guard: next post allowed in about {minutes} minute(s).")
        return False
    return True


async def post_next(
    auth_token: str,
    ct0: str,
    gemini_api_key: str | None = None,
) -> bool:
    if not auth_token or not ct0:
        raise QueueError("X_AUTH_TOKEN and X_CT0 are required.")
    if not (gemini_api_key or "").strip():
        raise QueueError("GEMINI_API_KEY is required.")

    state = _load_state()
    if state["daily_count"] >= POSTS_PER_DAY:
        print(
            f"Daily limit reached: {POSTS_PER_DAY} quote-posts have been "
            "published today."
        )
        return False

    if not _validate_interval(state):
        return False

    client = _client(auth_token, ct0)
    if not await client.is_logged_in():
        raise QueueError("X session is not logged in; auth_token/ct0 may be expired.")

    used_source_ids = (
        state["posted_source_tweet_ids"] + state["skipped_source_tweet_ids"]
    )
    try:
        source = await find_trending_source(client, used_source_ids)
    except TrendSourceError as exc:
        raise QueueError(str(exc)) from exc

    source_payload = _source_dict(source)
    print(f"Researching source post with Gemini: {source.url}")
    try:
        research = research_source(source_payload)
        draft = write_quote(source_payload, research)
    except GeminiError as exc:
        raise QueueError(str(exc)) from exc

    if not draft.should_quote:
        skipped = state["skipped_source_tweet_ids"]
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print(
            "Gemini rejected this source for quote-posting; trying a different "
            "source next run."
        )
        return False

    quote_text = draft.quote_text.strip()
    if not quote_text or len(quote_text) > 280:
        raise QueueError("Gemini returned invalid quote-post text length.")

    print(f"Gemini angle: {draft.angle}")
    print(f"Quote text ({len(quote_text)}/280): {quote_text}")
    print(f"Quote-posting source: {source.url}")

    # Twikit documents attachment_url as the URL of the tweet to be quoted.
    tweet = await client.create_tweet(
        text=quote_text,
        attachment_url=source.url,
    )
    tweet_id = str(getattr(tweet, "id", "") or "")

    posted = state["posted_source_tweet_ids"]
    posted.append(source.tweet_id)
    state["posted_source_tweet_ids"] = posted[-SOURCE_HISTORY_LIMIT:]

    now = datetime.now(timezone.utc)
    state.update(
        {
            "day_key": _today_key(),
            "daily_count": int(state.get("daily_count", 0)) + 1,
            "last_posted_at": now.isoformat(),
            "last_source_tweet_id": source.tweet_id,
            "last_tweet_id": tweet_id,
        }
    )
    _save_state(state)

    print(
        f"Posted quote #{state['daily_count']}/{POSTS_PER_DAY}"
        + (f" as tweet {tweet_id}." if tweet_id else ".")
    )
    return True
