import json
import time
from datetime import datetime, timezone

import httpx
from google import genai
from google.genai import types

from .models import Draft, Trend

MODELS = ("gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.1-flash-lite")
MAX_ATTEMPTS_PER_KEY = 3
CANDIDATE_COUNT = 8

POST_INSTRUCTIONS = {
    "funny_ragebait": """Write a funny, provocative technology/AI X post based DIRECTLY on ONE supplied X Trend.
The tone MUST be playful ragebait: bold, cheeky, slightly controversial, relatable, and likely to make tech people argue.
Use EXACTLY ONE relevant emoji in every candidate. Never be hateful, abusive, deceptive, or invent facts.
Do not turn the trend into a generic technology joke; the supplied X Trend must clearly be the topic.""",
    "breaking_news": """Write a breaking-news-style technology/AI X post based DIRECTLY on ONE supplied X Trend.
It MUST begin exactly with "Breaking news 🚨".
Sound urgent and current, but NEVER invent facts, numbers, quotes, launches, timelines, causes, or details.
Use only what the supplied X Trend itself establishes. If it is merely a trending topic, say it is gaining attention rather than pretending a verified event happened.
The 🚨 emoji is the ONLY emoji allowed in the main post.""",
    "question": """Write a natural technology/AI X post centered on ONE specific, thoughtful question tied DIRECTLY to the supplied X Trend.
The question must be concrete and interesting, not generic engagement bait.
Use EXACTLY ONE relevant emoji naturally in every candidate.
Do not invent facts, quotes, personal experiences, or events.""",
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "trend": {"type": "string"},
        "angle": {"type": "string"},
        "candidates": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["trend", "angle", "candidates"],
}


class GeminiQuotaError(RuntimeError):
    pass


def _validate_candidate(text: str, post_type: str) -> bool:
    text = text.strip()
    if len(text.split()) != 17 or len(text) > 280:
        return False
    if post_type == "funny_ragebait":
        return any(ch in text for ch in "😂🤣😭😅💀🔥🤯😤🙃😈")
    if post_type == "question":
        return text.count("?") == 1
    if post_type == "breaking_news":
        return "?" not in text
    return True


class GeminiWriter:
    def __init__(self, api_key, draft_count, backup_api_key=""):
        self.api_keys = [key.strip() for key in (api_key, backup_api_key) if key and key.strip()]
        if not self.api_keys:
            raise ValueError("At least one Gemini API key is required.")
        self.clients = [
            genai.Client(
                api_key=key,
                http_options=types.HttpOptions(
                    retry_options=types.HttpRetryOptions(attempts=1),
                    timeout=20000,
                ),
            )
            for key in self.api_keys
        ]
        self.draft_count = 1

    def _generate(self, client, prompt, model):
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                max_output_tokens=768,
            ),
        )
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            if hasattr(parsed, "model_dump"):
                return parsed.model_dump()
            if isinstance(parsed, dict):
                return parsed
            return dict(parsed)
        raw = (getattr(response, "text", None) or "").strip()
        if not raw:
            raise GeminiQuotaError("Gemini returned no content.")
        return json.loads(raw)

    def generate(self, trends: list[Trend], post_type: str = "standard", recent_texts: list[str] | None = None):
        if post_type not in POST_INSTRUCTIONS:
            raise ValueError(f"Unsupported post type: {post_type}")

        payload = {"trends": [{"rank": t.rank, "name": t.name} for t in trends]}
        recent_texts = recent_texts or []
        prompt = f"""You are generating ONE publishable X post for the requested style.

STYLE:
{POST_INSTRUCTIONS[post_type]}

HARD RULES FOR EVERY CANDIDATE:
- Exactly 17 whitespace-separated words, counted before returning each candidate.
- Maximum 280 characters.
- Treat punctuation attached to a word as part of that word; whitespace is the only word separator.
- Do not add or remove words after counting; every returned candidate must already be exactly 17 words.
- No hashtags unless genuinely necessary.
- No labels like "Post:", "Breaking:", "Question:", or "Tweet:".
- Every candidate must be meaningfully different from the others.
- Carefully count the words before returning each candidate.
- Return exactly {CANDIDATE_COUNT} candidates.

RECENT POSTS TO AVOID:
{json.dumps(recent_texts[-30:], ensure_ascii=False)}

INPUT X TRENDS:
{json.dumps(payload, ensure_ascii=False)}
"""

        last_error = None
        for model in MODELS:
            for attempt in range(MAX_ATTEMPTS_PER_KEY):
                for key_index, client in enumerate(self.clients):
                    try:
                        print(
                            f"Gemini generation attempt: model={model}, key={key_index + 1}, "
                            f"round={attempt + 1}/{MAX_ATTEMPTS_PER_KEY}"
                        )
                        data = self._generate(client, prompt, model)
                        candidates = [str(x).strip() for x in data.get("candidates", [])]
                        valid = [x for x in candidates if _validate_candidate(x, post_type)]
                        if not valid:
                            raise GeminiQuotaError(
                                "Gemini returned candidates, but none met the exact 17-word/style rules."
                            )

                        text = valid[0]
                        now = datetime.now(timezone.utc).isoformat()
                        return [
                            Draft(
                                trend=str(data.get("trend") or trends[0].name),
                                angle=str(data.get("angle") or post_type),
                                text=text,
                                generated_at=now,
                                post_type=post_type,
                            )
                        ]
                    except (GeminiQuotaError, json.JSONDecodeError, ValueError) as exc:
                        last_error = exc
                        print(f"Gemini attempt failed; trying again: {exc}")
                    except Exception as exc:
                        message = str(exc)
                        is_transient = (
                            isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadTimeout))
                            or "429" in message
                            or "RESOURCE_EXHAUSTED" in message
                            or "500" in message
                            or "502" in message
                            or "503" in message
                            or "504" in message
                            or "UNAVAILABLE" in message
                        )
                        if not is_transient:
                            raise
                        last_error = GeminiQuotaError(message[:1000])
                        print(f"Gemini transient error; trying again: {message[:300]}")
                        time.sleep(2)

        raise last_error or RuntimeError("Gemini generation failed.")
