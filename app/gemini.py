from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request

MODEL = "gemini-3.5-flash-lite"
CLOUDFLARE_MODEL = os.environ.get("CLOUDFLARE_AI_MODEL", "@cf/zai-org/glm-4.7-flash")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
CLOUDFLARE_API_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
MAX_QUOTE_CHARS = 260
MAX_API_ATTEMPTS_PER_KEY = 3
CLOUDFLARE_CANDIDATE_COUNT = 3
BANNED_STYLE_PHRASES = (
    "this highlights",
    "it's worth noting",
    "in today's world",
    "a reminder that",
    "the implications are",
    "fascinating",
    "interesting development",
    "what do you think",
    "thoughts?",
    "agree?",
    "let that sink in",
)
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
    fragments = _quote_options(str(source["text"]))
    options = "\n".join(f"- \"{item}\"" for item in fragments)
    return f"""Write ONE hilarious, meme-native X quote-post about the source post below.

You are writing the actual quote-post, not an explanation of it.

CORE GOAL:
Make a real X user stop scrolling and either laugh, reply with "nah", "bro", "LMAO", a counterexample,
or argue with the take. Create a tiny argument or a "wait that's actually true" feeling.
Do NOT ask a question or say "thoughts?" to manufacture engagement.

HUMAN X VOICE:
- Sound like a real person reacting in the moment.
- Specific, punchy, slightly chaotic, confident, and conversational.
- Prefer a memorable joke over a complete explanation.
- Safe ragebait is good: cheeky, skeptical, dramatically unimpressed, absurd, or contrarian.
- Attack the idea, product behavior, situation, or hype — never protected identities and never private-person harassment.
- Never invent facts, numbers, events, quotes, or motives.
- Lowercase/fragments/deadpan humor/slang/emojis are allowed when they genuinely fit. Do not force them.
- Vary the construction. Do not reuse the same "this is X" joke every time.

MEME / REPLY-MAGNET FORMATS YOU MAY USE WHEN THEY FIT:
- deadpan comparison
- absurd analogy
- fake scoreboard / ranking
- "me watching this happen" energy
- dramatic overreaction to a tiny detail
- painfully specific observation
- "bro really..." style
- "we are so cooked" style
- short setup → hard punchline
- a confident take that is easy to disagree with

Do NOT make every post a meme template. The source itself should determine the joke.

SOURCE QUOTE:
- You MUST use exactly ONE fragment from the options below, preserving the words exactly.
- Use 2-6 words only.
- Put that fragment in quotation marks and WEAVE it naturally into the joke.
- Never quote more than 6 consecutive words.
- Do not paste the quote at the beginning like a citation unless that genuinely makes the joke better.
- If every option is unusable, quote a short exact fragment from the source rather than inventing one.

QUOTE OPTIONS:
{options}

ANTI-AI / ANTI-CORPORATE:
Never sound like a journalist, brand account, PR team, social-media manager, AI assistant, LinkedIn post,
press release, marketing copy, or content farm.
Never use filler such as: "this highlights", "it's worth noting", "in today's world", "a reminder that",
"the implications are", "fascinating", or "interesting development".
Never end with "What do you think?", "Thoughts?", "Agree?", "Let that sink in", or similar engagement bait.
No hashtags unless one is genuinely part of the joke.
No joke explanation after the punchline.

LENGTH:
- Maximum {MAX_QUOTE_CHARS} characters.
- Prefer 1-2 short sentences.
- Keep it tight enough that the punchline lands immediately.

FINAL CHECK:
1. Would someone actually post this from their personal X account?
2. Is there a concrete joke, twist, roast, or sharp observation?
3. Is the exact source fragment woven into the sentence?
4. Does it invite replies through the TAKE itself, not a question?
5. Does it avoid generic AI/corporate phrasing?
6. Is it about AI, technology, sports, gaming, or major tech products/companies only?
7. Could a skeptical X user easily disagree with it?

Return ONLY the post text. No JSON. No labels. No markdown fences.

SOURCE TREND: {source['trend']}
SOURCE AUTHOR: @{source['username']}
SOURCE POST:
{source['text']}
"""


def _quote_options(source_text: str, limit: int = 6) -> list[str]:
    """Return distinctive short source fragments for the model to quote verbatim."""
    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
        "has", "have", "in", "is", "it", "of", "on", "or", "that", "the", "this",
        "to", "was", "were", "with", "you", "your", "i", "we", "they", "he", "she",
    }
    tokens = source_text.split()
    candidates: list[tuple[float, str]] = []

    for width in (6, 5, 4, 3, 2):
        for index in range(len(tokens) - width + 1):
            fragment = " ".join(tokens[index : index + width]).strip()
            if not fragment or len(fragment) > 90:
                continue
            window = tokens[index : index + width]
            if any(
                token.startswith("@")
                or token.startswith("#")
                or "http://" in token
                or "https://" in token
                for token in window
            ):
                continue

            plain_words = [
                re.sub(r"[^A-Za-z0-9]+", "", token).lower()
                for token in window
            ]
            content_words = [word for word in plain_words if word and word not in stop_words]
            if not content_words:
                continue

            score = float(len(content_words) * 3 + width)
            score += 2.0 if any(len(word) >= 8 for word in content_words) else 0.0
            score += 1.5 if any(char.isdigit() for char in fragment) else 0.0
            score += 1.5 if any(char in fragment for char in "?!") else 0.0
            score += 1.0 if any(char in fragment for char in "😭💀") else 0.0
            candidates.append((score, fragment))

    candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)

    selected: list[str] = []
    seen: set[str] = set()
    for _, fragment in candidates:
        key = fragment.lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append(fragment)
        if len(selected) >= limit:
            break
    return selected


