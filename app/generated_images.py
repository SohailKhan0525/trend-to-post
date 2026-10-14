from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class GeneratedImageError(RuntimeError):
    pass


MAX_IMAGE_BYTES = 6 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def generate_comparison_image(headline: str, question: str) -> bytes:
    """Generate a clean, text-free visual for a developer comparison meme."""
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not token:
        raise GeneratedImageError("Cloudflare image generation credentials are missing.")

    # Use only the two compared entities as visual grounding; post copy is never rendered.
    subject = " ".join(str(headline).split())[:240]
    prompt = (
        "Create one polished, playful, high-quality 3D editorial illustration for a "
        "software developer comparison meme. The exact comparison headline is context only: "
        + subject + ". "
        "Identify the TWO entities named in that headline and depict a faithful, recognizable "
        "visual metaphor for each one: the first named entity must appear only on the LEFT, "
        "and the second named entity must appear only on the RIGHT. Make the two sides clearly "
        "different and semantically matched to those exact entities, not generic unrelated "
        "characters. Use original, charming 3D characters or objects, tactile materials, "
        "expressive shapes, studio lighting, and a clean split-color background. Never render "
        "the headline or any other supplied text into the image. ABSOLUTELY NO TEXT, no letters, "
        "typography, captions, logos, brand marks, watermarks, labels, UI, signs, readable "
        "symbols, and no text overlays whatsoever. Do not depict real people. Do not imitate official "
        "trademarked mascots; use original visual metaphors specific to the two named entities. "
        "Image only, landscape 4:3 composition."
    )
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        + account_id
        + "/ai/run/@cf/black-forest-labs/flux-1-schnell"
    )
    payload = json.dumps({
        "prompt": prompt,
        "width": 1024,
        "height": 768,
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
    if not data.startswith(PNG_SIGNATURE):
        raise GeneratedImageError("Image model response was not a PNG; refusing to publish it.")
    return data
