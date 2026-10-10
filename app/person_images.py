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

# Candidate people are known public-facing AI/technology leaders, engineers,
# researchers, and builders with an X handle. Before looking for a photo, the
# queue verifies that the handle has recent posts on X. Tuple fields:
# (full search name, short display name, topic, X handle without @, aliases)
PERSON_SUBJECTS = (
    ("Thibault Sottiaux", "Tibo", "Codex / AI engineering", "thsottiaux", ("Tibo",)),
    ("Sam Altman", "Sam Altman", "AI", "sama", ()),
    ("Greg Brockman", "Greg Brockman", "AI engineering", "gdb", ()),
    ("Mira Murati", "Mira Murati", "AI engineering", "miramurati", ()),
    ("Andrej Karpathy", "Andrej Karpathy", "AI research", "karpathy", ()),
    ("Alexandr Wang", "Alexandr Wang", "AI", "alexandr_wang", ()),
    ("Noam Brown", "Noam Brown", "AI research", "polynoamial", ()),
    ("Ilya Sutskever", "Ilya Sutskever", "AI research", "ilyasut", ()),
    ("John Schulman", "John Schulman", "AI research", "johnschulman2", ()),
    ("Dario Amodei", "Dario Amodei", "AI research", "DarioAmodei", ()),
    ("Amanda Askell", "Amanda Askell", "AI alignment", "AmandaAskell", ()),
    ("Boris Cherny", "Boris Cherny", "coding tools", "bcherny", ()),
    ("Mitchell Hashimoto", "Mitchell Hashimoto", "developer tools", "mitchellh", ()),
    ("Guillermo Rauch", "Guillermo Rauch", "developer tools", "rauchg", ()),
    ("Pieter Levels", "Pieter Levels", "indie software", "levelsio", ()),
    ("Simon Willison", "Simon Willison", "AI / software", "simonw", ()),
    ("Andrew Ng", "Andrew Ng", "AI research", "AndrewYNg", ()),
    ("Fei-Fei Li", "Fei-Fei Li", "AI research", "drfeifei", ()),
    ("Yann LeCun", "Yann LeCun", "AI research", "ylecun", ()),
    ("Demis Hassabis", "Demis Hassabis", "AI research", "demishassabis", ()),
    ("Jensen Huang", "Jensen Huang", "AI hardware", "jensenhuang", ()),
    ("Lisa Su", "Lisa Su", "AI hardware", "LisaSu", ()),
    ("Satya Nadella", "Satya Nadella", "technology", "satyanadella", ()),
    ("Sundar Pichai", "Sundar Pichai", "technology", "sundarpichai", ()),
    ("Tim Cook", "Tim Cook", "technology", "tim_cook", ()),
    ("Marques Brownlee", "Marques Brownlee", "technology creator", "MKBHD", ()),
    ("Harrison Chase", "Harrison Chase", "AI developer tools", "hwchase17", ()),
    ("DHH", "DHH", "software builder", "dhh", ("David Heinemeier Hansson",)),
    ("Tobi Lutke", "Tobi Lutke", "technology founder", "tobi", ("Tobi Lütke",)),
    ("Paul Graham", "Paul Graham", "startup founder", "paulg", ()),
    ("Theo Browne", "Theo", "developer creator", "theo", ("Theo",)),
    ("Linus Torvalds", "Linus Torvalds", "software", "Linus__Torvalds", ()),
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
        "noncommercial", "non-commercial", "cc by-nc", "cc-by-nc", "by-nc",
        "no derivatives", "no-derivatives", "cc by-nd", "cc-by-nd", "by-nd",
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


def _candidate_from_page(page: dict, person: tuple) -> dict | None:
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
    names_to_match = (person[0], person[1], *person[4])
    if not any(_person_matches(name, title, description) for name in names_to_match):
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
        "subject": person[1],
        "search_name": person[0],
        "category": person[2],
        "x_handle": person[3],
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
    eligible_x_handles: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Discover a real portrait on Wikimedia Commons and verify its reuse license.

    Images with missing metadata, restrictive/non-commercial/no-derivatives licenses,
    non-photo vector formats, or weak subject matches are skipped.
    """
    used_titles = {str(value).casefold().strip() for value in used_file_titles}
    excluded = {str(value).casefold().strip() for value in excluded_subjects}
    eligible = {
        str(value).casefold().lstrip("@").strip()
        for value in (eligible_x_handles or [])
        if str(value).strip()
    }
    subjects = [
        item for item in PERSON_SUBJECTS
        if item[1].casefold() not in excluded
        and item[0].casefold() not in excluded
        and (not eligible or item[3].casefold() in eligible)
    ]
    if eligible_x_handles is not None and not subjects:
        raise PersonImageError("No active X people remain after applying today's exclusions.")
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
            "gsrsearch": f'("{person[0]}" OR "{person[1]}") portrait',
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
