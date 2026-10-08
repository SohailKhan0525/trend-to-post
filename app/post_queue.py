from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from twikit import Client

from .brand_targets import DAILY_BRAND_TARGETS, ensure_daily_brand_targets
from .gemini import GeminiError, generate_company_original, generate_quote
from .trend_source import (
    SOURCE_HISTORY_LIMIT,
    SourceTweet,
    TrendSourceError,
    find_trending_source,
)

STATE_PATH = Path("state/post_queue.json")
POSTS_PER_DAY = 20
COMPANY_POSTS_PER_DAY = DAILY_BRAND_TARGETS
QUOTE_POSTS_PER_DAY = POSTS_PER_DAY - COMPANY_POSTS_PER_DAY
AI_GENERATIONS_PER_DAY = 20
MIN_POST_INTERVAL_MINUTES = 72
BRAND_AI_GENERATIONS_PER_DAY = COMPANY_POSTS_PER_DAY


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
        "company_post_count": 0,
        "company_posted_handles": [],
        "last_posted_at": None,
        "last_post_type": None,
        "last_company_handle": None,
        "last_source_tweet_id": None,
        "last_tweet_id": None,
        "posted_source_tweet_ids": [],
        "skipped_source_tweet_ids": [],
        "brand_tag_day_key": None,
        "brand_tag_queue": [],
        "brand_tag_cooldowns": {},
        "brand_draft_day_key": None,
        "brand_draft_queue": [],
        "brand_ai_call_count": 0,
        "brand_format_lab": [],
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
        state["company_post_count"] = 0
        state["company_posted_handles"] = []
        state["last_post_type"] = None
        state["last_company_handle"] = None
        state["brand_draft_day_key"] = None
        state["brand_draft_queue"] = []
        state["brand_ai_call_count"] = 0
        state["brand_format_lab"] = []

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
    try:
        state["company_post_count"] = int(state.get("company_post_count", 0))
    except (TypeError, ValueError) as exc:
        raise QueueError("State company_post_count must be an integer.") from exc
    if not 0 <= state["company_post_count"] <= COMPANY_POSTS_PER_DAY:
        raise QueueError(
            f"State company_post_count is outside 0..{COMPANY_POSTS_PER_DAY}."
        )
    company_handles = state.get("company_posted_handles", [])
    if not isinstance(company_handles, list):
        raise QueueError("State company_posted_handles must be a list.")
    state["company_posted_handles"] = [
        str(handle).strip() for handle in company_handles if str(handle).strip()
    ][:COMPANY_POSTS_PER_DAY]

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

    brand_drafts = state.get("brand_draft_queue", [])
    if not isinstance(brand_drafts, list):
        raise QueueError("State brand_draft_queue must be a list.")
    state["brand_draft_queue"] = [
        item for item in brand_drafts[-DAILY_BRAND_TARGETS:]
        if isinstance(item, dict)
    ]

    try:
        state["brand_ai_call_count"] = int(state.get("brand_ai_call_count", 0))
    except (TypeError, ValueError) as exc:
        raise QueueError("State brand_ai_call_count must be an integer.") from exc
    if not 0 <= state["brand_ai_call_count"] <= BRAND_AI_GENERATIONS_PER_DAY:
        raise QueueError(
            "State brand_ai_call_count is outside "
            f"0..{BRAND_AI_GENERATIONS_PER_DAY}."
        )

    brand_lab = state.get("brand_format_lab", [])
    if not isinstance(brand_lab, list):
        raise QueueError("State brand_format_lab must be a list.")
    state["brand_format_lab"] = [
        item for item in brand_lab[-20:]
        if isinstance(item, dict)
    ]
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


def _next_company_target(state: dict) -> dict | None:
    posted = {
        str(handle).strip()
        for handle in state.get("company_posted_handles", [])
        if str(handle).strip()
    }
    for item in state.get("brand_tag_queue", []):
        if not isinstance(item, dict):
            continue
        handle = str(item.get("handle", "")).strip()
        name = str(item.get("name", "")).strip()
        if handle and name and handle not in posted:
            return item
    return None


