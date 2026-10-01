from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request

MODEL = "gemini-3.5-flash-lite"
CLOUDFLARE_MODEL = os.environ.get("CLOUDFLARE_AI_MODEL", "@cf/zai-org/glm-4.7-flash")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
CLOUDFLARE_API_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
MAX_QUOTE_CHARS = 260
MAX_API_ATTEMPTS_PER_KEY = 3
RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}


class GeminiError(RuntimeError):
    pass


class QuoteDraft:
    __slots__ = ("should_quote", "quote_text", "angle")

    def __init__(self, should_quote: bool, quote_text: str, angle: str) -> None:
        self.should_quote = should_quote
        self.quote_text = quote_text
        self.angle = angle


def _api_keys() -> list[str]:
    keys: list[str] = []
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_BACKUP"):
        value = os.environ.get(name, "").strip()
        if value and value not in keys:
            keys.append(value)
    if not keys:
        raise GeminiError("GEMINI_API_KEY or GEMINI_API_KEY_BACKUP is required.")
    return keys


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After")
    if retry_after:
        try:
            return min(max(float(retry_after), 1.0), 60.0)
        except ValueError:
            pass
    return min((2 ** (attempt - 1)) + random.uniform(0, 0.5), 30.0)


def _post_json(payload: dict) -> dict:
    keys = _api_keys()
    last_error: GeminiError | None = None

    for key_index, api_key in enumerate(keys, start=1):
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )

        for attempt in range(1, MAX_API_ATTEMPTS_PER_KEY + 1):
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                detail_lower = detail.lower()
                last_error = GeminiError(
                    f"Gemini request failed with HTTP {exc.code}: {detail[:1200]}"
                )

                if exc.code == 429:
                    # A backup key can be useful when it belongs to another
                    # Google Cloud/AI Studio project. It cannot bypass a quota
                    # shared by the same project.
                    if key_index < len(keys):
                        print(
                            f"Gemini key {key_index} hit HTTP 429; "
                            "trying backup key."
                        )
                        break

                    if "quota_exceeded" in detail_lower or "exceeded your current quota" in detail_lower:
                        raise GeminiError(
                            "Gemini quota is exhausted for the configured project. "
                            "The bot will not spin on retries; wait for the quota reset "
                            "or use a project/key with available quota."
                        ) from exc

                    if attempt < MAX_API_ATTEMPTS_PER_KEY:
                        delay = _retry_delay(exc, attempt)
                        print(
                            f"Gemini rate limited (429); retrying in "
                            f"{delay:.1f}s ({attempt}/{MAX_API_ATTEMPTS_PER_KEY})."
                        )
                        time.sleep(delay)
                        continue
                    raise last_error from exc

                if exc.code in RETRYABLE_HTTP_CODES and attempt < MAX_API_ATTEMPTS_PER_KEY:
                    delay = _retry_delay(exc, attempt)
                    print(
                        f"Gemini HTTP {exc.code}; retrying in "
                        f"{delay:.1f}s ({attempt}/{MAX_API_ATTEMPTS_PER_KEY})."
                    )
                    time.sleep(delay)
                    continue

                raise last_error from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = GeminiError(f"Gemini request failed: {exc}")
                if attempt < MAX_API_ATTEMPTS_PER_KEY:
                    delay = min((2 ** (attempt - 1)) + random.uniform(0, 0.5), 30.0)
                    time.sleep(delay)
                    continue
                raise last_error from exc
            except json.JSONDecodeError as exc:
                raise GeminiError("Gemini returned invalid JSON.") from exc

    raise last_error or GeminiError("Gemini request failed unexpectedly.")


def _extract_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback") or payload.get("error")
        raise GeminiError(f"Gemini returned no candidates: {feedback}")

    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "\n".join(
        str(part.get("text", ""))
        for part in parts
        if isinstance(part, dict) and part.get("text")
    ).strip()
    if not text:
        raise GeminiError("Gemini returned an empty response.")
    return text



def _prompt(source: dict) -> str:
    return f"""Write ONE X quote-post about the source post below.

This is not a news article, summary, thought-leadership post, or corporate update.
It is one person reacting to something they just saw on the timeline.

NON-NEGOTIABLE STYLE:
- Make it genuinely funny or sharply witty. Dry humor, absurdity, understatement, internet humor, or a clever observation are fine.
- No generic AI phrasing. Never say "this highlights", "it's worth noting", "in today's world", "a reminder that", "the implications are", "fascinating", "interesting development", or similar filler.
- No corporate, motivational, polished LinkedIn, or engagement-bait voice.
- No "What do you think?", "Thoughts?", "Agree?", and no hashtags unless essential to the joke.
- Do not explain the joke. Do not invent facts or context. Do not manufacture outrage.
- Keep it under {MAX_QUOTE_CHARS} characters.

QUOTE REQUIREMENT:
- Include a short verbatim fragment from the source, normally 2-8 words, in quotation marks.
- Then react to that fragment with the funny/clever observation.
- The quoted fragment must actually appear in SOURCE POST.
- Never quote more than 8 consecutive words.
- If there is no usable fragment or the source is weak, return should_quote=false.

Return JSON only:
{{
  "should_quote": true,
  "quote_text": "\\"short source fragment\\" + your funny reaction",
  "angle": "brief description of the joke/observation"
}}

SOURCE TREND: {source['trend']}
SOURCE AUTHOR: @{source['username']}
SOURCE POST:
{source['text']}
"""


