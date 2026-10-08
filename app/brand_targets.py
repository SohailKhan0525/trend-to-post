from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets


DAILY_BRAND_TARGETS = 5
BRAND_COOLDOWN_DAYS = 4

# Famous, conversation-friendly companies/products spanning the account's
# allowed verticals. Five selected targets are used by the daily company-post slots.
# Automated @mentions are disabled unless a recipient has explicitly opted in.
BRAND_POOL = (
    ("@BMW", "BMW", ("bmw", "m4", "m3", "m5", "i4", "i5", "i7", "x5")),
    ("@MercedesBenz", "Mercedes-Benz", ("mercedes", "amg", "maybach")),
    ("@Porsche", "Porsche", ("porsche", "911", "taycan", "macan")),
    ("@Ferrari", "Ferrari", ("ferrari", "sf90", "296")),
    ("@Tesla", "Tesla", ("tesla", "model 3", "model s", "model x", "model y", "cybertruck")),
    ("@Apple", "Apple", ("apple", "iphone", "ipad", "macbook", "vision pro")),
    ("@Google", "Google", ("google", "gemini", "pixel", "android")),
    ("@Microsoft", "Microsoft", ("microsoft", "copilot", "windows", "xbox", "azure")),
    ("@OpenAI", "OpenAI", ("openai", "chatgpt")),
    ("@AnthropicAI", "Anthropic", ("anthropic", "claude")),
    ("@NVIDIA", "NVIDIA", ("nvidia", "geforce", "cuda")),
    ("@AMD", "AMD", ("amd", "radeon", "ryzen")),
    ("@Intel", "Intel", ("intel", "core", "arc")),
    ("@Meta", "Meta", ("meta", "quest", "llama")),
    ("@Amazon", "Amazon", ("amazon", "aws")),
    ("@Samsung", "Samsung", ("samsung", "galaxy")),
    ("@Sony", "Sony", ("sony", "playstation", "ps5")),
    ("@NintendoAmerica", "Nintendo", ("nintendo", "switch", "zelda", "mario")),
    ("@Xbox", "Xbox", ("xbox", "game pass")),
    ("@PlayStation", "PlayStation", ("playstation", "ps5", "ps plus")),
    ("@Steam", "Steam", ("steam", "steam deck")),
    ("@EpicGames", "Epic Games", ("epic games", "fortnite", "unreal")),
    ("@EA", "EA", ("ea", "fc", "fifa")),
    ("@Roblox", "Roblox", ("roblox",)),
    ("@Adobe", "Adobe", ("adobe", "photoshop", "premiere")),
    ("@Canva", "Canva", ("canva",)),
    ("@Figma", "Figma", ("figma",)),
    ("@NotionHQ", "Notion", ("notion",)),
    ("@GitHub", "GitHub", ("github", "copilot")),
    ("@vercel", "Vercel", ("vercel",)),
    ("@Cloudflare", "Cloudflare", ("cloudflare",)),
    ("@SlackHQ", "Slack", ("slack",)),
    ("@Discord", "Discord", ("discord",)),
    ("@Spotify", "Spotify", ("spotify",)),
    ("@Netflix", "Netflix", ("netflix",)),
    ("@Nike", "Nike", ("nike", "air max", "air jordan")),
    ("@adidas", "adidas", ("adidas", "ultraboost",)),
)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def ensure_daily_brand_targets(state: dict) -> bool:
    """Create five rotating brand targets with a four-day cooldown."""
    today = _today()
    existing_day = str(state.get("brand_tag_day_key", "")).strip()
    existing_queue = state.get("brand_tag_queue")
    if existing_day == today and isinstance(existing_queue, list) and len(existing_queue) == DAILY_BRAND_TARGETS:
        return False

    now = datetime.now(timezone.utc)
    cooldowns = state.get("brand_tag_cooldowns", {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}

    eligible = []
    for handle, name, keywords in BRAND_POOL:
        expires = _parse_iso(cooldowns.get(handle))
        if expires and expires > now:
            continue
        eligible.append((handle, name, keywords))

    # If the pool is temporarily exhausted, release the oldest cooldowns rather
    # than producing an empty queue.
    if len(eligible) < DAILY_BRAND_TARGETS:
        ordered = sorted(
            BRAND_POOL,
            key=lambda item: _parse_iso(cooldowns.get(item[0])) or datetime.min.replace(tzinfo=timezone.utc),
        )
        eligible = [item for item in ordered if item not in eligible]

    rng = secrets.SystemRandom()
    picks = rng.sample(eligible, k=min(DAILY_BRAND_TARGETS, len(eligible)))
    queue = []

    for handle, name, keywords in picks:
        expires = now + timedelta(days=BRAND_COOLDOWN_DAYS)
        cooldowns[handle] = expires.isoformat()
        queue.append(
            {
                "handle": handle,
                "name": name,
                "keywords": list(keywords),
                "selected_at": now.isoformat(),
                "eligible_again_at": expires.isoformat(),
                "status": "manual_approval_required",
            }
        )

    state["brand_tag_day_key"] = today
    state["brand_tag_queue"] = queue
    state["brand_tag_cooldowns"] = cooldowns
    return True
