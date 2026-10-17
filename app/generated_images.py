from __future__ import annotations

import base64
import binascii
import io
import json
import os
import secrets
import time
import urllib.error
import urllib.request

from PIL import Image


class GeneratedImageError(RuntimeError):
    """Raised when Cloudflare FLUX image generation fails or returns invalid data."""


MODEL = "@cf/black-forest-labs/flux-1-schnell"
IMAGE_WIDTH = 1024
IMAGE_HEIGHT = 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = bytes.fromhex("ffd8ff")
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_PROMPT_CHARS = 2048


def _split_comparison(headline: str) -> tuple[str, str]:
    import re

    cleaned = " ".join(str(headline).strip().rstrip("?!").split())
    for separator in (" or ", " versus ", " vs "):
        parts = re.split(re.escape(separator), cleaned, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip()[:80], parts[1].strip()[:80]
    raise GeneratedImageError(
        "Comparison headline must contain two choices separated by 'or', 'vs', or 'versus'."
    )


def build_comparison_image_prompt(headline: str) -> str:
    """Build a strict, reference-guided FLUX prompt that fits the model's 2,048-char input cap."""
    left, right = _split_comparison(headline)
    prompt = f"""Premium editorial comparison-meme art, carefully art-directed like high-end collectible-toy product photography. COMPOSITION: exact 50/50 vertical split at the midpoint; LEFT background soft cool light gray, RIGHT background vivid mint/turquoise green. Place a large centered white “VS” with a dark navy outline, crisp and legible, directly over the center seam.

LEFT CHOICE — “{left}”: one bold, simple, original visual metaphor/character, dark and high-contrast, clearly confined to the left half. It must instantly suggest this choice without using brand assets.

RIGHT CHOICE — “{right}”: three or four colorful fuzzy 3D plush-like mascots, distinct silhouettes, charming expressive faces, tactile fibers, vivid pink/yellow/blue/lime accents, soft contact shadows. Keep the group on the right half and make it visually communicate this choice, not random decoration.

QUALITY: delightful, polished, premium 3D art; softbox studio lighting; crisp character details; realistic fluffy materials; balanced scale; clean negative space; subtle grounding shadows; strong contrast and instantly readable visual storytelling. Keep both sides equally polished, with subjects fully in frame and within safe margins. No subject may obscure the central VS. The divide must be straight and unmistakable. Compose for a social feed with a clear left-versus-right story.

TEXT AND BRAND SAFETY: the ONLY permitted letters anywhere are the exact “VS” at the center. Never render choice names, headline, captions, labels, UI text, numbers, pseudo-text, watermarks, signatures, logos, or brand marks. Use original generic metaphors; do not copy any company's logo, mascot, screenshot, proprietary layout, or identity. No extra panels, borders, frames, or decorative lettering.

FINAL QUALITY CHECK: correct left/right concepts; clear gray/mint split; central VS legible; no accidental text; no cropping, duplicate limbs, melted objects, confusing overlaps, visual glitches, or logo-like shapes."""
    if len(prompt) > MAX_PROMPT_CHARS:
        raise GeneratedImageError(
            f"Comparison image prompt exceeds Cloudflare FLUX's {MAX_PROMPT_CHARS}-character limit."
        )
    return prompt


def _cloudflare_credentials() -> tuple[str, str]:
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not token:
        raise GeneratedImageError(
            "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required for FLUX.1 schnell image generation."
        )
    return account_id, token


def _validate_image(image_bytes: bytes) -> bytes:
    if not image_bytes:
        raise GeneratedImageError("Cloudflare FLUX returned an empty image.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise GeneratedImageError("Generated comparison image exceeds the 6 MiB upload limit.")
    if not (image_bytes.startswith(PNG_SIGNATURE) or image_bytes.startswith(JPEG_SIGNATURE)):
        raise GeneratedImageError("Cloudflare FLUX returned data that is not a PNG or JPEG image.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise GeneratedImageError("Generated comparison image has an unsupported image format.")
            if image.width < 512 or image.height < 512:
                raise GeneratedImageError("Generated comparison image resolution is too small.")
            image.verify()
    except GeneratedImageError:
        raise
    except Exception as exc:
        raise GeneratedImageError(f"Cloudflare FLUX returned a corrupt image: {exc}") from exc
    return image_bytes


def generate_comparison_image(headline: str, question: str = "") -> bytes:
    """Generate and validate a text-free comparison image with Cloudflare FLUX.1 [schnell].

    The Cloudflare Workers AI REST API is used directly; no Worker deployment or local
    vector fallback is used. API quota and service errors are raised so the post queue
    never silently substitutes a different image style.
    """
    del question  # Kept for call-site compatibility; the generated headline is authoritative.
    prompt = build_comparison_image_prompt(headline)
    account_id, token = _cloudflare_credentials()
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/run/{MODEL}"
    )
    payload = {
        "prompt": prompt,
        "steps": 4,
        "seed": secrets.randbelow(2_147_483_646) + 1,
    }

    for attempt in range(2):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
            if response_payload.get("success") is False:
                details = response_payload.get("errors") or response_payload.get("messages")
                raise GeneratedImageError(f"Cloudflare FLUX image generation failed: {details}")

            result = response_payload.get("result")
            encoded_image = result.get("image") if isinstance(result, dict) else None
            if not isinstance(encoded_image, str) or not encoded_image.strip():
                raise GeneratedImageError("Cloudflare FLUX returned no base64 image.")
            try:
                image_bytes = base64.b64decode(encoded_image, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise GeneratedImageError("Cloudflare FLUX returned invalid base64 image data.") from exc
            return _validate_image(image_bytes)

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:900]
            if exc.code in {408, 500, 502, 503, 504} and attempt == 0:
                time.sleep(2)
                continue
            if exc.code == 429:
                raise GeneratedImageError(
                    "Cloudflare FLUX was rate- or daily-quota-limited (HTTP 429); "
                    f"no fallback image was published. Details: {detail}"
                ) from exc
            raise GeneratedImageError(
                f"Cloudflare FLUX failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 0:
                time.sleep(2)
                continue
            raise GeneratedImageError(f"Cloudflare FLUX request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise GeneratedImageError("Cloudflare FLUX returned invalid JSON.") from exc

    raise GeneratedImageError("Cloudflare FLUX image generation failed after retrying.")
