from __future__ import annotations

import base64
import io
import json
import os
import random
import textwrap
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


IMAGE_MODEL = os.environ.get(
    "CLOUDFLARE_IMAGE_MODEL",
    "@cf/black-forest-labs/flux-1-schnell",
)
IMAGE_API_URL = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
)
WIDTH = 1080
HEIGHT = 1350
HEADER_HEIGHT = 400
BACKGROUND = (13, 15, 21)
PANEL = (22, 25, 33)
USER_BUBBLE = (49, 53, 64)
BOT_BUBBLE = (33, 37, 48)
ACCENT = (143, 159, 255)
INK = (242, 244, 249)
MUTED = (164, 172, 188)
LABEL = (255, 197, 114)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cloudflare_background(prompt: str) -> Image.Image | None:
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not token or not prompt:
        return None

    url = IMAGE_API_URL.format(
        account_id=account_id,
        model=IMAGE_MODEL,
    )
    payload = {
        "prompt": (
            "Create a playful, high-quality editorial illustration to sit behind a fictional chat meme. "
            "No text, no letters, no logos, no brand marks, no watermarks, no interface. "
            + prompt[:1400]
        ),
        "steps": 4,
        "seed": random.randint(1, 2_000_000_000),
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "").lower()

        image_bytes = raw
        if "json" in content_type or raw.lstrip().startswith(b"{"):
            response_json = json.loads(raw.decode("utf-8"))
            result = response_json.get("result") or {}
            encoded = result.get("image") if isinstance(result, dict) else None
            if response_json.get("success") is False:
                raise ValueError(str(response_json.get("errors") or "Workers AI image error"))
            if not isinstance(encoded, str) or not encoded:
                raise ValueError("Workers AI returned no base64 image.")
            image_bytes = base64.b64decode(encoded, validate=False)

        with Image.open(io.BytesIO(image_bytes)) as image:
            return image.convert("RGB")
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Cloudflare image generation unavailable; using rendered-only meme art: {exc}")
        return None


def _fit_background(image: Image.Image | None) -> Image.Image:
    if image is not None:
        try:
            top = ImageOps.fit(image, (WIDTH, HEADER_HEIGHT), method=Image.Resampling.LANCZOS)
            overlay = Image.new("RGBA", top.size, (7, 9, 15, 100))
            top = Image.alpha_composite(top.convert("RGBA"), overlay).convert("RGB")
            return top
        except Exception as exc:
            print(f"Could not place generated background; using vector fallback: {exc}")

    top = Image.new("RGB", (WIDTH, HEADER_HEIGHT), (25, 28, 42))
    pixels = top.load()
    for y in range(HEADER_HEIGHT):
        blend = y / max(1, HEADER_HEIGHT - 1)
        color = (
            int(25 + 10 * blend),
            int(28 + 5 * blend),
            int(42 + 18 * blend),
        )
        for x in range(WIDTH):
            pixels[x, y] = color

    draw = ImageDraw.Draw(top, "RGBA")
    draw.ellipse((730, -170, 1190, 290), fill=(120, 128, 250, 55))
    draw.ellipse((820, 160, 1140, 480), fill=(255, 188, 104, 30))
    draw.rounded_rectangle((80, 175, 405, 265), radius=36, fill=(255, 255, 255, 15))
    return top


def _wrap_pixels(text: str, font: ImageFont.ImageFont, draw: ImageDraw.ImageDraw, max_width: int) -> list[str]:
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_meme_image(
    conversation: list[dict],
    image_prompt: str,
    subject: str,
    output_path: str | Path,
) -> Path:
    """Render a clearly labeled fictional chat meme with crisp text and optional FLUX artwork."""
    background = _cloudflare_background(image_prompt)
    top = _fit_background(background)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    canvas.paste(top, (0, 0))
    draw = ImageDraw.Draw(canvas)

    # This label prevents the generated conversation from being mistaken for a real transcript.
    badge_font = _font(20, bold=True)
    badge = "FICTIONAL CHAT  •  PARODY"
    badge_box = draw.textbbox((0, 0), badge, font=badge_font)
    badge_width = badge_box[2] - badge_box[0] + 42
    draw.rounded_rectangle((58, 48, 58 + badge_width, 94), radius=20, fill=(13, 15, 21))
    draw.text((79, 59), badge, font=badge_font, fill=LABEL)

    subject_font = _font(42, bold=True)
    clean_subject = " ".join(str(subject or "INTERNET SUPPORT").split())[:48]
    draw.text((62, 130), clean_subject, font=subject_font, fill=INK)
    draw.text((64, 194), "CASE FILE  /  NORMAL REQUEST, UNNORMAL OUTCOME", font=_font(17, bold=True), fill=(231, 234, 245))

    # Put a solid panel over the lower part so dialogue remains high contrast on any generated art.
    draw.rounded_rectangle((28, 360, WIDTH - 28, HEIGHT - 38), radius=34, fill=PANEL)
    draw.text((64, 387), "CHAT LOG", font=_font(17, bold=True), fill=ACCENT)
    draw.line((64, 424, WIDTH - 64, 424), fill=(53, 58, 72), width=2)

    speaker_font = _font(17, bold=True)
    body_font = _font(29)
    y = 448
    bottom_limit = HEIGHT - 95
    messages = conversation[:5]

    for index, message in enumerate(messages):
        speaker = str(message.get("speaker", "AI")).strip().upper()
        body = " ".join(str(message.get("text", "")).split())
        if not body:
            continue

        is_user = speaker in {"USER", "PLAYER"}
        left = 305 if is_user else 62
        right = WIDTH - 62 if is_user else 800
        max_text_width = right - left - 42
        lines = _wrap_pixels(body, body_font, draw, max_text_width)
        if len(lines) > 4:
            lines = lines[:4]
            lines[-1] = lines[-1].rstrip(" .") + "…"

        line_heights = [
            draw.textbbox((0, 0), line or "Ag", font=body_font)[3]
            for line in lines
        ]
        bubble_height = 24 + 24 + sum(line_heights) + max(0, len(lines) - 1) * 7
        if y + bubble_height > bottom_limit:
            # Reduce body size if the last one or two messages would overflow.
            body_font = _font(25)
            lines = _wrap_pixels(body, body_font, draw, max_text_width)
            if len(lines) > 4:
                lines = lines[:4]
                lines[-1] = lines[-1].rstrip(" .") + "…"
            line_heights = [draw.textbbox((0, 0), line or "Ag", font=body_font)[3] for line in lines]
            bubble_height = 24 + 24 + sum(line_heights) + max(0, len(lines) - 1) * 7
        if y + bubble_height > bottom_limit:
            break

        bubble_color = USER_BUBBLE if is_user else BOT_BUBBLE
        draw.rounded_rectangle(
            (left, y, right, y + bubble_height),
            radius=24,
            fill=bubble_color,
            outline=(63, 69, 86),
            width=2,
        )
        draw.text((left + 21, y + 14), speaker, font=speaker_font, fill=ACCENT if not is_user else LABEL)
        body_y = y + 42
        for line, line_height in zip(lines, line_heights):
            draw.text((left + 21, body_y), line, font=body_font, fill=INK)
            body_y += line_height + 7
        y += bubble_height + 17

    footer = "FICTIONAL SCENARIO • NOT A REAL CHAT OR COMPANY STATEMENT"
    footer_font = _font(14, bold=True)
    footer_width = draw.textbbox((0, 0), footer, font=footer_font)[2]
    draw.text(((WIDTH - footer_width) // 2, HEIGHT - 68), footer, font=footer_font, fill=MUTED)

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, format="PNG", optimize=True)
    return path
