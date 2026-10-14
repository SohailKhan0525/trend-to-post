from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from twikit import Client

from .brand_targets import DAILY_BRAND_TARGETS, ensure_daily_brand_targets
from .gemini import COMPANY_HOOK_TYPES, GeminiError, generate_company_original, generate_original_text, generate_quote
from .generated_images import GeneratedImageError, generate_comparison_image
from .person_images import (
    PERSON_SUBJECTS,
    PersonImageError,
    attribution_reply,
    download_image_bytes,
    find_licensed_person_image,
)
from .trend_source import (
    SOURCE_HISTORY_LIMIT,
    SourceTweet,
    TrendSourceError,
    find_ai_tech_source,
    find_official_brand_source,
    find_trending_source,
    _ai_tech_topic_allowed,
)

STATE_PATH = Path("state/post_queue.json")
POSTS_PER_DAY = 20
COMPANY_POSTS_PER_DAY = DAILY_BRAND_TARGETS
TREND_QUOTE_POSTS_PER_DAY = 12
PERSON_PHOTO_POSTS_PER_DAY = 8
AI_TECH_QUOTE_POSTS_PER_DAY = 0
# Allow retries across flaky source searches/provider responses without changing
# the hard cap of twenty successfully published main posts.
AI_GENERATIONS_PER_DAY = 40
ORIGINAL_POSTS_PER_DAY = 11  # legacy state compatibility only
# The external dispatcher and scheduled fallback both run every 30 minutes.
# A 58-minute guard allows one successful post per hour despite event overlap,
# leaving enough time to complete the 20-slot daily plan without duplicate posts.
MIN_POST_INTERVAL_MINUTES = 58


def _set_workflow_output(name: str, value: str) -> None:
    """Write a safe scalar output when running inside GitHub Actions."""
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"{name}={value}\n")
BRAND_AI_GENERATIONS_PER_DAY = COMPANY_POSTS_PER_DAY
INVENTED_TOPIC_SEEDS = (
    "AI and strange software behaviour",
    "developer tools and coding rituals",
    "consumer technology and device habits",
    "gaming and game-design absurdities",
    "sports culture and ridiculous fan logic",
    "startups, builders and founder life",
    "internet culture and digital etiquette",
    "AI assistants and impossible product features",
)


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
        "trend_quote_post_count": 0,
        "trend_quote_posts": [],
        "trend_quote_day_key": None,
        "trend_manual_post_count": 0,  # legacy state compatibility only
        "trend_manual_posts": [],
        "trend_manual_day_key": None,
        "trend_source_ids": [],
        "invented_post_count": 0,  # legacy state compatibility only
        "person_photo_day_key": None,
        "person_photo_post_count": 0,
        "person_photo_posts": [],
        "mention_post_timestamps": [],
        "person_photo_title_history": [],
        "person_photo_subject_history": [],
        "skipped_person_image_titles": [],
        "ai_tech_quote_day_key": None,
        "ai_tech_quote_post_count": 0,
        "ai_tech_quote_posts": [],
        "ai_tech_source_ids": [],
        "next_lane_index": 0,
    }



