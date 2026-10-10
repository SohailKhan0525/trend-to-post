from __future__ import annotations

import html
import json
import random
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "TrendToPostBot/1.0 (licensed-photo discovery; educational use)"
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_SUBJECT_SEARCHES = 8
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}

# Public-facing people across AI/tech, gaming, sport and internet culture.
# Wikimedia Commons is used because its file pages expose reuse-license metadata.
PERSON_SUBJECTS = (
    ("Sam Altman", "AI"),
    ("Jensen Huang", "AI hardware"),
    ("Satya Nadella", "technology"),
    ("Sundar Pichai", "technology"),
    ("Tim Cook", "technology"),
    ("Lisa Su", "AI hardware"),
    ("Mark Zuckerberg", "technology"),
    ("Elon Musk", "technology"),
    ("Linus Torvalds", "software"),
    ("Gabe Newell", "gaming"),
    ("Phil Spencer", "gaming"),
    ("Hideo Kojima", "gaming"),
    ("Shigeru Miyamoto", "gaming"),
    ("Todd Howard", "gaming"),
    ("Marques Brownlee", "technology creator"),
    ("Linus Sebastian", "technology creator"),
    ("MrBeast", "internet creator"),
    ("Cristiano Ronaldo", "sport"),
    ("Lionel Messi", "sport"),
    ("LeBron James", "sport"),
    ("Stephen Curry", "sport"),
    ("Serena Williams", "sport"),
    ("Roger Federer", "sport"),
    ("Taylor Swift", "music"),
    ("Keanu Reeves", "entertainment"),
    ("Ryan Reynolds", "entertainment"),
    ("Zendaya", "entertainment"),
    ("Tom Holland", "entertainment"),
    ("Dwayne Johnson", "entertainment"),
    ("Pedro Pascal", "entertainment"),
)


class PersonImageError(RuntimeError):
    pass


def _clean_html(value: object) -> str:
    raw = html.unescape(str(value or ""))
    raw = re.sub(r"<[^>]+>", " ", raw)
    return " ".join(raw.split()).strip()


def _get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=8) as response:
            payload = response.read(3 * 1024 * 1024 + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise PersonImageError(f"Wikimedia Commons request failed: {exc}") from exc
    if len(payload) > 3 * 1024 * 1024:
        raise PersonImageError("Wikimedia Commons returned an unexpectedly large response.")
    try:
        result = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PersonImageError("Wikimedia Commons returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise PersonImageError("Wikimedia Commons returned an invalid response.")
    return result


def _license_allowed(short_name: str, usage_terms: str = "") -> bool:
    value = " ".join(f"{short_name} {usage_terms}".casefold().split())
    if not value or any(term in value for term in (
        "noncommercial", "non-commercial", "no derivatives", "no-derivatives",
        "fair use", "all rights reserved", "permission required",
    )):
        return False
    return (
        "public domain" in value
        or "cc0" in value
        or "cc by" in value
        or "creative commons attribution" in value
        or "attribution-sharealike" in value
        or "attribution sharealike" in value
    )


def _needs_attribution(short_name: str) -> bool:
    value = short_name.casefold()
    return "public domain" not in value and "cc0" not in value


def _person_matches(name: str, title: str, description: str) -> bool:
    haystack = " ".join(f"{title} {description}".casefold().split())
    words = [word.casefold() for word in re.findall(r"[A-Za-z0-9]+", name) if len(word) >= 3]
    return bool(words) and all(re.search(rf"\b{re.escape(word)}\b", haystack) for word in words)


def _metadata_value(metadata: dict, key: str) -> str:
    item = metadata.get(key, {})
    if isinstance(item, dict):
        return _clean_html(item.get("value") or item.get("raw") or "")
    return _clean_html(item)


def _candidate_from_page(page: dict, person: tuple[str, str]) -> dict | None:
    title = str(page.get("title", "")).strip()
    infos = page.get("imageinfo") or []
    if not title.startswith("File:") or not infos or not isinstance(infos[0], dict):
        return None
    info = infos[0]
    metadata = info.get("extmetadata") or {}
    if not isinstance(metadata, dict):
        return None

    mime = str(info.get("thumbmime") or info.get("mime") or "").lower().strip()
    thumbnail_url = str(info.get("thumburl") or info.get("url") or "").strip()
    if mime not in ALLOWED_IMAGE_MIMES or not thumbnail_url.startswith("https://"):
        return None
    if int(info.get("thumbwidth") or info.get("width") or 0) < 400:
        return None

    short_name = _metadata_value(metadata, "LicenseShortName")
    usage_terms = _metadata_value(metadata, "UsageTerms")
    if not _license_allowed(short_name, usage_terms):
        return None

    description = _metadata_value(metadata, "ImageDescription")
    if not _person_matches(person[0], title, description):
        return None

    artist = (
        _metadata_value(metadata, "Attribution")
        or _metadata_value(metadata, "Artist")
        or _metadata_value(metadata, "Credit")
    )
    if _needs_attribution(short_name) and not artist:
        return None

    file_page_url = str(info.get("descriptionurl") or "").strip()
    if not file_page_url:
        file_page_url = "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"), safe=":/()")
    license_url = _metadata_value(metadata, "LicenseUrl")
    return {
        "subject": person[0],
        "category": person[1],
        "title": title,
        "description": description or title.removeprefix("File:"),
        "thumbnail_url": thumbnail_url,
        "mime": mime,
        "file_page_url": file_page_url,
        "license_name": short_name,
        "license_url": license_url,
        "artist": artist,
        "needs_attribution": _needs_attribution(short_name),
    }


