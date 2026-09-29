import asyncio
import base64
import binascii
import json
import os
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MODEL = "@cf/black-forest-labs/flux-2-klein-4b"


class ImageGenerationError(RuntimeError):
    pass


def has_mention_tag(item: dict) -> bool:
    return "@" in str(item.get("sentence", ""))


def _build_prompt(sentence: str, item_type: str = "") -> str:
    post = sentence.strip()
    lower = post.lower()

    if "hardcoded secret" in lower:
        return (
            "Editorial illustration for a funny software-developer post. "
            "Show one exhausted developer in a contemporary office, sitting at a laptop after finally solving a stubborn bug. "
            "He is leaning back in his chair with both hands behind his head, eyes closed, wearing a small relieved grin. "
            "On the desk are a coffee mug, notebook, and laptop. The laptop screen shows only abstract colored code-like shapes, "
            "not readable programming and not letters. Make the scene subtly funny through the exaggerated relief and messy desk. "
            "Modern magazine illustration, tasteful, cinematic composition, realistic proportions, slightly stylized editorial art, "
            "warm natural light, shallow depth of field, sophisticated tech-publication aesthetic. "
            "NO TEXT ANYWHERE. No words, letters, numbers, captions, logos, watermarks, UI, readable code, signs, or typography."
        )

    return (
        "Create one single coherent editorial photograph or tasteful editorial illustration inspired by the meaning of the post. "
        "Use concrete people, objects, environment, and action to tell the story. "
        "Never turn the post into a poster, quote card, screenshot, infographic, or text overlay. "
        "Post type: " + (item_type or "general") + ". "
        "The following is semantic reference only. Understand its meaning and subject; do NOT reproduce or display any of its wording. "
        f"Semantic reference: {post} "
        "Premium realistic visual style, believable real-world setting, natural anatomy, strong composition, cinematic depth, "
        "polished lighting. Absolutely no readable text, letters, numbers, captions, logos, watermarks, UI, signs, "
        "readable screens, or typography anywhere in the image."
    )


def _multipart_body(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"----trend-to-post-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _generate_image_sync(prompt: str, seed: int) -> tuple[bytes, str]:
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
    body, content_type = _multipart_body(
        {
            "prompt": prompt,
            "width": "1024",
            "height": "1024",
            "seed": str(seed),
        }
    )

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": content_type,
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


async def generate_image(sentence: str, item_type: str = "", seed: int = 0) -> Path:
    if not sentence.strip():
        raise ImageGenerationError("Cannot generate an image for an empty sentence.")
    if "@" in sentence:
        raise ImageGenerationError(
            "Refusing to generate an image for a post containing an @ mention."
        )

    prompt = _build_prompt(sentence.strip(), item_type.strip())
    image_bytes, suffix = await asyncio.to_thread(
        _generate_image_sync,
        prompt,
        seed,
    )

    temp = tempfile.NamedTemporaryFile(prefix="x-post-", suffix=suffix, delete=False)
    try:
        temp.write(image_bytes)
        temp.flush()
    finally:
        temp.close()

    return Path(temp.name)
