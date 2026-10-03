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
CLOUDFLARE_CANDIDATE_COUNT = 2
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
    __slots__ = (
        "should_quote",
        "quote_text",
        "angle",
        "format_name",
        "comedy_mechanism",
        "self_reply",
        "use_self_reply",
    )

    def __init__(
        self,
        should_quote: bool,
        quote_text: str,
        angle: str,
        format_name: str = "",
        comedy_mechanism: str = "",
        self_reply: str = "",
        use_self_reply: bool = False,
    ) -> None:
        self.should_quote = should_quote
        self.quote_text = quote_text
        self.angle = angle
        self.format_name = format_name
        self.comedy_mechanism = comedy_mechanism
        self.self_reply = self_reply
        self.use_self_reply = use_self_reply


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


def _prompt(source: dict, format_memory: list[dict] | None = None) -> str:
    fragments = _quote_options(str(source["text"]))
    options = "\n".join(f"- \"{item}\"" for item in fragments)
    recent_formats = format_memory[-10:] if format_memory else []
    memory_text = "\n".join(
        f"- {item.get('format_name', 'unknown')}: {item.get('comedy_mechanism', '')}"
        for item in recent_formats
        if isinstance(item, dict)
    ) or "- none yet"

    return f"""You are the creative brain of an experimental X account.

Write ONE original quote-post package about the source post below.

IMPORTANT: You are not a generic tweet generator.
You are running a private FORMAT LAB whose job is to create new internet-native comedy formats.

THE TARGET:
Make someone stop scrolling, laugh, quote-post it, or reply because the TAKE itself creates tension.
No "thoughts?", no "what do you think?", no empty engagement tricks.

FORMAT LAB:
Recent formats already used by this account:
{memory_text}

Do NOT recycle a recent format name, structure, opening, or punchline mechanism unless you mutate it substantially.

For this candidate, invent or mutate a format that feels like it belongs to the account.
A format can be something that does not exist in normal social-media language:
a fake institution, strange measurement, fictional protocol, bizarre report type, new mini-game,
new recurring notation, fake category, impossible scoreboard, etc.

Novelty matters, but coherence matters more. Do not make random nonsense just to seem original.

CREATIVE LANES:
- Lane A: a very strong, funny, immediately understandable format.
- Lane B: take a risk and INVENT a new format/mechanism that is not in recent memory.

MEME / X STYLE:
- human, punchy, specific, slightly chaotic
- deadpan, absurd analogy, dramatic overreaction, fake bureaucracy, fake stats, fake rules,
  "bro really..." energy, "we are cooked" energy, or an entirely new structure
- lowercase/fragments/slang/emojis are allowed when natural
- roast the idea, product behavior, hype, or situation—not protected identities or private people
- never invent facts or present fiction as real information
- safe ragebait is allowed: confident, cheeky, skeptical, dramatically unimpressed
- no political content

SOURCE QUOTE:
Use exactly ONE 2-6 word fragment from QUOTE OPTIONS.
Preserve the words exactly and put them in quotation marks.
Weave it into the joke naturally.
Never quote more than 6 consecutive source words.

REPLY-CHAIN DESIGN:
Sometimes the best bit is a main post followed by ONE self-reply.
Set use_self_reply=true only when the self-reply materially improves the joke.
The self-reply should feel like "evidence", a second punchline, a fake receipt, a callback,
a tiny escalation, or an invented artifact—not a generic explanation.
Never ask a question.
If used, keep it under 240 characters.

THE MAIN POST:
- maximum {MAX_QUOTE_CHARS} characters
- 1-2 short sentences or a compact meme artifact
- the punchline should land fast
- don't explain the joke
- don't sound like journalism, PR, LinkedIn, marketing, or an AI assistant

BANNED PHRASES:
"this highlights", "it's worth noting", "in today's world", "a reminder that",
"the implications are", "fascinating", "interesting development",
"What do you think?", "Thoughts?", "Agree?", "Let that sink in"

FINAL CHECK:
- funny without needing a paragraph of context
- exact quote fragment is present
- format feels distinctive
- easy for another X user to disagree with, riff on, or add a joke to
- no politics
- no fake facts

Return JSON only:
{{
  "post": "the actual quote-post",
  "quote_fragment": "the exact 2-6 word source fragment",
  "format_name": "short invented or mutated format name",
  "comedy_mechanism": "short description of why the joke works",
  "self_reply": "optional second punchline or artifact",
  "use_self_reply": false
}}

QUOTE OPTIONS:
{options}

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

    return QuoteDraft(
        True,
        text,
        draft.angle,
        draft.format_name,
        draft.comedy_mechanism,
        draft.self_reply,
        draft.use_self_reply,
    )


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
        if fragment in source_text and 2 <= len(fragment.split()) <= 6
    ]
    if not valid_fragments:
        return _repair_missing_source_quote(draft, source)

    if len(text) > MAX_QUOTE_CHARS:
        raise GeminiError("Generated quote-post is too long.")
    if draft.self_reply and len(draft.self_reply) > 240:
        raise GeminiError("Generated self-reply is too long.")
    lowered = text.lower()
    unquoted = re.sub(r'["“‘][^"”’]*["”’]', "", lowered)
    if any(phrase in unquoted for phrase in BANNED_STYLE_PHRASES):
        raise GeminiError("Generated quote-post used generic engagement or corporate phrasing.")

    return draft


def _clean_generated_post(text: str) -> str:
    text = text.strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith(fence):
            lines = lines[1:]
        if lines and lines[-1].strip() == fence:
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict) and data.get("post"):
        return json.dumps(data, ensure_ascii=False)
    if isinstance(data, dict) and data.get("quote_text"):
        text = str(data["quote_text"]).strip()

    for prefix in ("quote_text:", "quote:", "post:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()

    return " ".join(text.split())


def _pick_best_draft(drafts: list[QuoteDraft]) -> QuoteDraft:
    def score(draft: QuoteDraft) -> float:
        text = draft.quote_text
        lowered = text.lower()
        unquoted = re.sub(r'["“‘][^"”’]*["”’]', "", lowered)
        value = 0.0
        if any(token in lowered for token in ("bro", "nah", "lmao", "lol", "😭", "💀", "literally")):
            value += 2.0
        if any(token in lowered for token in ("we are", "we're", "they really", "imagine", "meanwhile")):
            value += 1.0
        if "?" not in text:
            value += 1.5
        if len(text) <= 180:
            value += 1.0
        if len(text) <= 140:
            value += 0.5
        if any(char in text for char in ("!", "—", "…")):
            value += 0.5
        if text[:1].islower():
            value += 0.25
        value -= sum(2.5 for phrase in BANNED_STYLE_PHRASES if phrase in unquoted)
        return value

    return max(drafts, key=score)


def _parse_experiment(text: str) -> QuoteDraft:
    text = _clean_generated_post(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return QuoteDraft(True, text, "")

    if not isinstance(data, dict):
        return QuoteDraft(True, text, "")

    post = " ".join(str(data.get("post", "")).split()).strip()
    fragment = " ".join(str(data.get("quote_fragment", "")).split()).strip()
    format_name = " ".join(str(data.get("format_name", "")).split()).strip()
    mechanism = " ".join(str(data.get("comedy_mechanism", "")).split()).strip()
    self_reply = " ".join(str(data.get("self_reply", "")).split()).strip()
    use_self_reply = bool(data.get("use_self_reply")) and bool(self_reply)

    if not post:
        raise GeminiError("AI returned no main quote-post text.")

    return QuoteDraft(
        True,
        post,
        fragment,
        format_name,
        mechanism,
        self_reply,
        use_self_reply,
    )


def _pick_best_draft(drafts: list[QuoteDraft], format_memory: list[dict]) -> QuoteDraft:
    recent_names = {
        str(item.get("format_name", "")).strip().lower()
        for item in format_memory
        if isinstance(item, dict)
    }

    def score(draft: QuoteDraft) -> float:
        text = draft.quote_text
        lowered = text.lower()
        unquoted = re.sub(r'["“‘][^"”’]*["”’]', "", lowered)
        value = 0.0
        if draft.format_name and draft.format_name.lower() not in recent_names:
            value += 4.0
        if draft.use_self_reply and draft.self_reply:
            value += 2.0
        if any(token in lowered for token in ("bro", "nah", "lmao", "lol", "😭", "💀", "literally")):
            value += 2.0
        if any(token in lowered for token in ("we are", "we're", "they really", "imagine", "meanwhile")):
            value += 1.0
        if "?" not in text:
            value += 1.5
        if len(text) <= 180:
            value += 1.0
        if len(text) <= 140:
            value += 0.5
        if len(text) >= 90 and len(text) <= 220:
            value += 0.5
        if any(char in text for char in ("!", "—", "…")):
            value += 0.5
        if text[:1].islower():
            value += 0.25
        if any(phrase in unquoted for phrase in BANNED_STYLE_PHRASES):
            value -= 4.0
        return value

    return max(drafts, key=score)


def generate_quote(source: dict, format_memory: list[dict] | None = None) -> QuoteDraft:
    format_memory = format_memory or []
    prompt = _prompt(source, format_memory)
    cloudflare_error: GeminiError | None = None

    try:
        raw_candidates = _cloudflare_candidates(prompt)
        drafts: list[QuoteDraft] = []
        for raw in raw_candidates:
            draft = _parse_experiment(raw)
            if not draft.should_quote:
                continue
            draft = _repair_missing_source_quote(draft, source)
            if not draft.quote_text:
                continue
            drafts.append(_validate_quote_against_source(draft, source))
        if drafts:
            return _pick_best_draft(drafts, format_memory)
        cloudflare_error = GeminiError("Cloudflare generated no usable experiment candidates.")
    except GeminiError as exc:
        cloudflare_error = exc

    try:
        _api_keys()
    except GeminiError:
        raise cloudflare_error or GeminiError("Cloudflare generation failed.")

    print(
        "Cloudflare generation failed; trying Gemini 3.5 Flash-Lite fallback: "
        f"{cloudflare_error}"
    )

    fallback_prompt = prompt + """

