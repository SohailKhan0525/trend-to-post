import json
import time
from datetime import datetime, timezone

import httpx
from google import genai
from google.genai import types

from .models import Draft, Trend

MODEL = "gemini-3.5-flash-lite"
MAX_ATTEMPTS_PER_KEY = 4
CANDIDATE_COUNT = 5

POST_INSTRUCTIONS = {
    "funny_ragebait": """Write funny, provocative technology/AI X posts that invite disagreement.
The tone MUST be playful ragebait: bold, cheeky, slightly controversial, relatable, and likely to make tech people argue.
Use 1-2 relevant emojis in every candidate. Never be hateful, abusive, deceptive, or invent facts.
Use only the supplied X Trend as the topic.""",
    "breaking_news": """Write a breaking-news-style technology/AI X post.
Sound urgent and current, like a concise newsroom alert, but NEVER invent facts, numbers, quotes, launches, timelines, causes, or details.
Use only what the supplied X Trend itself establishes. If it is merely a trending topic, say it is gaining attention rather than pretending a verified event happened.""",
    "question": """Write a natural technology/AI X post centered on ONE specific, thoughtful question.
The question must be concrete, interesting, and tied directly to the supplied X Trend.
Avoid generic engagement bait. Do not invent facts, quotes, personal experiences, or events.""",
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
        return "?" in text
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

    def _generate(self, client, prompt):
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                temperature=0.9,
                max_output_tokens=512,
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
- Exactly 17 whitespace-separated words.
- Maximum 280 characters.
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
        for attempt in range(MAX_ATTEMPTS_PER_KEY):
            for key_index, client in enumerate(self.clients):
                try:
                    print(
                        f"Gemini generation attempt: model={MODEL}, key={key_index + 1}, "
                        f"round={attempt + 1}/{MAX_ATTEMPTS_PER_KEY}"
                    )
                    data = self._generate(client, prompt)
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
