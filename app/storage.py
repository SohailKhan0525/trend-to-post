import hashlib
import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import Draft, Trend

HISTORY_PATH = Path("state/content_history.json")
SIMILARITY_THRESHOLD = 0.90
IST = ZoneInfo("Asia/Kolkata")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _fingerprint(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def load_recent_texts(limit: int = 100) -> list[str]:
    history = _load_history()
    return [str(item.get("text", "")) for item in history[-limit:] if item.get("text")]


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
    return any(
        item.get("fingerprint") == fingerprint
        or (
            (previous := _normalize(str(item.get("text", ""))))
            and SequenceMatcher(None, candidate, previous).ratio() >= SIMILARITY_THRESHOLD
        )
        for item in history
    )


def filter_new_drafts(drafts: list[Draft]) -> list[Draft]:
    history = _load_history()
    result = []

    for draft in drafts:
        if _is_duplicate(draft.text, history):
            continue
        result.append(draft)
        history.append({
            "fingerprint": _fingerprint(draft.text),
            "text": draft.text,
            "trend": draft.trend,
            "angle": draft.angle,
            "post_type": draft.post_type,
            "generated_at": draft.generated_at,
        })

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(history[-1000:], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def write_daily_markdown(root: Path, trends: list[Trend], drafts: list[Draft]):
    now = datetime.now(IST)
    stamp = now.strftime("%Y-%m-%d-%H%M-IST")
    directory = root / "posts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}.md"

    lines = [
        f"# X Trend Content — {now.strftime('%d %b %Y, %I:%M %p IST')}",
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
            f"**Post type:** {draft.post_type}",
            "",
            f"**Trend:** {draft.trend}",
            "",
            f"**Angle:** {draft.angle}",
            "",
            f"**Word count:** {len(draft.text.split())}",
            "",
            f"**Character count:** {len(draft.text)}",
            "",
            "**Main post:**",
            "",
            draft.text,
            "",
            f"**Reply word count:** {len(draft.reply_text.split())}",
            "",
            f"**Reply character count:** {len(draft.reply_text)}",
            "",
            "**Second-account reply:**",
            "",
            draft.reply_text,
            "",
        ]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