def find_licensed_person_image(
    used_file_titles: list[str] | tuple[str, ...] = (),
    excluded_subjects: list[str] | tuple[str, ...] = (),
) -> dict:
    """Discover a real portrait on Wikimedia Commons and verify its reuse license.

    Images with missing metadata, restrictive/non-commercial/no-derivatives licenses,
    non-photo vector formats, or weak subject matches are skipped.
    """
    used_titles = {str(value).casefold().strip() for value in used_file_titles}
    excluded = {str(value).casefold().strip() for value in excluded_subjects}
    subjects = [item for item in PERSON_SUBJECTS if item[0].casefold() not in excluded]
    random.shuffle(subjects)
    searched = 0

    for person in subjects:
        if searched >= MAX_SUBJECT_SEARCHES:
            break
        searched += 1
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrsearch": f'"{person[0]}" portrait',
            "gsrlimit": "20",
            "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
            "iiurlwidth": "1600",
            "iiextmetadatafilter": "ImageDescription|LicenseShortName|UsageTerms|LicenseUrl|Artist|Attribution|Credit",
        }
        try:
            data = _get_json(COMMONS_API + "?" + urlencode(params))
        except PersonImageError as exc:
            print(f"Skipping Commons subject search for {person[0]}: {exc}")
            continue

        pages = data.get("query", {}).get("pages", [])
        if not isinstance(pages, list):
            continue
        candidates = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            candidate = _candidate_from_page(page, person)
            if candidate and candidate["title"].casefold() not in used_titles:
                candidates.append(candidate)
        if candidates:
            return random.choice(candidates)

    raise PersonImageError(
        "No unused real-person portrait with suitable, machine-readable reuse terms "
        "was found in the Commons search batch."
    )


def download_image_bytes(image: dict) -> bytes:
    """Download a previously vetted Commons thumbnail with size and host checks."""
    url = str(image.get("thumbnail_url", "")).strip()
    if not url.startswith("https://"):
        raise PersonImageError("Image URL must use HTTPS.")
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").casefold()
    if host not in {"upload.wikimedia.org", "commons.wikimedia.org"}:
        raise PersonImageError("Image URL is not hosted by Wikimedia Commons.")

    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=15) as response:
            final_host = (urlparse(response.geturl()).hostname or "").casefold()
            if final_host not in {"upload.wikimedia.org", "commons.wikimedia.org"}:
                raise PersonImageError("Image download redirected outside Wikimedia Commons.")
            data = response.read(MAX_IMAGE_BYTES + 1)
            content_type = response.headers.get_content_type().lower()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise PersonImageError(f"Image download failed: {exc}") from exc

    if len(data) > MAX_IMAGE_BYTES:
        raise PersonImageError("Image exceeded the 6 MiB upload safety limit.")
    if not data:
        raise PersonImageError("Image download was empty.")
    if content_type not in ALLOWED_IMAGE_MIMES:
        # Some image CDN responses omit a reliable Content-Type. In that case,
        # trust only the MIME type already returned by the vetted Commons API.
        content_type = str(image.get("mime", "")).lower().strip()
    if content_type not in ALLOWED_IMAGE_MIMES:
        raise PersonImageError(f"Unsupported image content type: {content_type or 'unknown'}.")
    if not (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    ):
        raise PersonImageError("Downloaded file did not match a supported image format.")
    image["mime"] = content_type
    return data


def attribution_reply(image: dict) -> str:
    """Create concise attribution text for a self-reply when the license requires it."""
    artist = re.sub(r"\s+", " ", str(image.get("artist") or "Unknown creator")).strip()
    artist = re.sub(r"<[^>]+>", "", html.unescape(artist))
    license_name = str(image.get("license_name") or "license listed on source page").strip()
    license_url = str(image.get("license_url") or "").strip()
    file_url = str(image.get("file_page_url") or "").strip()
    fixed = f"License: {license_name}. "
    if license_url:
        fixed += f"{license_url} "
    fixed += file_url
    max_artist = max(20, 280 - len("Photo credit: . ") - len(fixed))
    artist = artist[:max_artist].rstrip()
    result = f"Photo credit: {artist}. {fixed}".strip()
    return result[:280]
