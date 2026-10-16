from __future__ import annotations

import hashlib
import io
import re
import xml.sax.saxutils as xml_escape

from PIL import Image, ImageFilter, ImageEnhance
import cairosvg


class GeneratedImageError(RuntimeError):
    """Raised when a local comparison image cannot be rendered safely."""


IMAGE_WIDTH = 1200
IMAGE_HEIGHT = 900
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _split_comparison(headline: str) -> tuple[str, str]:
    cleaned = " ".join(str(headline).strip().rstrip("?!").split())
    for separator in (" or ", " versus ", " vs "):
        parts = re.split(re.escape(separator), cleaned, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip()[:80], parts[1].strip()[:80]
    raise GeneratedImageError("Comparison headline must contain two choices separated by 'or', 'vs', or 'versus'.")


def _kind(label: str) -> str:
    value = label.casefold()
    if any(word in value for word in ("dark mode", "light mode", "ui", "ux", "design", "figma")):
        return "interface"
    if any(word in value for word in ("ai", "gpt", "claude", "gemini", "llm", "agent", "model", "copilot")):
        return "brain"
    if any(word in value for word in ("code", "coding", "developer", "dev", "cursor", "ide", "terminal", "vscode", "github", "python")):
        return "code"
    if any(word in value for word in ("cloud", "server", "database", "backend", "kubernetes", "docker")):
        return "cloud"
    if any(word in value for word in ("phone", "mobile", "android", "iphone", "app")):
        return "device"
    return "spark"


def _svg_scene(left: str, right: str) -> str:
    left_kind, right_kind = _kind(left), _kind(right)
    # These SVGs are generated locally. User-supplied labels influence only the
    # small, escaped accessibility description and never appear as image text.
    safe_left = xml_escape.escape(left)
    safe_right = xml_escape.escape(right)

    def subject(kind: str, x: int, color: str, accent: str, flip: bool = False) -> str:
        transform = f"translate({x} 0)" + (" scale(-1 1) translate(-300 0)" if flip else "")
        if kind == "code":
            shape = f"""
              <rect x="62" y="286" width="176" height="140" rx="24" fill="#111827" stroke="{accent}" stroke-width="5"/>
              <path d="M112 332 L88 356 L112 380 M188 332 L212 356 L188 380 M160 322 L140 390" fill="none" stroke="{color}" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="150" cy="250" r="40" fill="{accent}" opacity=".9"/>
              <path d="M130 250 L145 265 L174 232" fill="none" stroke="#fff" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>
            """
        elif kind == "brain":
            shape = f"""
              <path d="M150 250 C108 211 73 248 88 280 C55 310 80 349 108 351 C103 391 143 409 163 381 C190 408 226 386 217 351 C251 339 252 300 222 281 C237 245 194 215 169 247 C163 237 156 237 150 250Z" fill="{accent}" stroke="{color}" stroke-width="6"/>
              <path d="M154 253 L154 375 M119 278 L146 302 L119 328 M190 274 L169 300 L193 322 M127 354 L148 340 L174 359" fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="80" cy="235" r="12" fill="{color}"/><circle cx="230" cy="235" r="10" fill="{accent}"/><circle cx="228" cy="390" r="13" fill="{color}"/>
            """
        elif kind == "interface":
            shape = f"""
              <rect x="64" y="236" width="174" height="180" rx="22" fill="#fff" stroke="{color}" stroke-width="7"/>
              <rect x="64" y="236" width="174" height="38" rx="18" fill="{accent}"/>
              <circle cx="86" cy="255" r="5" fill="#fff"/><circle cx="104" cy="255" r="5" fill="#fff"/><circle cx="122" cy="255" r="5" fill="#fff"/>
              <rect x="84" y="294" width="62" height="88" rx="10" fill="{accent}" opacity=".8"/>
              <rect x="158" y="294" width="60" height="16" rx="8" fill="{color}" opacity=".9"/>
              <rect x="158" y="320" width="48" height="9" rx="4" fill="#94a3b8"/>
              <rect x="158" y="338" width="54" height="9" rx="4" fill="#cbd5e1"/>
              <circle cx="218" cy="216" r="30" fill="{color}"/><path d="M204 216 L215 227 L234 205" fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
            """
        elif kind == "cloud":
            shape = f"""
              <path d="M88 356 C54 356 48 311 79 295 C79 249 129 232 158 260 C187 226 239 252 233 291 C269 309 250 356 219 356Z" fill="{accent}" stroke="{color}" stroke-width="7"/>
              <rect x="116" y="316" width="70" height="58" rx="10" fill="#fff" stroke="{color}" stroke-width="5"/>
              <path d="M133 335 L169 335 M133 348 L160 348 M133 361 L151 361" stroke="{color}" stroke-width="5" stroke-linecap="round"/>
              <circle cx="88" cy="244" r="12" fill="{color}"/><circle cx="225" cy="237" r="9" fill="{accent}"/>
            """
        elif kind == "device":
            shape = f"""
              <rect x="96" y="220" width="120" height="210" rx="26" fill="#111827" stroke="{color}" stroke-width="7"/>
              <rect x="108" y="246" width="96" height="150" rx="13" fill="{accent}"/>
              <circle cx="156" cy="413" r="6" fill="#fff"/>
              <circle cx="155" cy="315" r="30" fill="#fff" opacity=".92"/>
              <path d="M140 315 L152 327 L173 300" fill="none" stroke="{color}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="84" cy="242" r="17" fill="{accent}"/><circle cx="228" cy="386" r="13" fill="{color}"/>
            """
        else:
            shape = f"""
              <path d="M150 215 L177 292 L256 320 L177 347 L150 425 L123 347 L44 320 L123 292Z" fill="{accent}" stroke="{color}" stroke-width="6" stroke-linejoin="round"/>
              <circle cx="72" cy="240" r="15" fill="{color}"/><circle cx="235" cy="240" r="11" fill="{accent}"/>
              <circle cx="230" cy="400" r="16" fill="{color}"/><path d="M88 385 L103 400 L88 415 L73 400Z" fill="{accent}"/>
            """
        return f'<g transform="{transform}">{shape}</g>'

    # A diagonal seam and separated panels keep the choices legible without any text.
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{IMAGE_WIDTH}" height="{IMAGE_HEIGHT}" viewBox="0 0 {IMAGE_WIDTH} {IMAGE_HEIGHT}" role="img" aria-label="Text-free illustration comparing {safe_left} and {safe_right}">
      <defs>
        <linearGradient id="bgL" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#172554"/><stop offset="1" stop-color="#2563eb"/></linearGradient>
        <linearGradient id="bgR" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3f1d5a"/><stop offset="1" stop-color="#db2777"/></linearGradient>
        <linearGradient id="panel" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff" stop-opacity=".22"/><stop offset="1" stop-color="#ffffff" stop-opacity=".06"/></linearGradient>
        <filter id="shadow" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="20" stdDeviation="18" flood-color="#020617" flood-opacity=".28"/></filter>
      </defs>
      <rect width="600" height="900" fill="url(#bgL)"/><rect x="600" width="600" height="900" fill="url(#bgR)"/>
      <circle cx="80" cy="110" r="190" fill="#60a5fa" opacity=".12"/><circle cx="1120" cy="780" r="240" fill="#f9a8d4" opacity=".12"/>
      <path d="M0 640 C190 550 360 760 600 650 L600 900 L0 900Z" fill="#020617" opacity=".12"/>
      <path d="M600 650 C820 750 1000 550 1200 640 L1200 900 L600 900Z" fill="#020617" opacity=".14"/>
      <g filter="url(#shadow)"><rect x="66" y="110" width="468" height="680" rx="44" fill="url(#panel)" stroke="#ffffff" stroke-opacity=".22" stroke-width="2"/><rect x="666" y="110" width="468" height="680" rx="44" fill="url(#panel)" stroke="#ffffff" stroke-opacity=".22" stroke-width="2"/></g>
      <g transform="translate(84 140)">{subject(left_kind, 0, "#bfdbfe", "#38bdf8")}</g>
      <g transform="translate(684 140)">{subject(right_kind, 0, "#fce7f3", "#fb7185")}</g>
      <path d="M600 0 L600 900" stroke="#ffffff" stroke-opacity=".5" stroke-width="3" stroke-dasharray="12 18"/>
      <circle cx="600" cy="450" r="43" fill="#fff" opacity=".95"/><path d="M583 450 L594 461 L617 438" fill="none" stroke="#334155" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
      <g fill="#fff" opacity=".65"><circle cx="104" cy="690" r="5"/><circle cx="132" cy="720" r="3"/><circle cx="1090" cy="205" r="5"/><circle cx="1060" cy="235" r="3"/><path d="M480 175 l9 18 18 9 -18 9 -9 18 -9 -18 -18 -9 18 -9Z"/><path d="M724 665 l6 12 12 6 -12 6 -6 12 -6 -12 -12 -6 12 -6Z"/></g>
    </svg>'''


def generate_comparison_image(headline: str, question: str = "") -> bytes:
    """Render an original comparison illustration locally as a PNG using SVG, CairoSVG and Pillow.

    No API key, network call, remote image service, or generated-image quota is required.
    The headline is used only to choose visual motifs; no text is rendered into the image.
    """
    try:
        left, right = _split_comparison(headline)
        svg = _svg_scene(left, right)
        rendered = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            output_width=IMAGE_WIDTH,
            output_height=IMAGE_HEIGHT,
        )
        with Image.open(io.BytesIO(rendered)) as source:
            image = source.convert("RGB")
        # Pillow normalizes dimensions/color mode and applies a very subtle finish.
        image = ImageEnhance.Contrast(image).enhance(1.025)
        image = image.filter(ImageFilter.SMOOTH)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        data = output.getvalue()
    except GeneratedImageError:
        raise
    except Exception as exc:
        raise GeneratedImageError(f"Local SVG/PNG image rendering failed: {exc}") from exc

    if not data.startswith(PNG_SIGNATURE):
        raise GeneratedImageError("Local image renderer did not produce a valid PNG.")
    if len(data) > 6 * 1024 * 1024:
        raise GeneratedImageError("Rendered PNG exceeds the 6 MiB upload limit.")
    return data
