from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from twikit import Client

from .brand_targets import DAILY_BRAND_TARGETS, ensure_daily_brand_targets
from .gemini import GeminiError, generate_company_original, generate_original_meme
from .meme_image import render_meme_image
from .trend_source import (
    SOURCE_HISTORY_LIMIT,
    SourceTweet,
    TrendSourceError,
    find_trending_source,
)

STATE_PATH = Path("state/post_queue.json")
POSTS_PER_DAY = 20
COMPANY_POSTS_PER_DAY = DAILY_BRAND_TARGETS
ORIGINAL_POSTS_PER_DAY = POSTS_PER_DAY - COMPANY_POSTS_PER_DAY
AI_GENERATIONS_PER_DAY = 20
IMAGE_GENERATIONS_PER_DAY = POSTS_PER_DAY
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
        "image_call_count": 0,
        "company_post_count": 0,
        "company_posted_handles": [],
        "company_manual_posts": [],
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
        state["image_call_count"] = 0
        state["company_post_count"] = 0
        state["company_posted_handles"] = []
        state["company_manual_posts"] = []
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
        state["image_call_count"] = int(state.get("image_call_count", 0))
    except (TypeError, ValueError) as exc:
        raise QueueError("State image_call_count must be an integer.") from exc
    if not 0 <= state["image_call_count"] <= IMAGE_GENERATIONS_PER_DAY:
        raise QueueError(
            f"State image_call_count is outside 0..{IMAGE_GENERATIONS_PER_DAY}."
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

    manual_posts = state.get("company_manual_posts", [])
    if not isinstance(manual_posts, list):
        raise QueueError("State company_manual_posts must be a list.")
    state["company_manual_posts"] = [
        item for item in manual_posts[-COMPANY_POSTS_PER_DAY:]
        if isinstance(item, dict)
    ]

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


COMPANY_MANUAL_POSTS_PATH = Path("state/company_manual_posts.md")


def _write_company_manual_posts(state: dict) -> None:
    posts = state.get("company_manual_posts", [])
    lines = [
        "# Company Posts — Manual",
        "",
        f"UTC date: {state.get('day_key', _today())}",
        "",
        "These five company posts are included in the same 20-content daily quota.",
        "Copy the post text exactly, including the @mention, and publish it manually on X.",
        "Matching PNG image files are in state/generated/company/ and are included in the workflow artifact.",
        "",
    ]

    if not posts:
        lines.append("No company posts generated yet.")
    else:
        for item in posts:
            slot = item.get("slot_number", "")
            name = str(item.get("name", "")).strip()
            handle = str(item.get("handle", "")).strip()
            post = str(item.get("post", "")).strip()
            lines.extend(
                [
                    f"## Slot {slot} — {name} {handle}".strip(),
                    "",
                    f"> {post}",
                    "",
                    f"Format: {item.get('format_name', '')}",
                    f"Hook: {item.get('hook_type', '')}",
                    f"Mechanism: {item.get('comedy_mechanism', '')}",
                    f"Status: {item.get('status', 'ready_for_manual_post')}",
                    "",
                ]
            )

    COMPANY_MANUAL_POSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = COMPANY_MANUAL_POSTS_PATH.with_suffix(".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    tmp.replace(COMPANY_MANUAL_POSTS_PATH)


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


def _ensure_company_manual_posts(state: dict) -> bool:
    """Prepare up to five tagged company originals and matching images before auto-posting."""
    while (
        int(state.get("company_post_count", 0)) < COMPANY_POSTS_PER_DAY
        and int(state.get("daily_count", 0)) < POSTS_PER_DAY
    ):
        if int(state.get("ai_call_count", 0)) >= AI_GENERATIONS_PER_DAY:
            print("Text-generation budget reached before all company assets were prepared.")
            return False
        if int(state.get("image_call_count", 0)) >= IMAGE_GENERATIONS_PER_DAY:
            print("Image-generation budget reached before all company assets were prepared.")
            return False

        target = _next_company_target(state)
        if target is None:
            print("No unused company target remains for today's manual meme assets.")
            return False

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
                include_handle=True,
            )
        except GeminiError as exc:
            target["status"] = "generation_failed"
            target["last_error"] = str(exc)
            _save_state(state)
            print(f"Company AI failed for {target_name} ({target_handle}): {exc}")
            return False

        if not draft.should_post:
            target["status"] = "generation_rejected"
            _save_state(state)
            print(f"Company AI rejected target; it will retry later: {target_name}.")
            return False

        text = draft.post_text.strip()
        if not text or len(text) > 280:
            target["status"] = "generation_rejected"
            target["last_error"] = "Invalid company caption length."
            _save_state(state)
            return False

        image_path = (
            Path("state/generated/company")
            / f"{_today()}_{target_handle.lstrip('@').lower()}.png"
        )
        state["image_call_count"] += 1
        _save_state(state)
        try:
            render_meme_image(
                draft.conversation,
                draft.image_prompt,
                target_name,
                image_path,
            )
        except Exception as exc:
            target["status"] = "image_render_failed"
            target["last_error"] = str(exc)
            _save_state(state)
            print(f"Company meme image failed for {target_name}: {exc}")
            return False

        now = datetime.now(timezone.utc).isoformat()
        company_slot = int(state.get("company_post_count", 0)) + 1
        manual_item = {
            "slot_number": company_slot,
            "handle": target_handle,
            "name": target_name,
            "post": text,
            "angle": draft.angle,
            "hook_type": draft.hook_type,
            "format_name": draft.format_name,
            "comedy_mechanism": draft.comedy_mechanism,
            "structure_signature": draft.structure_signature,
            "image_path": str(image_path),
            "generated_at": now,
            "status": "ready_for_manual_post",
        }

        manual_posts = state["company_manual_posts"]
        manual_posts.append(manual_item)
        state["company_manual_posts"] = manual_posts[-COMPANY_POSTS_PER_DAY:]

        lab = state["brand_format_lab"]
        lab.append(
            {
                "format_name": draft.format_name or "unnamed company format",
                "comedy_mechanism": draft.comedy_mechanism,
                "hook_type": draft.hook_type,
                "structure_signature": draft.structure_signature,
                "used_at": now,
                "target": target_handle,
                "manual": True,
            }
        )
        state["brand_format_lab"] = lab[-20:]

        fingerprints = state["recent_post_fingerprints"]
        fingerprints.append(draft.structure_signature[:180])
        state["recent_post_fingerprints"] = fingerprints[-30:]

        handles = state["company_posted_handles"]
        handles.append(target_handle)
        state["company_posted_handles"] = handles[-COMPANY_POSTS_PER_DAY:]
        target["status"] = "manual_ready"
        target["generated_at"] = now
        target.pop("last_error", None)

        state.update(
            {
                "day_key": _today(),
                "daily_count": int(state["daily_count"]) + 1,
                "company_post_count": int(state["company_post_count"]) + 1,
                "last_post_type": "company_manual",
                "last_company_handle": target_handle,
            }
        )
        _write_company_manual_posts(state)
        _save_state(state)
        print(
            f"Prepared company asset {state['company_post_count']}/{COMPANY_POSTS_PER_DAY}: "
            f"{target_name} ({target_handle})."
        )

    return (
        int(state.get("company_post_count", 0)) >= COMPANY_POSTS_PER_DAY
        or int(state.get("daily_count", 0)) >= POSTS_PER_DAY
    )


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

    _write_company_manual_posts(state)

    if ensure_daily_brand_targets(state):
        _save_state(state)
        targets = ", ".join(
            f"{item['handle']} ({item['name']})"
            for item in state.get("brand_tag_queue", [])
            if isinstance(item, dict)
        )
        print(
            "Brand target queue refreshed: "
            f"{targets}. Five company assets will be prepared together inside the 20-slot quota; "
            "their @mentions are for your manual posts only."
        )

    if state["company_post_count"] < COMPANY_POSTS_PER_DAY:
        if not _ensure_company_manual_posts(state):
            _write_company_manual_posts(state)
            _save_state(state)
            return False

    if state["daily_count"] >= POSTS_PER_DAY:
        print(f"Daily post limit reached: {POSTS_PER_DAY}.")
        return False

    if state["ai_call_count"] >= AI_GENERATIONS_PER_DAY:
        print(f"Daily AI generation limit reached: {AI_GENERATIONS_PER_DAY}.")
        return False

    if state["image_call_count"] >= IMAGE_GENERATIONS_PER_DAY:
        print(f"Daily image generation limit reached: {IMAGE_GENERATIONS_PER_DAY}.")
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

        # Each daily content slot uses one text generation and one image-generation attempt.
        state["ai_call_count"] += 1
        _save_state(state)
        try:
            draft = generate_original_meme(payload, state["format_lab"])
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

    if not draft.should_post:
        skipped = state["skipped_source_tweet_ids"]
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print("AI rejected the topic seed; it is skipped on later runs.")
        return False

    text = draft.post_text.strip()
    if not text or len(text) > 280:
        raise QueueError("AI returned invalid original post text length.")

    image_path = Path("state/generated/automatic/last_original_meme.png")
    state["image_call_count"] += 1
    _save_state(state)
    try:
        render_meme_image(
            draft.conversation,
            draft.image_prompt,
            draft.target_name or source.trend,
            image_path,
        )
    except Exception as exc:
        skipped = state["skipped_source_tweet_ids"]
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print(f"Could not render original meme image; topic seed skipped safely: {exc}")
        return False

    print(f"Original post ({len(text)}/280): {text}")
    print(f"Meme image ready: {image_path}")

    try:
        media_id = await client.upload_media(
            str(image_path),
            wait_for_completion=True,
        )
        tweet = await client.create_tweet(text=text, media_ids=[media_id])
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
            "content_type": "original_with_image",
            "mutation_stage": draft.hook_type,
            "structure_signature": draft.structure_signature,
            "image_path": str(image_path),
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
            "last_post_type": "original_with_image",
            "last_company_handle": None,
        }
    )
    _save_state(state)

    original_count = state["daily_count"] - state["company_post_count"]
    print(
        f"Posted original_with_image #{original_count}/{ORIGINAL_POSTS_PER_DAY}; "
        f"company manual assets {state['company_post_count']}/{COMPANY_POSTS_PER_DAY}; "
        f"total content slots {state['daily_count']}/{POSTS_PER_DAY}; "
        f"text generations {state['ai_call_count']}/{AI_GENERATIONS_PER_DAY}; "
        f"image attempts {state['image_call_count']}/{IMAGE_GENERATIONS_PER_DAY}. "
        f"Format={draft.format_name or 'unnamed'}"
    )
    return True
