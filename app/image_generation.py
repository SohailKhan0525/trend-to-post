import asyncio
import base64
import binascii
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "@cf/black-forest-labs/flux-1-schnell"

IMAGE_PROMPT_SUFFIX = """
Act as a professional editorial art director creating a premium social-media image.

Interpret the meaning of the post, rather than illustrating its words literally.

Create ONE strong visual concept with:
- a clear primary subject
- a specific environment that supports the idea
- a memorable visual metaphor when the post is abstract
- natural human or technological details only when relevant
- cinematic, realistic lighting
- strong depth, composition, and visual hierarchy
- a polished contemporary editorial / magazine aesthetic
- an image that is understandable and interesting even when viewed without the post

For technology or AI topics, prefer sophisticated real-world scenes, products, interfaces represented as physical environments, data centers, laboratories, engineers, robots, hardware, or conceptual visual metaphors. Avoid generic neon cyberpunk imagery unless the post specifically calls for it.

For future/space/science topics, use believable engineering, scientific, or cinematic environments rather than generic sci-fi clichés.

For questions, create a visual scene that represents the subject being questioned; do not create a literal question mark.

For emotional or human topics, use expressive but natural scenes and body language rather than generic stock-photo compositions.

Composition rules:
- one coherent scene
- clear focal point
- strong foreground/midground/background separation
- visually balanced
- no clutter
- no collage
- no split-screen
- no random objects

Absolutely no visible text, letters, words, captions, subtitles, typography, logos, watermarks, UI screenshots, charts with readable labels, or fake social-media interfaces.

Do not reproduce the post as text inside the image.
Do not add decorative text.
Do not make the image look like an AI-generated meme.
"""

class ImageGenerationError(RuntimeError):
    pass


def _build_prompt(sentence: str) -> str:
    return (
        "Create a premium editorial image inspired by this social media post.\n\n"
        f"POST: {sentence}\n\n"
        f"VISUAL BRIEF:{IMAGE_PROMPT_SUFFIX.strip()}"
    )


def _generate_image_sync(prompt: str) -> tuple[bytes, str]:
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()

    if not account_id or not api_token:
        raise ImageGenerationError(
            "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required for image generation."
        )

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        f"/ai/run/{MODEL}"
    )
    body = json.dumps(
        {
            "prompt": prompt,
            "steps": 8,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ImageGenerationError(
            f"Cloudflare image generation failed with HTTP {exc.code}: {detail[:1000]}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ImageGenerationError(f"Cloudflare image generation request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ImageGenerationError("Cloudflare returned an invalid JSON response.") from exc

    if not payload.get("success", False):
        raise ImageGenerationError(
            f"Cloudflare image generation failed: {json.dumps(payload.get('errors', payload))[:1000]}"
        )

    result = payload.get("result")
    if isinstance(result, dict):
        image_b64 = result.get("image") or result.get("image_b64")
    elif isinstance(result, str):
        image_b64 = result
    else:
        image_b64 = None

    if not isinstance(image_b64, str) or not image_b64:
        raise ImageGenerationError("Cloudflare response did not contain generated image data.")

    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ImageGenerationError("Cloudflare returned invalid base64 image data.") from exc

    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        suffix = ".png"
    elif image_bytes.startswith(b"\xff\xd8\xff"):
        suffix = ".jpg"
    else:
        raise ImageGenerationError("Generated image format was not recognized as PNG or JPEG.")

    return image_bytes, suffix


async def generate_image(sentence: str) -> Path:
    if not sentence.strip():
        raise ImageGenerationError("Cannot generate an image for an empty sentence.")

    prompt = _build_prompt(sentence.strip())
    image_bytes, suffix = await asyncio.to_thread(_generate_image_sync, prompt)

    temp = tempfile.NamedTemporaryFile(prefix="x-post-", suffix=suffix, delete=False)
    try:
        temp.write(image_bytes)
        temp.flush()
    finally:
        temp.close()

    return Path(temp.name)


def is_grok_post(item: dict) -> bool:
    return str(item.get("type", "")).strip().lower() == "grok" or (
        str(item.get("sentence", "")).strip().lower().startswith("@grok")
    )
