from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .brand_targets import DAILY_BRAND_TARGETS, ensure_daily_brand_targets
from .gemini import GeminiError, generate_company_original
from .post_queue import (
    BRAND_AI_GENERATIONS_PER_DAY,
    QueueError,
    _load_state,
    _save_state,
    _today,
)

BRAND_DRAFTS_PATH = Path("state/brand_drafts.md")


def _write_markdown(state: dict) -> None:
    queue = state.get("brand_draft_queue", [])
    date_key = str(state.get("brand_draft_day_key", "")).strip() or _today()

    lines = [
        "# Daily Company Reply Drafts",
        "",
        f"UTC date: {date_key}",
        "",
        "These are copy-ready original posts for manual approval. "
        "They are not auto-published or auto-mentioned by the bot.",
        "",
    ]

    if not queue:
        lines.append("No drafts generated yet.")
    else:
        for index, item in enumerate(queue, start=1):
            lines.extend([
                f"## {index}. {item.get('name', '')} {item.get('handle', '')}".strip(),
                "",
                f"> {item.get('post', '')}",
                "",
                f"Hook: {item.get('hook_type', '')}",
                f"Format: {item.get('format_name', '')}",
                f"Mechanism: {item.get('comedy_mechanism', '')}",
                f"Angle: {item.get('angle', '')}",
                f"Status: {item.get('status', 'manual_approval_required')}",
                "",
            ])

    BRAND_DRAFTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BRAND_DRAFTS_PATH.with_suffix(".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    tmp.replace(BRAND_DRAFTS_PATH)


def _current_targets(state: dict) -> list[dict]:
    targets = state.get("brand_tag_queue", [])
    if not isinstance(targets, list):
        raise QueueError("Brand target queue must be a list.")
    return [
        item for item in targets
        if isinstance(item, dict) and item.get("handle") and item.get("name")
    ][:DAILY_BRAND_TARGETS]


def generate_daily_brand_drafts() -> list[dict]:
    state = _load_state()
    changed = ensure_daily_brand_targets(state)
    today = _today()

    if changed or str(state.get("brand_draft_day_key", "")).strip() != today:
        state["brand_draft_day_key"] = today
        state["brand_draft_queue"] = []
        state["brand_ai_call_count"] = 0
        state["brand_format_lab"] = []
        _save_state(state)

    targets = _current_targets(state)
    if len(targets) < DAILY_BRAND_TARGETS:
        raise QueueError(
            f"Only {len(targets)} valid brand targets are available; "
            f"expected {DAILY_BRAND_TARGETS}."
        )

    existing = {
        str(item.get("handle", "")).strip(): item
        for item in state.get("brand_draft_queue", [])
        if isinstance(item, dict) and item.get("handle")
    }

    for target in targets:
        handle = str(target["handle"]).strip()
        if handle in existing and existing[handle].get("post"):
            continue

        if state["brand_ai_call_count"] >= BRAND_AI_GENERATIONS_PER_DAY:
            print(
                "Daily company-draft AI limit reached: "
                f"{BRAND_AI_GENERATIONS_PER_DAY}."
            )
            break

        state["brand_ai_call_count"] += 1
        _save_state(state)

        try:
            draft = generate_company_original(target, state["brand_format_lab"])
        except GeminiError as exc:
            target["status"] = "generation_failed"
            target["last_error"] = str(exc)
            _save_state(state)
            print(f"Company draft failed for {handle}: {exc}")
            continue

        if not draft.should_post:
            target["status"] = "generation_rejected"
            _save_state(state)
            continue

        generated_at = datetime.now(timezone.utc).isoformat()
        item = {
            "handle": handle,
            "name": str(target.get("name", "")).strip(),
            "post": draft.post_text.strip(),
            "angle": draft.angle,
            "hook_type": draft.hook_type,
            "format_name": draft.format_name,
            "comedy_mechanism": draft.comedy_mechanism,
            "structure_signature": draft.structure_signature,
            "generated_at": generated_at,
            "status": "manual_approval_required",
        }
        existing[handle] = item

        lab = state["brand_format_lab"]
        lab.append({
            "format_name": draft.format_name or "unnamed company format",
            "comedy_mechanism": draft.comedy_mechanism,
            "hook_type": draft.hook_type,
            "structure_signature": draft.structure_signature,
            "used_at": generated_at,
            "target": handle,
        })
        state["brand_format_lab"] = lab[-20:]
        target["status"] = "draft_generated"
        target.pop("last_error", None)

        state["brand_draft_queue"] = [
            existing[item_handle]
            for item_handle in [str(t["handle"]).strip() for t in targets]
            if item_handle in existing
        ]
        _save_state(state)
        print(f"Company draft ready for {handle}: {draft.post_text}")

    state["brand_draft_queue"] = [
        existing[item_handle]
        for item_handle in [str(t["handle"]).strip() for t in targets]
        if item_handle in existing
    ]
    _write_markdown(state)
    _save_state(state)

    print(
        f"Company drafts ready: {len(state['brand_draft_queue'])}/{DAILY_BRAND_TARGETS}; "
        f"AI calls {state['brand_ai_call_count']}/{BRAND_AI_GENERATIONS_PER_DAY}."
    )
    return state["brand_draft_queue"]