def _validate_quote_against_source(draft: QuoteDraft, source: dict) -> QuoteDraft:
    if not draft.should_quote:
        return draft
    text = draft.quote_text
    source_text = str(source["text"])
    fragments = []
    start = 0
    while True:
        left = text.find('"', start)
        if left < 0:
            break
        right = text.find('"', left + 1)
        if right < 0:
            break
        fragment = text[left + 1:right].strip()
        if fragment:
            fragments.append(fragment)
        start = right + 1
    if not fragments:
        raise GeminiError("Generated post contains no quoted source fragment.")
    if not any(fragment in source_text for fragment in fragments):
        raise GeminiError("Generated quote fragment does not exist in the source post.")
    return draft


def generate_quote(source: dict) -> QuoteDraft:
    prompt = _prompt(source)
    try:
        return _validate_quote_against_source(
            _parse_quote_json(_cloudflare_text(prompt)),
            source,
        )
    except GeminiError as cloudflare_error:
        if not _api_keys():
            raise cloudflare_error
        print(
            "Cloudflare generation failed; trying Gemini 3.5 Flash-Lite fallback: "
            f"{cloudflare_error}"
        )

    schema = {
        "type": "OBJECT",
        "properties": {
            "should_quote": {"type": "BOOLEAN"},
            "quote_text": {"type": "STRING"},
            "angle": {"type": "STRING"},
        },
        "required": ["should_quote", "quote_text", "angle"],
    }

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": 350,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }

    return _validate_quote_against_source(
        _parse_quote_json(_extract_text(_post_json(payload))),
        source,
    )

def _parse_quote_json(text: str) -> QuoteDraft:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiError(
            f"Gemini quote response was not valid JSON: {text[:800]}"
        ) from exc

    if not isinstance(data, dict):
        raise GeminiError("Gemini quote response was not a JSON object.")

    should_quote = bool(data.get("should_quote"))
    quote_text = " ".join(str(data.get("quote_text", "")).split()).strip()
    angle = " ".join(str(data.get("angle", "")).split()).strip()

    if should_quote and not quote_text:
        raise GeminiError("Gemini marked the source quotable but returned no quote text.")

    if quote_text and len(quote_text) > MAX_QUOTE_CHARS:
        raise GeminiError(
            f"Gemini quote text is {len(quote_text)} characters; "
            f"expected {MAX_QUOTE_CHARS} or fewer."
        )

    return QuoteDraft(
        should_quote=should_quote,
        quote_text=quote_text,
        angle=angle,
    )



def _cloudflare_credentials() -> tuple[str, str]:
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not token:
        raise GeminiError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required.")
    return account_id, token


def _cloudflare_text(prompt: str) -> str:
    account_id, token = _cloudflare_credentials()
    url = CLOUDFLARE_API_URL.format(account_id=account_id, model=CLOUDFLARE_MODEL)
    payload = {
        "messages": [
            {"role": "system", "content": "Write short, funny, human X quote-posts. Never sound like an AI assistant, news summary, marketer, content farm, or corporate social-media manager."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 220,
        "temperature": 0.95,
        "top_p": 0.9,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(1, 3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("success") is False:
                    raise GeminiError(f"Cloudflare Workers AI error: {payload.get('errors') or payload.get('messages')}")
                result = payload.get("result") or {}
                text = result.get("response")
                if not text and result.get("choices"):
                    text = ((result["choices"][0].get("message") or {}).get("content"))
                if not isinstance(text, str) or not text.strip():
                    raise GeminiError("Cloudflare Workers AI returned no text.")
                return text.strip()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {408, 429, 500, 502, 503, 504} and attempt == 1:
                time.sleep(2)
                continue
            raise GeminiError("Cloudflare Workers AI failed with HTTP {exc.code}: {detail[:1000]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 1:
                time.sleep(2)
                continue
            raise GeminiError(f"Cloudflare Workers AI request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise GeminiError("Cloudflare Workers AI returned invalid JSON.") from exc
    raise GeminiError("Cloudflare Workers AI failed unexpectedly.")