Generate only ONE candidate for Lane A: clear, funny, understandable, but still distinctive.
"""
    payload = {
        "contents": [{"parts": [{"text": fallback_prompt}]}],
        "generationConfig": {
            "temperature": 0.95,
            "maxOutputTokens": 260,
            "responseMimeType": "application/json",
        },
    }

    draft = _parse_experiment(_extract_text(_post_json(payload)))
    draft = _repair_missing_source_quote(draft, source)
    return _validate_quote_against_source(draft, source)


def _parse_quote_json(text: str) -> QuoteDraft:
    cleaned = _clean_generated_post(text)
    if not cleaned:
        raise GeminiError("AI returned an empty quote-post.")
    return QuoteDraft(True, cleaned, "")




def _cloudflare_credentials() -> tuple[str, str]:
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not token:
        raise GeminiError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required.")
    return account_id, token


def _cloudflare_candidates(prompt: str) -> list[str]:
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
        "n": CLOUDFLARE_CANDIDATE_COUNT,
        "max_completion_tokens": 180,
        "temperature": 1.0,
        "top_p": 0.95,
        "stream": False,
        "options": {"rejectIfBusy": True},
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
                outputs: list[str] = []
                for choice in choices:
                    choice = choice or {}
                    message = choice.get("message") or {}
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        outputs.append(content.strip())
                    elif isinstance(content, list):
                        joined = "".join(
                            part.get("text", "")
                            for part in content
                            if isinstance(part, dict) and isinstance(part.get("text"), str)
                        ).strip()
                        if joined:
                            outputs.append(joined)
                    for key in ("text", "output_text"):
                        value = choice.get(key)
                        if isinstance(value, str) and value.strip():
                            outputs.append(value.strip())

                if outputs:
                    return outputs

                result = response_payload.get("result")
                if isinstance(result, str) and result.strip():
                    return [result.strip()]
                if isinstance(result, dict):
                    for key in ("response", "text", "output_text"):
                        value = result.get(key)
                        if isinstance(value, str) and value.strip():
                            return [value.strip()]

                raise GeminiError("Cloudflare Workers AI returned no usable completion text.")

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
