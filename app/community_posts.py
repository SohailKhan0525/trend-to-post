from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .post_queue import QueueError, _client, _save_state

COMMUNITY_STATE_PATH = Path("state/community_posts.json")
IST = ZoneInfo("Asia/Kolkata")
COMMUNITY_POSTS = {
    "connect": "Let's connect if you are building in tech, ai, vibe coding 👋🤝",
    "building": "What are you building today 👀.\nShare it with a link 👇",
}


def _load_community_state() -> dict:
    if not COMMUNITY_STATE_PATH.exists():
        return {"day_key": "", "posted": {}}
    try:
        value = json.loads(COMMUNITY_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueError(f"Could not read community-post state: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("posted", {}), dict):
        raise QueueError("Community-post state has an invalid shape.")
    value.setdefault("day_key", "")
    value.setdefault("posted", {})
    return value


def _save_community_state(state: dict) -> None:
    COMMUNITY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = COMMUNITY_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(COMMUNITY_STATE_PATH)


async def post_community_post(auth_token: str, ct0: str, slot: str) -> bool:
    """Publish one fixed, non-AI community post once per India-local calendar day."""
    if slot not in COMMUNITY_POSTS:
        raise QueueError(f"Unknown community post slot: {slot}")
    if not auth_token or not ct0:
        raise QueueError("X_AUTH_TOKEN and X_CT0 are required.")

    now = datetime.now(IST)
    day_key = now.date().isoformat()
    state = _load_community_state()
    if state.get("day_key") != day_key:
        state = {"day_key": day_key, "posted": {}}
    if slot in state["posted"]:
        print(f"Community post '{slot}' already published for {day_key}; skipping duplicate.")
        return False

    client = _client(auth_token, ct0)
    if not await client.is_logged_in():
        raise QueueError("X session is not logged in; X auth cookies may be expired.")

    text = COMMUNITY_POSTS[slot]
    tweet = await client.create_tweet(text=text)
    tweet_id = str(getattr(tweet, "id", "") or "")
    if not tweet_id:
        raise QueueError("X accepted the community post but returned no tweet ID; inspect before retrying.")

    state["posted"][slot] = {
        "tweet_id": tweet_id,
        "text": text,
        "posted_at": now.isoformat(),
    }
    _save_community_state(state)
    print(f"Published fixed community post '{slot}' for {day_key}: {tweet_id}")
    return True
