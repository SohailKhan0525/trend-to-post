import json
from datetime import datetime, timezone
from pathlib import Path

from twikit import Client

QUEUE_PATH = Path("x.json")
STATE_PATH = Path("state/post_queue.json")


class QueueError(RuntimeError):
    pass


def _load_queue() -> list[dict]:
    if not QUEUE_PATH.exists():
        raise QueueError("x.json is missing.")
    try:
        items = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QueueError(f"x.json is invalid JSON: {exc}") from exc
    if not isinstance(items, list) or not items:
        raise QueueError("x.json must contain a non-empty JSON array.")

    for item in items:
        if not isinstance(item, dict):
            raise QueueError("Every queue entry must be an object.")
        text = str(item.get("sentence", "")).strip()
        if not text:
            raise QueueError(f"Queue entry {item.get('number', '?')} has no sentence.")
        if len(text) > 280:
            raise QueueError(
                f"Queue entry {item.get('number', '?')} exceeds 280 characters."
            )
    return items


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"next_index": 0}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QueueError(f"Queue state is invalid JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise QueueError("Queue state must be a JSON object.")
    return state


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def post_next(auth_token: str, ct0: str) -> bool:
    if not auth_token or not ct0:
        raise QueueError("X_AUTH_TOKEN and X_CT0 are required.")

    queue = _load_queue()
    state = _load_state()
    index = int(state.get("next_index", 0))

    if index >= len(queue):
        print(f"Queue exhausted: {len(queue)} posts have already been published.")
        return False

    item = queue[index]
    text = str(item["sentence"]).strip()
    number = item.get("number", index + 1)

    client = Client("en-US", impersonate="chrome124")
    client.set_cookies({"auth_token": auth_token, "ct0": ct0})

    if not await client.is_logged_in():
        raise QueueError("X session is not logged in; auth_token/ct0 may be expired.")

    print(f"Posting queue item {number}/{len(queue)}...")
    tweet = await client.create_tweet(text=text)
    tweet_id = str(getattr(tweet, "id", "") or "")

    state.update(
        {
            "next_index": index + 1,
            "last_number": number,
            "last_text": text,
            "last_tweet_id": tweet_id,
            "last_posted_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_state(state)

    print(
        f"Posted queue item {number}/{len(queue)}"
        + (f" as tweet {tweet_id}" if tweet_id else "")
    )
    return True