def _active_mention_posts(state: dict, now: datetime | None = None) -> list[dict]:
    """Return mention-post records still inside the rolling 48-hour limit."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    active: list[dict] = []
    for item in state.get("mention_post_timestamps", []):
        if not isinstance(item, dict):
            continue
        try:
            timestamp = datetime.fromisoformat(str(item.get("timestamp", "")))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            age = current.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if age < timedelta(hours=48):
            active.append(item)
    return active

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
        state["company_manual_posts"] = []
        state["last_post_type"] = None
        state["last_company_handle"] = None
        state["brand_draft_day_key"] = None
        state["brand_draft_queue"] = []
        state["brand_ai_call_count"] = 0
        state["brand_format_lab"] = []
        state["trend_quote_post_count"] = 0
        state["trend_quote_posts"] = []
        state["trend_quote_day_key"] = None
        state["trend_manual_post_count"] = 0
        state["trend_manual_posts"] = []
        state["trend_manual_day_key"] = None
        state["trend_source_ids"] = []
        state["trend_names_used"] = []
        state["invented_post_count"] = 0
        state["person_photo_day_key"] = None
        state["person_photo_post_count"] = 0
        state["person_photo_posts"] = []
        state["ai_tech_quote_day_key"] = None
        state["ai_tech_quote_post_count"] = 0
        state["ai_tech_quote_posts"] = []
        state["ai_tech_source_ids"] = []
        state["ai_tech_trend_names_used"] = []
        state["skipped_person_image_titles"] = []
        state["next_lane_index"] = 0

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

    try:
        state["trend_quote_post_count"] = int(state.get("trend_quote_post_count", 0))
        state["invented_post_count"] = int(state.get("invented_post_count", 0))
    except (TypeError, ValueError) as exc:
        raise QueueError("Trend-quote and invented post counters must be integers.") from exc
    if not 0 <= state["trend_quote_post_count"] <= TREND_QUOTE_POSTS_PER_DAY:
        raise QueueError(
            f"State trend_quote_post_count is outside 0..{TREND_QUOTE_POSTS_PER_DAY}."
        )
    quote_posts = state.get("trend_quote_posts", [])
    if not isinstance(quote_posts, list):
        raise QueueError("State trend_quote_posts must be a list.")
    state["trend_quote_posts"] = [
        item for item in quote_posts[-TREND_QUOTE_POSTS_PER_DAY:]
        if isinstance(item, dict)
    ]
    state["trend_source_ids"] = _history(state.get("trend_source_ids", []))
    state["person_photo_title_history"] = _history(
        state.get("person_photo_title_history", [])
    )
    state["person_photo_subject_history"] = _history(
        state.get("person_photo_subject_history", [])
    )
    state["skipped_person_image_titles"] = _history(
        state.get("skipped_person_image_titles", [])
    )
    state["ai_tech_source_ids"] = _history(state.get("ai_tech_source_ids", []))
    try:
        state["person_photo_post_count"] = int(state.get("person_photo_post_count", 0))
        state["ai_tech_quote_post_count"] = int(state.get("ai_tech_quote_post_count", 0))
        state["next_lane_index"] = int(state.get("next_lane_index", 0)) % 3
    except (TypeError, ValueError) as exc:
        raise QueueError("Visual/AI-tech lane counters must be integers.") from exc
    if not 0 <= state["person_photo_post_count"] <= PERSON_PHOTO_POSTS_PER_DAY:
        raise QueueError(
            f"State person_photo_post_count is outside 0..{PERSON_PHOTO_POSTS_PER_DAY}."
        )
    if not 0 <= state["ai_tech_quote_post_count"] <= AI_TECH_QUOTE_POSTS_PER_DAY:
        raise QueueError(
            f"State ai_tech_quote_post_count is outside 0..{AI_TECH_QUOTE_POSTS_PER_DAY}."
        )
    photo_posts = state.get("person_photo_posts", [])
    if not isinstance(photo_posts, list):
        raise QueueError("State person_photo_posts must be a list.")
    state["person_photo_posts"] = [
        item for item in photo_posts[-PERSON_PHOTO_POSTS_PER_DAY:]
        if isinstance(item, dict)
    ]
    ai_tech_posts = state.get("ai_tech_quote_posts", [])
    if not isinstance(ai_tech_posts, list):
        raise QueueError("State ai_tech_quote_posts must be a list.")
    state["ai_tech_quote_posts"] = [
        item for item in ai_tech_posts[-AI_TECH_QUOTE_POSTS_PER_DAY:]
        if isinstance(item, dict)
    ]

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
        "source_type": source.source_type,
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
        "Copy the text exactly, including the @mention and emoji, and publish it manually on X.",
        "Text only: no images or generated chat screenshots.",
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
    """Prepare up to five tagged text-only company originals before auto-posting."""
    while (
        int(state.get("company_post_count", 0)) < COMPANY_POSTS_PER_DAY
        and int(state.get("daily_count", 0)) < POSTS_PER_DAY
    ):
        if int(state.get("ai_call_count", 0)) >= AI_GENERATIONS_PER_DAY:
            print("Text-generation budget reached before all company assets were prepared.")
            return False
        target = _next_company_target(state)
        if target is None:
            print("No unused company target remains for today's manual company captions.")
            return False

        target_handle = str(target["handle"]).strip()
        target_name = str(target["name"]).strip()

        state["ai_call_count"] += 1
        state["brand_ai_call_count"] = min(
            COMPANY_POSTS_PER_DAY,
            int(state.get("brand_ai_call_count", 0)) + 1,
        )
        _save_state(state)

        used_hooks = {
            str(item.get("hook_type", "")).strip()
            for item in state.get("company_manual_posts", [])
            if isinstance(item, dict)
        }
        available_hooks = [
            item for item in COMPANY_HOOK_TYPES
            if item not in used_hooks
        ]
        if not available_hooks:
            available_hooks = list(COMPANY_HOOK_TYPES)
        hook_type = random.choice(available_hooks)

        try:
            draft = generate_company_original(
                target,
                state["brand_format_lab"],
                include_handle=True,
                hook_type=hook_type,
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
                "post_text": text,
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
            f"Prepared company caption {state['company_post_count']}/{COMPANY_POSTS_PER_DAY}: "
            f"{target_name} ({target_handle})."
        )

    return (
        int(state.get("company_post_count", 0)) >= COMPANY_POSTS_PER_DAY
        or int(state.get("daily_count", 0)) >= POSTS_PER_DAY
    )


async def _post_trend_quote(state: dict, client: Client) -> bool:
    """Publish an original meme-style quote-post sourced from X Trends."""
    today = _today()
    if state.get("trend_quote_day_key") != today:
        state["trend_quote_day_key"] = today
        state["trend_quote_post_count"] = 0
        state["trend_quote_posts"] = []
        state["trend_source_ids"] = []
        state["trend_names_used"] = []

    if int(state.get("trend_quote_post_count", 0)) >= TREND_QUOTE_POSTS_PER_DAY:
        return False
    if int(state.get("daily_count", 0)) >= POSTS_PER_DAY:
        print("Daily content-slot limit reached before the next brand quote-post.")
        return False
    if int(state.get("ai_call_count", 0)) >= AI_GENERATIONS_PER_DAY:
        print("Daily AI generation limit reached before the next brand quote-post.")
        return False

    used = (
        state.get("posted_source_tweet_ids", [])
        + state.get("skipped_source_tweet_ids", [])
        + state.get("trend_source_ids", [])
    )
    excluded_handles = [
        str(item.get("source_username", "")).strip()
        for item in state.get("trend_quote_posts", [])
        if isinstance(item, dict) and str(item.get("source_username", "")).strip()
    ]

    try:
        source = await find_trending_source(
            client, used, state.get("trend_names_used", []), ai_tech_only=True
        )
    except TrendSourceError as exc:
        print(f"Cannot find an eligible AI/technology X Trends quote source right now: {exc}")
        return False

    # Defense in depth: never quote a non-tech trend or unrelated source text,
    # even if a future source provider or test double returns an invalid item.
    if (
        source.source_type != "x_trend"
        or not _ai_tech_topic_allowed(source.trend)
        or not _ai_tech_topic_allowed(source.text)
    ):
        skipped = state.get("skipped_source_tweet_ids", [])
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print("Rejected source because it was not an AI/technology post under an actual X Trend.")
        return False

    payload = _source_dict(source)
    state["ai_call_count"] = int(state.get("ai_call_count", 0)) + 1
    _save_state(state)
    try:
        draft = generate_quote(payload, state["format_lab"])
    except GeminiError as exc:
        skipped = state.get("skipped_source_tweet_ids", [])
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print(f"AI/technology X Trends quote generation failed; source safely skipped: {exc}")
        return False

    if not draft.should_quote:
        skipped = state.get("skipped_source_tweet_ids", [])
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print("AI declined the AI/technology X Trends source; no post was published.")
        return False

    text = draft.quote_text.strip()
    if not text or len(text) > 280:
        skipped = state.get("skipped_source_tweet_ids", [])
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print("AI quote exceeded the post limit and was discarded.")
        return False

    print(
        f"Publishing AI/technology X Trends quote-post ({len(text)}/280) for "
        f"{source.trend!r} from @{source.username}: {text}"
    )
    try:
        tweet = await client.create_tweet(text=text, attachment_url=source.url)
    except Exception as exc:
        raise QueueError(f"X Trends quote-post failed: {exc}") from exc

    now = datetime.now(timezone.utc).isoformat()
    item = {
        "slot_number": int(state.get("trend_quote_post_count", 0)) + 1,
        "post": text,
        "source_username": source.username,
        "source_type": source.source_type,
        "source_tweet_id": source.tweet_id,
        "source_url": source.url,
        "source_text": source.text,
        "topic": source.trend,
        "format_name": draft.format_name,
        "comedy_mechanism": draft.comedy_mechanism,
        "structure_signature": draft.structure_signature,
        "tweet_id": str(getattr(tweet, "id", "") or ""),
        "generated_at": now,
        "status": "published",
    }
    used_trend_names = state.get("trend_names_used", [])
    used_trend_names.append(source.trend)
    state["trend_names_used"] = used_trend_names[-SOURCE_HISTORY_LIMIT:]

    state["trend_quote_posts"] = (
        state.get("trend_quote_posts", []) + [item]
    )[-TREND_QUOTE_POSTS_PER_DAY:]

    for key in ("trend_source_ids", "posted_source_tweet_ids"):
        values = state.get(key, [])
        values.append(source.tweet_id)
        state[key] = values[-SOURCE_HISTORY_LIMIT:]

    lab = state.get("format_lab", [])
    lab.append({
        "format_name": draft.format_name or "unnamed AI/technology X Trends quote",
        "comedy_mechanism": draft.comedy_mechanism,
        "used_at": now,
        "topic": source.trend,
        "tweet_id": item["tweet_id"],
        "content_type": "quote_post",
        "content_mode": "x_trends_quote",
        "source_tweet_id": source.tweet_id,
        "source_username": source.username,
        "post_text": text,
        "mutation_stage": draft.mutation_stage,
        "structure_signature": draft.structure_signature,
    })
    state["format_lab"] = lab[-20:]

    fingerprints = state.get("recent_post_fingerprints", [])
    fingerprints.append(draft.structure_signature[:180])
    state["recent_post_fingerprints"] = fingerprints[-30:]

    count = int(state.get("trend_quote_post_count", 0)) + 1
    state.update({
        "day_key": today,
        "trend_quote_day_key": today,
        "trend_quote_post_count": count,
        "daily_count": int(state.get("daily_count", 0)) + 1,
        "last_tweet_id": item["tweet_id"],
        "last_posted_at": now,
        "last_source_tweet_id": source.tweet_id,
        "last_post_type": "x_trends_quote",
        "last_company_handle": None,
    })
    _save_state(state)
    print(
        f"Published X Trends quote-post {count}/{TREND_QUOTE_POSTS_PER_DAY}; "
        f"invented originals {state.get('invented_post_count', 0)}/{ORIGINAL_POSTS_PER_DAY}; "
        f"company captions {state.get('company_post_count', 0)}/{COMPANY_POSTS_PER_DAY}; "
        f"daily slots {state['daily_count']}/{POSTS_PER_DAY}; "
        f"AI generations {state['ai_call_count']}/{AI_GENERATIONS_PER_DAY}."
    )
    return True


async def _find_active_x_person_handles(
    client: Client,
    excluded_subjects: list[str],
    max_active: int = 6,
    max_checks: int = 12,
) -> list[str]:
    """Return handles that currently have discoverable original posts on X."""
    excluded = {str(value).casefold().strip() for value in excluded_subjects}
    people = [
        item for item in PERSON_SUBJECTS
        if item[1].casefold() not in excluded and item[0].casefold() not in excluded
    ]
    random.shuffle(people)
    active: list[str] = []

    for person in people[:max_checks]:
        handle = person[3]
        try:
            results = await client.search_tweet(f"from:{handle}", "Latest", count=5)
        except Exception as exc:
            print(f"Could not verify @{handle} on X for photo lane: {exc}")
            message = str(exc).lower()
            if "429" in message or "rate limit" in message:
                break
            continue

        found = False
        for tweet in results or []:
            user = getattr(tweet, "user", None)
            actual_handle = str(getattr(user, "screen_name", "") or "").lstrip("@").casefold()
            if actual_handle != handle.casefold():
                continue
            if getattr(tweet, "in_reply_to", None) or getattr(tweet, "retweeted_tweet", None):
                continue
            # A live handle is not enough by itself: confirm the profile display
            # name matches the curated public figure so a recycled handle cannot
            # attach the wrong person's name to a photo.
            actual_name = "".join(
                ch.casefold()
                for ch in str(getattr(user, "name", "") or "")
                if ch.isalnum()
            )
            expected_names = (person[0], person[1], *person[4])
            name_matches = False
            for expected in expected_names:
                normalized = "".join(ch.casefold() for ch in expected if ch.isalnum())
                if not normalized:
                    continue
                if normalized == actual_name or (
                    len(normalized) >= 6 and normalized in actual_name
                ) or (
                    len(actual_name) >= 6 and actual_name in normalized
                ):
                    name_matches = True
                    break
            text = str(getattr(tweet, "text", "") or "").strip()
            if text and name_matches:
                found = True
                break
        if found:
            active.append(handle)
            if len(active) >= max_active:
                break

    return active


async def _post_developer_original_text_only(state: dict, client: Client) -> bool:
    """Fallback to a text-only developer prompt when no safe photo is available."""
    today = _today()
    if int(state.get("person_photo_post_count", 0)) >= PERSON_PHOTO_POSTS_PER_DAY:
        return False
    if int(state.get("daily_count", 0)) >= POSTS_PER_DAY:
        return False
    if int(state.get("ai_call_count", 0)) >= AI_GENERATIONS_PER_DAY:
        print("AI generation-attempt budget reached before text-only developer original.")
        return False

    payload = {
        "trend": "developer community",
        "text": "Evergreen questions about software, AI tools, building, and founder life.",
        "mention_handle": "",
    }
    state["ai_call_count"] = int(state.get("ai_call_count", 0)) + 1
    _save_state(state)
    try:
        draft = generate_original_text(
            payload,
            state.get("format_lab", []),
            content_mode="developer_prompt",
        )
    except GeminiError as exc:
        print(f"Text-only developer original generation failed: {exc}")
        return False

    text = draft.post_text.strip()
    if not draft.should_post or not text or len(text) > 180:
        print("Text-only developer original failed validation; no post published.")
        return False

    try:
        tweet = await client.create_tweet(text=text)
    except Exception as exc:
        raise QueueError(f"Text-only developer original post failed: {exc}") from exc

    tweet_id = str(getattr(tweet, "id", "") or "")
    if not tweet_id:
        raise QueueError(
            "X accepted the text-only developer original but returned no tweet ID; inspect the account before retrying."
        )

    now = datetime.now(timezone.utc).isoformat()
    item = {
        "slot_number": int(state.get("person_photo_post_count", 0)) + 1,
        "subject": "developer community",
        "post": text,
        "image_title": "",
        "image_source_url": "",
        "license_name": "",
        "tweet_id": tweet_id,
        "format_name": draft.format_name,
        "comedy_mechanism": draft.comedy_mechanism,
        "structure_signature": draft.structure_signature,
        "generated_at": now,
        "status": "published_text_only",
    }
    state["person_photo_posts"] = (
        state.get("person_photo_posts", []) + [item]
    )[-PERSON_PHOTO_POSTS_PER_DAY:]
    lab = state.get("format_lab", [])
    lab.append({
        "format_name": draft.format_name or "developer community question",
        "comedy_mechanism": draft.comedy_mechanism,
        "used_at": now,
        "topic": "developer community",
        "tweet_id": tweet_id,
        "content_type": "developer_prompt",
        "content_mode": "developer_original_text_only",
        "post_text": text,
        "mutation_stage": draft.hook_type,
        "structure_signature": draft.structure_signature,
    })
    state["format_lab"] = lab[-20:]
    fingerprints = state.get("recent_post_fingerprints", [])
    fingerprints.append(draft.structure_signature[:180])
    state["recent_post_fingerprints"] = fingerprints[-30:]
    state.update({
        "day_key": today,
        "person_photo_day_key": today,
        "person_photo_post_count": int(state.get("person_photo_post_count", 0)) + 1,
        "daily_count": int(state.get("daily_count", 0)) + 1,
        "last_tweet_id": tweet_id,
        "last_posted_at": now,
        "last_source_tweet_id": None,
        "last_post_type": "developer_original",
        "last_company_handle": None,
    })
    _save_state(state)
    print(
        f"Published text-only developer original "
        f"{state['person_photo_post_count']}/{PERSON_PHOTO_POSTS_PER_DAY}; "
        f"daily slots {state['daily_count']}/{POSTS_PER_DAY}."
    )
    return True


async def _post_comparison_meme(state: dict, client: Client) -> bool:
    """Publish a three-part Devs be honest post with an original text-free visual."""
    today = _today()
    if state.get("person_photo_day_key") != today:
        state["person_photo_day_key"] = today
        state["person_photo_post_count"] = 0
        state["person_photo_posts"] = []
    if int(state.get("person_photo_post_count", 0)) >= PERSON_PHOTO_POSTS_PER_DAY:
        return False
    if int(state.get("daily_count", 0)) >= POSTS_PER_DAY:
        return False
    if int(state.get("ai_call_count", 0)) >= AI_GENERATIONS_PER_DAY:
        print("AI generation-attempt budget reached before comparison meme.")
        return False

    payload = {
        "trend": "AI tools, developer tools, coding workflows and technology comparisons",
        "text": "Create a funny comparison between two tools or developer choices: short headline, Devs be honest opener, one natural question. The image is generated separately and must contain no text.",
        "mention_handle": "",
    }
    state["ai_call_count"] = int(state.get("ai_call_count", 0)) + 1
    _save_state(state)
    try:
        draft = generate_original_text(payload, state.get("format_lab", []), content_mode="developer_prompt")
        post_text = draft.post_text.strip()
        if not draft.should_post or not post_text or len(post_text) > 280:
            raise GeminiError("Comparison meme text failed validation.")
        lines = [line.strip() for line in post_text.splitlines() if line.strip()]
        if len(lines) != 2 or post_text.count("\\n\\n") != 1:
            raise GeminiError("Comparison meme must contain exactly two sections separated by one blank line.")
        if not lines[0].endswith("?") or not any(token in lines[0].casefold() for token in (" or ", " vs ", " versus ")):
            raise GeminiError("Comparison headline must compare two clear choices and end with a question mark.")
        if lines[1] != "Devs be honest...":
            raise GeminiError("Comparison opener must be exactly 'Devs be honest...'.")
        if any(mark in post_text for mark in ('"', "'", "“", "”", "@", "#", "http://", "https://")):
            raise GeminiError("Comparison post contains forbidden extra text, quote marks, mentions, tags or links.")
        image_bytes = generate_comparison_image(lines[0], "")
    except (GeminiError, GeneratedImageError) as exc:
        print(f"Comparison meme generation skipped safely: {exc}")
        return False

    try:
        media_id = await client.upload_media(image_bytes, media_type="image/png")
        try:
            await client.create_media_metadata(media_id, alt_text="Original text-free illustration comparing developer tools.")
        except Exception as exc:
            print(f"Could not set comparison image alt text; continuing: {exc}")
        tweet = await client.create_tweet(text=post_text, media_ids=[media_id])
    except Exception as exc:
        raise QueueError(f"Generated comparison meme upload/post failed: {exc}") from exc

    tweet_id = str(getattr(tweet, "id", "") or "")
    if not tweet_id:
        raise QueueError("X accepted the comparison meme but returned no tweet ID; inspect before retrying.")
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "slot_number": int(state.get("person_photo_post_count", 0)) + 1,
        "subject": lines[0], "post": post_text,
        "image_title": "AI-generated text-free comparison illustration",
        "image_source_url": "", "license_name": "Generated by Cloudflare Workers AI",
        "tweet_id": tweet_id, "format_name": draft.format_name,
        "comedy_mechanism": draft.comedy_mechanism,
        "structure_signature": draft.structure_signature, "generated_at": now,
        "status": "published_generated_comparison",
    }
    state["person_photo_posts"] = (state.get("person_photo_posts", []) + [item])[-PERSON_PHOTO_POSTS_PER_DAY:]
    lab = state.get("format_lab", [])
    lab.append({
        "format_name": draft.format_name or "developer comparison meme",
        "comedy_mechanism": draft.comedy_mechanism, "used_at": now,
        "topic": lines[0], "tweet_id": tweet_id,
        "content_type": "developer_comparison_meme",
        "content_mode": "generated_text_free_visual", "post_text": post_text,
        "mutation_stage": draft.hook_type, "structure_signature": draft.structure_signature,
    })
    state["format_lab"] = lab[-20:]
    state["recent_post_fingerprints"] = (state.get("recent_post_fingerprints", []) + [draft.structure_signature[:180]])[-30:]
    state.update({
        "day_key": today, "person_photo_day_key": today,
        "person_photo_post_count": int(state.get("person_photo_post_count", 0)) + 1,
        "daily_count": int(state.get("daily_count", 0)) + 1,
        "last_tweet_id": tweet_id, "last_posted_at": now,
        "last_source_tweet_id": None, "last_post_type": "developer_comparison_meme",
        "last_company_handle": None,
    })
    _save_state(state)
    print(f"Published generated comparison meme {state['person_photo_post_count']}/{PERSON_PHOTO_POSTS_PER_DAY}; daily slots {state['daily_count']}/{POSTS_PER_DAY}.")
    return True


async def _post_person_photo(state: dict, client: Client) -> bool:
    """Publish a source-licensed real-person photo with a varied audience prompt."""
    today = _today()
    if state.get("person_photo_day_key") != today:
        state["person_photo_day_key"] = today
        state["person_photo_post_count"] = 0
        state["person_photo_posts"] = []

    if int(state.get("person_photo_post_count", 0)) >= PERSON_PHOTO_POSTS_PER_DAY:
        return False
    if int(state.get("daily_count", 0)) >= POSTS_PER_DAY:
        return False
    if int(state.get("ai_call_count", 0)) >= AI_GENERATIONS_PER_DAY:
        print("AI generation-attempt budget reached before the next person-photo post.")
        return False

    # This lane is strictly comparison-meme-only. If text/image generation fails,
    # skip this dispatch rather than publishing a mismatched or text-only fallback.
    return await _post_comparison_meme(state, client)

    used_titles = (
        state.get("person_photo_title_history", [])
        + state.get("skipped_person_image_titles", [])
    )
    recent_subjects = (
        state.get("person_photo_subject_history", [])[-16:]
        + [
            str(item.get("subject", "")).strip()
            for item in state.get("person_photo_posts", [])
            if isinstance(item, dict)
        ]
    )
    active_handles = await _find_active_x_person_handles(
        client, recent_subjects, max_active=12, max_checks=24
    )
    if not active_handles:
        print("No verified active X person found for a licensed portrait; falling back to a text-only developer original.")
        return await _post_developer_original_text_only(state, client)

    try:
        image = find_licensed_person_image(
            used_file_titles=used_titles,
            excluded_subjects=recent_subjects,
            eligible_x_handles=active_handles,
        )
        image_bytes = download_image_bytes(image)
    except PersonImageError as exc:
        print(f"Person-photo lane could not find/download a vetted photo for active X people: {exc}")
        failed_title = str(locals().get("image", {}).get("title", "")).strip()
        if failed_title:
            state["skipped_person_image_titles"] = (
                state.get("skipped_person_image_titles", []) + [failed_title]
            )[-SOURCE_HISTORY_LIMIT:]
            _save_state(state)
        return await _post_developer_original_text_only(state, client)

    recent_mentions = _active_mention_posts(state)
    state["mention_post_timestamps"] = recent_mentions[-2:]

    # Mention only the pictured, verified-active account, and at most twice in
    # any rolling 48-hour window. Most posts intentionally have no @mention.
    mention_handle = ""
    if len(recent_mentions) < 2 and random.random() < 0.45:
        candidate_handle = str(image.get("x_handle", "")).strip().lstrip("@")
        if candidate_handle and candidate_handle.casefold() in {h.casefold() for h in active_handles}:
            mention_handle = "@" + candidate_handle

    payload = {
        "trend": str(image.get("subject", "")).strip(),
        "text": str(image.get("description", "")).strip()[:1200],
        "mention_handle": mention_handle,
    }
    state["ai_call_count"] = int(state.get("ai_call_count", 0)) + 1
    _save_state(state)
    try:
        draft = generate_original_text(
            payload,
            state["format_lab"],
            content_mode="developer_prompt",
        )
    except GeminiError as exc:
        state["skipped_person_image_titles"] = (
            state.get("skipped_person_image_titles", []) + [image["title"]]
        )[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print(f"Photo-caption generation failed; trying a text-only developer original: {exc}")
        return await _post_developer_original_text_only(state, client)

    text = draft.post_text.strip()
    if not draft.should_post or not text or len(text) > 280:
        state["skipped_person_image_titles"] = (
            state.get("skipped_person_image_titles", []) + [image["title"]]
        )[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print("Photo-caption validation failed; trying a text-only developer original.")
        return await _post_developer_original_text_only(state, client)

    # Reserve the mention budget before the network publish call. If X accepts
    # the post but the response times out, the next run must still count it.
    if mention_handle:
        mention_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "handle": mention_handle,
            "tweet_id": "pending",
        }
        state["mention_post_timestamps"] = (recent_mentions + [mention_record])[-2:]
        _save_state(state)

    try:
        media_id = await client.upload_media(
            image_bytes,
            media_type=str(image["mime"]),
        )
        try:
            await client.create_media_metadata(
                media_id,
                alt_text=f"Real photo of {image['subject']}. Source: Wikimedia Commons.",
            )
        except Exception as exc:
            # Alt text is helpful but not a prerequisite for publishing the vetted photo.
            print(f"Could not set photo alt text; continuing: {exc}")
        tweet = await client.create_tweet(text=text, media_ids=[media_id])
    except Exception as exc:
        state["skipped_person_image_titles"] = (
            state.get("skipped_person_image_titles", []) + [image["title"]]
        )[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        raise QueueError(f"X person-photo upload or post failed: {exc}") from exc

    tweet_id = str(getattr(tweet, "id", "") or "")
    if not tweet_id:
        raise QueueError(
            "X accepted the photo post but returned no tweet ID; inspect the account before retrying."
        )

    now = datetime.now(timezone.utc).isoformat()
    item = {
        "slot_number": int(state.get("person_photo_post_count", 0)) + 1,
        "subject": image["subject"],
        "post": text,
        "image_title": image["title"],
        "image_source_url": image["file_page_url"],
        "license_name": image["license_name"],
        "license_url": image.get("license_url", ""),
        "image_artist": image.get("artist", ""),
        "needs_attribution": bool(image.get("needs_attribution")),
        "attribution_status": "pending" if image.get("needs_attribution") else "not_required",
        "tweet_id": tweet_id,
        "format_name": draft.format_name,
        "comedy_mechanism": draft.comedy_mechanism,
        "structure_signature": draft.structure_signature,
        "generated_at": now,
        "status": "published",
    }
    state["person_photo_posts"] = (
        state.get("person_photo_posts", []) + [item]
    )[-PERSON_PHOTO_POSTS_PER_DAY:]
    state["person_photo_title_history"] = (
        state.get("person_photo_title_history", []) + [image["title"]]
    )[-SOURCE_HISTORY_LIMIT:]
    state["person_photo_subject_history"] = (
        state.get("person_photo_subject_history", []) + [image["subject"]]
    )[-SOURCE_HISTORY_LIMIT:]

    lab = state.get("format_lab", [])
    lab.append({
        "format_name": draft.format_name or "unnamed person-photo prompt",
        "comedy_mechanism": draft.comedy_mechanism,
        "used_at": now,
        "topic": image["subject"],
        "tweet_id": tweet_id,
        "content_type": "photo_prompt",
        "content_mode": "licensed_person_photo",
        "post_text": text,
        "mutation_stage": draft.hook_type,
        "structure_signature": draft.structure_signature,
    })
    state["format_lab"] = lab[-20:]
    fingerprints = state.get("recent_post_fingerprints", [])
    fingerprints.append(draft.structure_signature[:180])
    state["recent_post_fingerprints"] = fingerprints[-30:]

    if mention_handle:
        for mention_record in reversed(state.get("mention_post_timestamps", [])):
            if mention_record.get("handle") == mention_handle and mention_record.get("tweet_id") == "pending":
                mention_record["tweet_id"] = tweet_id
                break

    state.update({
        "day_key": today,
        "person_photo_day_key": today,
        "person_photo_post_count": int(state.get("person_photo_post_count", 0)) + 1,
        "daily_count": int(state.get("daily_count", 0)) + 1,
        "last_tweet_id": tweet_id,
        "last_posted_at": now,
        "last_source_tweet_id": None,
        "last_post_type": "person_photo_prompt",
        "last_company_handle": None,
    })
    _save_state(state)

    # Attribution is a separate self-reply only for licenses that require it.
    # The parent post and counters are committed first so a reply failure cannot
    # cause the same image/caption to be published again on the next queue run.
    if image.get("needs_attribution"):
        try:
            reply = await client.create_tweet(
                text=attribution_reply(image),
                reply_to=tweet_id,
            )
            item["attribution_reply_id"] = str(getattr(reply, "id", "") or "")
            item["attribution_status"] = "posted"
        except Exception as exc:
            item["attribution_status"] = "failed"
            item["attribution_error"] = str(exc)[:300]
            print(f"Photo is published, but required credit reply failed: {exc}")
        _save_state(state)

    print(
        f"Published licensed real-person photo prompt "
        f"{state['person_photo_post_count']}/{PERSON_PHOTO_POSTS_PER_DAY}: "
        f"{image['subject']} | daily slots {state['daily_count']}/{POSTS_PER_DAY}."
    )
    return True


async def _post_ai_tech_quote(state: dict, client: Client) -> bool:
    """Publish a text-only AI/technology Quote Post with a validated funny comment."""
    today = _today()
    if state.get("ai_tech_quote_day_key") != today:
        state["ai_tech_quote_day_key"] = today
        state["ai_tech_quote_post_count"] = 0
        state["ai_tech_quote_posts"] = []
        state["ai_tech_source_ids"] = []
        state["ai_tech_trend_names_used"] = []

    if int(state.get("ai_tech_quote_post_count", 0)) >= AI_TECH_QUOTE_POSTS_PER_DAY:
        return False
    if int(state.get("daily_count", 0)) >= POSTS_PER_DAY:
        return False
    if int(state.get("ai_call_count", 0)) >= AI_GENERATIONS_PER_DAY:
        print("AI generation-attempt budget reached before the next AI/tech quote-post.")
        return False

    used = (
        state.get("posted_source_tweet_ids", [])
        + state.get("skipped_source_tweet_ids", [])
        + state.get("trend_source_ids", [])
        + state.get("ai_tech_source_ids", [])
    )
    excluded_handles = [
        str(item.get("source_username", "")).strip()
        for key in ("trend_quote_posts", "ai_tech_quote_posts")
        for item in state.get(key, [])
        if isinstance(item, dict) and str(item.get("source_username", "")).strip()
    ]
    try:
        source = await find_trending_source(
            client,
            used,
            state.get("ai_tech_trend_names_used", []),
            ai_tech_only=True,
        )
        if not _ai_tech_topic_allowed(source.text):
            print(f"Skipping X Trends source outside AI/technology scope: {source.trend!r}")
            return False
    except TrendSourceError as exc:
        print(f"No eligible AI/tech quote source found right now: {exc}")
        return False

    payload = _source_dict(source)
    state["ai_call_count"] = int(state.get("ai_call_count", 0)) + 1
    _save_state(state)
    try:
        draft = generate_quote(payload, state["format_lab"], scope="ai_tech")
    except GeminiError as exc:
        skipped = state.get("skipped_source_tweet_ids", [])
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print(f"AI/tech quote generation failed; source safely skipped: {exc}")
        return False

    if not draft.should_quote:
        skipped = state.get("skipped_source_tweet_ids", [])
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print("AI declined the AI/tech source; no post was published.")
        return False

    text = draft.quote_text.strip()
    if not text or len(text) > 280:
        skipped = state.get("skipped_source_tweet_ids", [])
        skipped.append(source.tweet_id)
        state["skipped_source_tweet_ids"] = skipped[-SOURCE_HISTORY_LIMIT:]
        _save_state(state)
        print("AI/tech quote exceeded the post limit and was discarded.")
        return False

    print(
        f"Publishing AI/tech text-only Quote Post ({len(text)}/280) from "
        f"@{source.username}: {text}"
    )
    try:
        tweet = await client.create_tweet(text=text, attachment_url=source.url)
    except Exception as exc:
        raise QueueError(f"X AI/tech Quote Post failed: {exc}") from exc

    tweet_id = str(getattr(tweet, "id", "") or "")
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "slot_number": int(state.get("ai_tech_quote_post_count", 0)) + 1,
        "post": text,
        "source_username": source.username,
        "source_type": source.source_type,
        "source_tweet_id": source.tweet_id,
        "source_url": source.url,
        "source_text": source.text,
        "topic": source.trend,
        "format_name": draft.format_name,
        "comedy_mechanism": draft.comedy_mechanism,
        "structure_signature": draft.structure_signature,
        "tweet_id": tweet_id,
        "generated_at": now,
        "status": "published",
    }
    used_trend_names = state.get("ai_tech_trend_names_used", [])
    used_trend_names.append(source.trend)
    state["ai_tech_trend_names_used"] = used_trend_names[-SOURCE_HISTORY_LIMIT:]

    state["ai_tech_quote_posts"] = (
        state.get("ai_tech_quote_posts", []) + [item]
    )[-AI_TECH_QUOTE_POSTS_PER_DAY:]
    for key in ("ai_tech_source_ids", "posted_source_tweet_ids"):
        values = state.get(key, [])
        values.append(source.tweet_id)
        state[key] = values[-SOURCE_HISTORY_LIMIT:]

    lab = state.get("format_lab", [])
    lab.append({
        "format_name": draft.format_name or "unnamed AI/tech quote",
        "comedy_mechanism": draft.comedy_mechanism,
        "used_at": now,
        "topic": source.trend,
        "tweet_id": tweet_id,
        "content_type": "quote_post",
        "content_mode": "ai_tech_quote",
        "source_tweet_id": source.tweet_id,
        "source_username": source.username,
        "post_text": text,
        "mutation_stage": draft.mutation_stage,
        "structure_signature": draft.structure_signature,
    })
    state["format_lab"] = lab[-20:]
    fingerprints = state.get("recent_post_fingerprints", [])
    fingerprints.append(draft.structure_signature[:180])
    state["recent_post_fingerprints"] = fingerprints[-30:]

    state.update({
        "day_key": today,
        "ai_tech_quote_day_key": today,
        "ai_tech_quote_post_count": int(state.get("ai_tech_quote_post_count", 0)) + 1,
        "daily_count": int(state.get("daily_count", 0)) + 1,
        "last_tweet_id": tweet_id,
        "last_posted_at": now,
        "last_source_tweet_id": source.tweet_id,
        "last_post_type": "ai_tech_quote",
        "last_company_handle": None,
    })
    _save_state(state)
    print(
        f"Published AI/tech Quote Post {state['ai_tech_quote_post_count']}/{AI_TECH_QUOTE_POSTS_PER_DAY}; "
        f"official-company quotes {state.get('trend_quote_post_count', 0)}/{TREND_QUOTE_POSTS_PER_DAY}; "
        f"photo prompts {state.get('person_photo_post_count', 0)}/{PERSON_PHOTO_POSTS_PER_DAY}; "
        f"daily slots {state['daily_count']}/{POSTS_PER_DAY}; "
        f"generation attempts {state['ai_call_count']}/{AI_GENERATIONS_PER_DAY}."
    )
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

    _set_workflow_output("published", "false")
    _set_workflow_output("skip_reason", "not_started")
    state = _load_state()
    if int(state.get("daily_count", 0)) >= POSTS_PER_DAY:
        print(f"Daily content-slot limit reached: {POSTS_PER_DAY}.")
        _set_workflow_output("skip_reason", "daily_cap")
        return False
    if int(state.get("ai_call_count", 0)) >= AI_GENERATIONS_PER_DAY:
        print(f"Daily generation-attempt budget reached: {AI_GENERATIONS_PER_DAY}.")
        _set_workflow_output("skip_reason", "generation_budget")
        return False
    if not _interval_ok(state):
        _set_workflow_output("skip_reason", "interval_guard")
        return False

    client = _client(auth_token, ct0)
    if not await client.is_logged_in():
        raise QueueError("X session is not logged in; auth cookies may be expired.")

    # Daily plan: 12 actual X Trends quote-posts plus 8 short developer-community
    # originals, using a verified reusable real portrait.
    lanes = (
        ("x_trends_quote", "trend_quote_post_count", TREND_QUOTE_POSTS_PER_DAY, _post_trend_quote),
        ("person_photo_prompt", "person_photo_post_count", PERSON_PHOTO_POSTS_PER_DAY, _post_person_photo),
        ("ai_tech_quote", "ai_tech_quote_post_count", AI_TECH_QUOTE_POSTS_PER_DAY, _post_ai_tech_quote),
    )
    start = int(state.get("next_lane_index", 0)) % len(lanes)
    eligible = [
        (index, lane)
        for offset in range(len(lanes))
        for index in [(start + offset) % len(lanes)]
        for lane in [lanes[index]]
        if int(state.get(lane[1], 0)) < lane[2]
    ]
    if not eligible:
        print(
            "Daily content plan complete: "
            f"X Trends quote-posts {state.get('trend_quote_post_count', 0)}/{TREND_QUOTE_POSTS_PER_DAY}, "
            f"developer originals {state.get('person_photo_post_count', 0)}/{PERSON_PHOTO_POSTS_PER_DAY}, "
            f"AI/tech quotes {state.get('ai_tech_quote_post_count', 0)}/{AI_TECH_QUOTE_POSTS_PER_DAY}."
        )
        _set_workflow_output("skip_reason", "daily_plan_complete")
        return False

    last_error = None
    for index, lane in eligible:
        lane_name, _, _, publisher = lane
        state["next_lane_index"] = (index + 1) % len(lanes)
        _save_state(state)
        print(f"Queue attempting lane: {lane_name}.")
        try:
            if await publisher(state, client):
                _set_workflow_output("published", "true")
                _set_workflow_output("skip_reason", "published")
                return True
        except QueueError:
            # A publish call with uncertain outcome must not silently fall through
            # and create a second main post in the same dispatch.
            raise
        except Exception as exc:
            last_error = exc
            print(f"Lane {lane_name} failed safely; trying the next eligible lane: {exc}")

        if int(state.get("daily_count", 0)) >= POSTS_PER_DAY:
            return False
        if int(state.get("ai_call_count", 0)) >= AI_GENERATIONS_PER_DAY:
            break

    if last_error is not None:
        print(f"No lane published a post during this dispatch; last error: {last_error}")
    else:
        print("No lane had eligible source material to publish during this dispatch.")
    _set_workflow_output("skip_reason", "no_eligible_source")
    return False
