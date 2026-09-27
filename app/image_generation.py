import asyncio
import base64
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "@cf/black-forest-labs/flux-1-schnell"

IMAGE_PROMPT_SUFFIX = (
    "Create one original, visually striking editorial image that communicates the core idea "
    "of this social media post. Use a modern, polished, cinematic visual style with clear "
    "subject focus, believable lighting, strong composition, and context-appropriate details. "
    "Do not place any words, captions, letters, logos, watermarks, social-media UI, or fake "
    "screenshots in the image. Represent the idea visually rather than literally printing the post."
)


class ImageGenerationError(RuntimeError):
    pass


def _build_prompt(sentence: str) -> str:
    return f'Social media post: "{sentence}"\n\nImage direction: {IMAGE_PROMPT_SUFFIX}'


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
            "steps": 4,
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
        with urllib.request.urlopen(request, timeout=120) as response:
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
    except (ValueError, base64.binascii.Error) as exc:
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
