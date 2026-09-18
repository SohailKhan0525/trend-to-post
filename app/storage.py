import hashlib
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from .models import Draft, Trend

HISTORY_PATH = Path("state/content_history.json")
SIMILARITY_THRESHOLD = 0.90


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _fingerprint(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def _load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _is_duplicate(text: str, history: list[dict]) -> bool:
    candidate = _normalize(text)
    fingerprint = _fingerprint(text)

    for item in history:
        if item.get("fingerprint") == fingerprint:
            return True
        previous = _normalize(str(item.get("text", "")))
        if previous and SequenceMatcher(None, candidate, previous).ratio() >= SIMILARITY_THRESHOLD:
            return True
    return False


def write_daily_markdown(root: Path, trends: list[Trend], drafts: list[Draft]):
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d-%H%M")
    directory = root / "posts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}.md"

    lines = [
        f"# X Trend Content — {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## X Trends collected",
        "",
    ]
    lines += [f"{t.rank}. {t.name}" for t in trends]
    lines += ["", "## Draft", ""]

    for i, draft in enumerate(drafts, 1):
        lines += [
            f"### Draft {i}",
            "",
            f"**Trend:** {draft.trend}",
            "",
            f"**Angle:** {draft.angle}",
            "",
            draft.text,
            "",
        ]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    history = _load_history()
    for draft in drafts:
        history.append({
            "fingerprint": _fingerprint(draft.text),
            "text": draft.text,
            "trend": draft.trend,
            "angle": draft.angle,
            "generated_at": draft.generated_at,
        })

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(history[-1000:], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def filter_new_drafts(drafts: list[Draft]) -> list[Draft]:
    history = _load_history()
    result = []
    for draft in drafts:
        if not _is_duplicate(draft.text, history):
            result.append(draft)
            history.append({
                "fingerprint": _fingerprint(draft.text),
                "text": draft.text,
                "trend": draft.trend,
                "angle": draft.angle,
                "generated_at": draft.generated_at,
            })
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(history[-1000:], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result
