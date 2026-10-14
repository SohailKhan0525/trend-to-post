from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class GeneratedImageError(RuntimeError):
    pass


MAX_IMAGE_BYTES = 6 * 1024 * 1024
JPEG_SIGNATURES = (b"\xff\xd8\xff",)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def generate_comparison_image(headline: str, question: str) -> bytes:
    """Generate a clean, text-free visual for a developer comparison meme."""
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not token:
        raise GeneratedImageError("Cloudflare image generation credentials are missing.")

    # Never pass the full headline/question to the image model: models often copy it into the image.
    # Give the model only the two short concepts, and describe the visual treatment separately.
    parts = str(headline).strip().rstrip("?!.").split(" or ", 1)
    if len(parts) != 2:
        parts = str(headline).strip().rstrip("?!.").split(" vs ", 1)
    if len(parts) == 2:
        left_entity = " ".join(parts[0].split())[:100]
        right_entity = " ".join(parts[1].split())[:100]
    else:
        left_entity, right_entity = "first concept", "second concept"

    is_dark_light = (
        "dark mode" in left_entity.lower() and "light mode" in right_entity.lower()
    ) or (
        "light mode" in left_entity.lower() and "dark mode" in right_entity.lower()
    )
    if is_dark_light:
        left_visual = (
            "a modern smartphone displaying a beautiful dark-mode app interface, "
            "near-black and charcoal surfaces with subtle cool highlights"
        )
        right_visual = (
            "a modern smartphone displaying a beautiful light-mode app interface, "
            "clean white surfaces with soft neutral borders and gentle highlights"
        )
    else:
        left_visual = f"an immediately recognizable visual representation of {left_entity}"
        right_visual = f"an immediately recognizable visual representation of {right_entity}"

    prompt = (
        "Create one polished, playful, high-quality 3D editorial illustration for a "
        "software developer comparison meme. This is an IMAGE ONLY, not a poster or social-media "
        "graphic. Show exactly two clearly recognizable subjects in a balanced side-by-side "
        "composition: LEFT side: " + left_visual + "; RIGHT side: " + right_visual + ". "
        "The concepts are visual guidance only. Do not write or print their names. "
        "Use a simple diagonal split-color studio background, attractive materials, clean "
        "composition, and soft cinematic lighting. No screenshot of a social media post. "
        "ABSOLUTELY NO TEXT OF ANY KIND: no headline, no question, no letters, no words, no "
        "numbers, no typography, no captions, no logos, no brand marks, no watermarks, no labels, "
        "no signs, no UI chrome, no badges, no alt-text markers, and no text overlays. "
        "If text would otherwise appear, leave that area completely blank. Do not depict real people. "
        "Use original generic objects/characters only when they make the compared concepts clearer. "
        "Landscape 4:3 composition."
    )
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        + account_id
        + "/ai/run/@cf/black-forest-labs/flux-1-schnell"
    )
    payload = json.dumps({
        "prompt": prompt,
        "steps": 4,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "image/png",
            "User-Agent": "TrendToPostBot/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
            content_type = response.headers.get("Content-Type", "").lower()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(1200).decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise GeneratedImageError(
            f"Cloudflare image generation failed: HTTP {exc.code}"
            + (f" - {detail}" if detail else "")
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GeneratedImageError(f"Cloudflare image generation failed: {exc}") from exc

    if len(data) > MAX_IMAGE_BYTES:
        raise GeneratedImageError("Generated image exceeds the 6 MiB upload limit.")
    if not data:
        raise GeneratedImageError("Image model returned an empty response.")
    # Some Cloudflare responses are JSON envelopes; support their base64 payload.
    if "json" in content_type or data.lstrip().startswith(b"{"):
        try:
            import base64
            result = json.loads(data)
            response_data = result.get("result", {}).get("image") or result.get("result", {}).get("response")
            if isinstance(response_data, str):
                data = base64.b64decode(response_data, validate=True)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise GeneratedImageError("Image model returned an invalid JSON image response.") from exc
    # FLUX.1 schnell returns base64 JPEG in result.image.
    if not (data.startswith(JPEG_SIGNATURES) or data.startswith(PNG_SIGNATURE)):
        raise GeneratedImageError(
            "Image model response was neither a JPEG nor PNG; refusing to publish it."
        )
    return data