def _company_slot_due(state: dict) -> bool:
    next_number = int(state["daily_count"]) + 1
    if next_number % 4 == 0:
        return True
    expected_company_posts = min(
        COMPANY_POSTS_PER_DAY,
        next_number // 4,
    )
    return int(state["company_post_count"]) < expected_company_posts


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

    if ensure_daily_brand_targets(state):
        _save_state(state)
        targets = ", ".join(
            f"{item['handle']} ({item['name']})"
            for item in state.get("brand_tag_queue", [])
            if isinstance(item, dict)
        )
        print(
            "Brand target queue refreshed: "
            f"{targets}. Five company slots are reserved inside the daily 20-post quota; "
            "unsolicited automated @mentions are disabled."
        )

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

    if _company_slot_due(state) and state["company_post_count"] < COMPANY_POSTS_PER_DAY:
        target = _next_company_target(state)
        if target is None:
            raise QueueError(
                "No unused company target is available for the next reserved company slot."
            )

        target_handle = str(target["handle"]).strip()
        target_name = str(target["name"]).strip()
        state["ai_call_count"] += 1
        state["brand_ai_call_count"] = min(
            COMPANY_POSTS_PER_DAY,
            int(state.get("brand_ai_call_count", 0)) + 1,
        )
        _save_state(state)

        try:
            draft = generate_company_original(
                target,
                state["brand_format_lab"],
                include_handle=False,
            )
        except GeminiError as exc:
            target["status"] = "generation_failed"
            target["last_error"] = str(exc)
            _save_state(state)
            print(
                "Company AI could not produce a policy-safe candidate; "
                f"target will retry on the next company slot: {target_name} ({target_handle})."
            )
            return False

        if not draft.should_post:
            target["status"] = "generation_rejected"
            _save_state(state)
            print(f"Company AI rejected target; retrying later: {target_name}.")
            return False

        text = draft.post_text.strip()
        if not text or len(text) > 280:
            raise QueueError("AI returned invalid company post text length.")

        print(f"Company post text ({len(text)}/280): {text}")
        print(
            f"Company target: {target_name} ({target_handle}); "
            "automated @mention disabled."
        )

        try:
            tweet = await client.create_tweet(text=text)
        except Exception as exc:
            raise QueueError(f"X company post failed: {exc}") from exc

        now = datetime.now(timezone.utc).isoformat()
        lab = state["brand_format_lab"]
        lab.append(
            {
                "format_name": draft.format_name or "unnamed company format",
                "comedy_mechanism": draft.comedy_mechanism,
                "hook_type": draft.hook_type,
                "structure_signature": draft.structure_signature,
                "used_at": now,
                "target": target_handle,
                "tweet_id": str(getattr(tweet, "id", "") or ""),
            }
        )
        state["brand_format_lab"] = lab[-20:]

        fingerprints = state["recent_post_fingerprints"]
        fingerprints.append(draft.structure_signature[:180])
        state["recent_post_fingerprints"] = fingerprints[-30:]

        handles = state["company_posted_handles"]
        handles.append(target_handle)
        state["company_posted_handles"] = handles[-COMPANY_POSTS_PER_DAY:]
        target["status"] = "auto_published"
        target["posted_at"] = now
        target["posted_tweet_id"] = str(getattr(tweet, "id", "") or "")
        target.pop("last_error", None)

        state.update(
            {
                "day_key": _today(),
                "daily_count": int(state["daily_count"]) + 1,
                "company_post_count": int(state["company_post_count"]) + 1,
                "last_tweet_id": str(getattr(tweet, "id", "") or ""),
                "last_posted_at": now,
                "last_post_type": "company_original",
                "last_company_handle": target_handle,
            }
        )
        _save_state(state)

        print(
            f"Posted company_original #{state['company_post_count']}/{COMPANY_POSTS_PER_DAY}; "
            f"total posts {state['daily_count']}/{POSTS_PER_DAY}; "
            f"AI generations {state['ai_call_count']}/{AI_GENERATIONS_PER_DAY}."
        )
        return True

    used = state["posted_source_tweet_ids"] + state["skipped_source_tweet_ids"]

    try:
        source = await find_trending_source(client, used)
        payload = _source_dict(source)

        # Both company originals and quote posts consume the same application-level
        # AI budget so the daily 20-call ceiling cannot be bypassed by the brand lane.
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
    print(f"Quote-post source: {source.url}")

    try:
        tweet = await client.create_tweet(text=text, attachment_url=source.url)
    except Exception as exc:
        raise QueueError(f"X post failed: {exc}") from exc

    now = datetime.now(timezone.utc).isoformat()
    lab = state["format_lab"]
    lab.append(
        {
            "format_name": draft.format_name or "unnamed format",
            "comedy_mechanism": draft.comedy_mechanism,
            "used_at": now,
            "topic": source.trend,
            "self_reply": False,
            "tweet_id": str(getattr(tweet, "id", "") or ""),
            "content_type": "quote_post",
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
            "last_tweet_id": str(getattr(tweet, "id", "") or ""),
            "last_posted_at": now,
            "last_source_tweet_id": source.tweet_id,
            "last_post_type": "quote_post",
            "last_company_handle": None,
        }
    )
    _save_state(state)

    quote_count = state["daily_count"] - state["company_post_count"]
    print(
        f"Posted quote_post #{quote_count}/{QUOTE_POSTS_PER_DAY}; "
        f"company posts {state['company_post_count']}/{COMPANY_POSTS_PER_DAY}; "
        f"total posts {state['daily_count']}/{POSTS_PER_DAY}; "
        f"AI generations {state['ai_call_count']}/{AI_GENERATIONS_PER_DAY}. "
        f"Format={draft.format_name or 'unnamed'}"
    )
    return True