def _source_fragment(source_text: str) -> str | None:
    options = _quote_options(source_text, limit=1)
    return options[0] if options else None


def _repair_missing_source_quote(draft: QuoteDraft, source: dict) -> QuoteDraft:
    if not draft.quote_text:
        return QuoteDraft(False, "", draft.angle)

    fragment = _source_fragment(str(source["text"]))
    if not fragment:
        return QuoteDraft(False, "", draft.angle)

    text = draft.quote_text.strip()
    quoted = f'"{fragment}"'
    if quoted not in text and f"“{fragment}”" not in text and f"‘{fragment}’" not in text:
        text = f'{text} "{fragment}"'.strip()

    if len(text) > MAX_QUOTE_CHARS:
        text = text[:MAX_QUOTE_CHARS].rstrip()

    return QuoteDraft(True, text, draft.angle)


def _validate_quote_against_source(draft: QuoteDraft, source: dict) -> QuoteDraft:
    if not draft.should_quote:
        return draft

    text = draft.quote_text
    source_text = str(source["text"])
    fragments = []
    for left_mark, right_mark in (("\"", "\""), ("“", "”"), ("‘", "’")):
        start = 0
        while True:
            left = text.find(left_mark, start)
            if left < 0:
                break
            right = text.find(right_mark, left + 1)
            if right < 0:
                break
            fragment = text[left + 1:right].strip()
            if fragment:
                fragments.append(fragment)
            start = right + 1

    if not fragments:
        return _repair_missing_source_quote(draft, source)

    valid_fragments = [
        fragment for fragment in fragments
        if fragment in source_text and len(fragment.split()) <= 8
    ]
    if not valid_fragments:
        return _repair_missing_source_quote(draft, source)

    if len(text) > MAX_QUOTE_CHARS:
        raise GeminiError("Generated quote-post is too long.")

    return draft


def generate_quote(source: dict) -> QuoteDraft:
    prompt = _prompt(source)
    try:
        return _validate_quote_against_source(
            _parse_quote_json(_cloudflare_text(prompt)),
            source,
        )
    except GeminiError as cloudflare_error:
        try:
            _api_keys()
        except GeminiError:
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
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

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
    url = CLOUDFLARE_API_URL.format(account_id=account_id)
    payload = {
        "model": CLOUDFLARE_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Write short, funny, human X quote-posts. "
                    "Never sound like an AI assistant, news summary, marketer, "
                    "content farm, or corporate social-media manager."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 220,
        "temperature": 0.95,
        "top_p": 0.9,
        "stream": False,
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

    for attempt in range(1, 3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                response_payload = json.loads(response.read().decode("utf-8"))

                if response_payload.get("success") is False:
                    raise GeminiError(
                        "Cloudflare Workers AI error: "
                        f"{response_payload.get('errors') or response_payload.get('messages')}"
                    )

                choices = response_payload.get("choices") or []
                if choices:
                    choice = choices[0] or {}
                    message = choice.get("message") or {}
                    text = message.get("content")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
                    if isinstance(text, list):
                        parts = [
                            part.get("text", "")
                            for part in text
                            if isinstance(part, dict) and isinstance(part.get("text"), str)
                        ]
                        joined = "".join(parts).strip()
                        if joined:
                            return joined
                    for key in ("text", "output_text"):
                        value = choice.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()

                result = response_payload.get("result")
                if isinstance(result, str) and result.strip():
                    return result.strip()
                if isinstance(result, dict):
                    for key in ("response", "text", "output_text"):
                        value = result.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()

                raise GeminiError(
                    "Cloudflare Workers AI returned an empty completion response."
                )

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429:
                # Do not hammer Cloudflare when capacity/quota is unavailable.
                raise GeminiError(
                    f"Cloudflare Workers AI rate/quota limited (HTTP 429): {detail[:800]}"
                ) from exc
            if exc.code in {408, 500, 502, 503, 504} and attempt == 1:
                time.sleep(2)
                continue
            raise GeminiError(
                f"Cloudflare Workers AI failed with HTTP {exc.code}: {detail[:1000]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 1:
                time.sleep(2)
                continue
            raise GeminiError(f"Cloudflare Workers AI request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise GeminiError("Cloudflare Workers AI returned invalid JSON.") from exc

    raise GeminiError("Cloudflare Workers AI failed unexpectedly.")
