import json
import random
from datetime import datetime, timezone
from pathlib import Path

from twikit import Client

from .image_generation import generate_image, is_grok_post

QUEUE_PATH = Path("x.json")
STATE_PATH = Path("state/post_queue.json")

POSTS_PER_DAY = 48
IMAGES_PER_DAY = 5


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


def _select_image_indices(
    queue: list[dict],
    cycle_start_index: int,
    cycle_number: int,
) -> set[int]:
    start = cycle_start_index + cycle_number * POSTS_PER_DAY
    end = min(start + POSTS_PER_DAY, len(queue))
    candidates = [
        index
        for index in range(start, end)
        if not is_grok_post(queue[index])
    ]

    if len(candidates) <= IMAGES_PER_DAY:
        return set(candidates)

    # The seed makes the random selection stable for retries of the same post.
    rng = random.Random(f"trend-to-post-image-cycle:{cycle_start_index}:{cycle_number}")
    return set(rng.sample(candidates, IMAGES_PER_DAY))


async def post_next(auth_token: str, ct0: str) -> bool:
    if not auth_token or not ct0:
        raise QueueError("X_AUTH_TOKEN and X_CT0 are required.")

    queue = _load_queue()
    state = _load_state()
    index = int(state.get("next_index", 0))

    if index < 0:
        raise QueueError(f"Queue state next_index cannot be negative: {index}")

    if index >= len(queue):
        print(f"Queue exhausted: {len(queue)} posts have already been published.")
        return False

    last_posted_at = state.get("last_posted_at")
    if last_posted_at:
        try:
            last_posted = datetime.fromisoformat(str(last_posted_at))
            elapsed = datetime.now(timezone.utc) - last_posted.astimezone(timezone.utc)
            if elapsed.total_seconds() < 30 * 60:
                remaining = int(30 * 60 - elapsed.total_seconds())
                print(
                    "Post interval guard: next post allowed in about "
                    f"{remaining // 60 + (1 if remaining % 60 else 0)} minute(s)."
                )
                return False
        except ValueError as exc:
            raise QueueError(
                f"Invalid last_posted_at in queue state: {last_posted_at}"
            ) from exc

    image_cycle_start_index = int(state.get("image_cycle_start_index", index))
    if not 0 <= image_cycle_start_index < len(queue):
        raise QueueError(
            "Queue state image_cycle_start_index must point to an existing queue item."
        )
    if index < image_cycle_start_index:
        raise QueueError(
            "Queue state next_index is before image_cycle_start_index; refusing to "
            "change the image schedule."
        )

    image_cycle = (index - image_cycle_start_index) // POSTS_PER_DAY
    image_targets = _select_image_indices(
        queue,
        image_cycle_start_index,
        image_cycle,
    )
    images_used_before = sum(target < index for target in image_targets)
    use_image = index in image_targets and images_used_before < IMAGES_PER_DAY

    item = queue[index]
    text = str(item["sentence"]).strip()
    number = item.get("number", index + 1)
    item_type = str(item.get("type", "")).strip()

    client = Client("en-US", impersonate="chrome124")
    client.set_cookies({"auth_token": auth_token, "ct0": ct0})

    if is_grok_post(item):
        print(
            f"Queue item {number}/{len(queue)} is @grok; "
            "posting without an image."
        )
    elif use_image:
        print(
            f"Queue item {number}/{len(queue)} is one of the "
            f"{IMAGES_PER_DAY} random image slots for this 24-hour posting cycle "
            f"({images_used_before + 1}/{IMAGES_PER_DAY})."
        )
    else:
        print(
            f"Queue item {number}/{len(queue)} is text-only; "
            f"{images_used_before}/{IMAGES_PER_DAY} image slots have already been used "
            "in this cycle."
        )

    if not await client.is_logged_in():
        raise QueueError("X session is not logged in; auth_token/ct0 may be expired.")

    media_ids: list[str] = []
    image_path: Path | None = None
    posted_with_image = False

    if use_image:
        print(f"Generating image for queue item {number}/{len(queue)}...")
        image_path = await generate_image(
            text,
            item_type=item_type,
            seed=int(number),
        )
        try:
            print(f"Uploading generated image for queue item {number}/{len(queue)}...")
            media_id = await client.upload_media(
                str(image_path),
                wait_for_completion=True,
            )
            media_ids.append(media_id)
            posted_with_image = True
        finally:
            image_path.unlink(missing_ok=True)

    print(f"Posting queue item {number}/{len(queue)}...")
    if media_ids:
        tweet = await client.create_tweet(text=text, media_ids=media_ids)
    else:
        tweet = await client.create_tweet(text=text)

    tweet_id = str(getattr(tweet, "id", "") or "")
    image_count = images_used_before + int(posted_with_image)

    state.update(
        {
            "next_index": index + 1,
            "last_number": number,
            "last_text": text,
            "last_tweet_id": tweet_id,
            "last_posted_at": datetime.now(timezone.utc).isoformat(),
            "image_cycle_start_index": image_cycle_start_index,
            "image_cycle": image_cycle,
            "image_count": image_count,
        }
    )
    _save_state(state)

    print(
        f"Posted queue item {number}/{len(queue)}"
        + (f" as tweet {tweet_id}" if tweet_id else "")
        + (" with image." if posted_with_image else " as text-only.")
    )
    return True
