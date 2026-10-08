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
POSTS_PER_DAY = 20
AI_GENERATIONS_PER_DAY = 20
MIN_POST_INTERVAL_MINUTES = 72


class QueueError(RuntimeError):
    pass


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _new_state() -> dict:
    return {
        "format_lab": [],
        "recent_post_fingerprints": [],
        "day_key": _today(),
        "daily_count": 0,
        "ai_call_count": 0,
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
        state["ai_call_count"] = 0

    try:
        state["daily_count"] = int(state.get("daily_count", 0))
        state["ai_call_count"] = int(state.get("ai_call_count", 0))
    except (TypeError, ValueError) as exc:
        raise QueueError("State counters must be integers.") from exc

    if not 0 <= state["daily_count"] <= POSTS_PER_DAY:
        raise QueueError(f"State daily_count is outside 0..{POSTS_PER_DAY}.")
    if not 0 <= state["ai_call_count"] <= AI_GENERATIONS_PER_DAY:
        raise QueueError(
            f"State ai_call_count is outside 0..{AI_GENERATIONS_PER_DAY}."
        )

    state["posted_source_tweet_ids"] = _history(
        state.get("posted_source_tweet_ids", [])
    )
    fingerprints = state.get("recent_post_fingerprints", [])
    if not isinstance(fingerprints, list):
        raise QueueError("State recent_post_fingerprints must be a list.")
    state["recent_post_fingerprints"] = [
        str(item).strip() for item in fingerprints[-30:] if str(item).strip()
    ]

    lab = state.get("format_lab", [])
    if not isinstance(lab, list):
        raise QueueError("State format_lab must be a list.")
    state["format_lab"] = [
        item for item in lab[-20:]
        if isinstance(item, dict)
    ]
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
        "author_followers": source.author_followers,
        "author_verified": source.author_verified,
        "author_professional": source.author_professional,
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
        os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        and os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    ):
        raise QueueError(
            "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required."
        )

    state = _load_state()

    if state["daily_count"] >= POSTS_PER_DAY:
        print(f"Daily post limit reached: {POSTS_PER_DAY}.")
        return False

    if state["ai_call_count"] >= AI_GENERATIONS_PER_DAY:
        print(f"Daily AI generation limit reached: {AI_GENERATIONS_PER_DAY}.")
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

        # Cloudflare is primary; Gemini 3.5 Flash-Lite is the fallback.
        # Consume the application-level generation budget before calling a provider
        # so repeated provider failures cannot bypass the daily guard.
        state["ai_call_count"] += 1
        _save_state(state)
        try:
            draft = generate_quote(payload, state["format_lab"])
        except GeminiError as exc:
            skipped = state["skipped_source_tweet_ids"]
            skipped.append(source.tweet_id)
            state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
            _save_state(state)
            print(
                "AI could not produce a policy-safe candidate after retries; "
                f"source skipped safely: {exc}"
            )
            return False
    except TrendSourceError as exc:
        raise QueueError(str(exc)) from exc

    if not draft.should_quote:
        skipped = state["skipped_source_tweet_ids"]
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print("AI rejected the source; it is skipped on later runs.")
        return False

    text = draft.quote_text.strip()
    if not text or len(text) > 280:
        raise QueueError("AI returned invalid post text length.")

    print(f"Post text ({len(text)}/280): {text}")
    if draft.content_type == "quote_post":
        print(f"Quote-post source: {source.url}")

    try:
        if draft.content_type == "quote_post":
            tweet = await client.create_tweet(text=text, attachment_url=source.url)
        else:
            tweet = await client.create_tweet(text=text)
    except Exception as exc:
        raise QueueError(f"X post failed: {exc}") from exc

    # Quote-post-only mode: never spend daily capacity on an automated self-reply.
    self_reply_posted = False
    self_reply_id = ""

    lab = state["format_lab"]
    lab.append(
        {
            "format_name": draft.format_name or "unnamed format",
            "comedy_mechanism": draft.comedy_mechanism,
            "used_at": datetime.now(timezone.utc).isoformat(),
            "topic": source.trend,
            "self_reply": self_reply_posted,
            "tweet_id": str(getattr(tweet, "id", "") or ""),
            "content_type": draft.content_type,
            "mutation_stage": draft.mutation_stage,
            "structure_signature": draft.structure_signature,
        }
    )
    state["format_lab"] = lab[-20:]

    fingerprints = state["recent_post_fingerprints"]
    fingerprints.append(draft.structure_signature[:180])
    state["recent_post_fingerprints"] = fingerprints[-30:]

    posted = state["posted_source_tweet_ids"]
    posted.append(source.tweet_id)
    state["posted_source_tweet_ids"] = posted[-SOURCE_HISTORY_LIMIT:]
    state.update(
        {
            "day_key": _today(),
            "daily_count": int(state["daily_count"]) + 1,
            "last_tweet_id": self_reply_id or str(getattr(tweet, "id", "") or ""),
            "last_posted_at": datetime.now(timezone.utc).isoformat(),
            "last_source_tweet_id": source.tweet_id,
        }
    )
    _save_state(state)

    print(
        f"Posted quote_post #{state['daily_count']}/{POSTS_PER_DAY}; "
        f"AI generations {state['ai_call_count']}/{AI_GENERATIONS_PER_DAY}."
        f" Format={draft.format_name or 'unnamed'}"
    )
    return True
