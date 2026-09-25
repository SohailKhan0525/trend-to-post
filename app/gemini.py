import json
import os
import random
import time
from datetime import datetime, timezone

import httpx
from google import genai
from google.genai import types

from .models import Draft, Trend

MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3-flash-preview",
)
PRO_MODEL = "gemini-3.1-pro-preview"
MAX_ATTEMPTS_PER_KEY = 1
CANDIDATE_COUNT = 1

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
            "minItems": CANDIDATE_COUNT,
            "maxItems": CANDIDATE_COUNT,
            "items": {"type": "string"},
            "description": f"Exactly {CANDIDATE_COUNT} distinct main-post candidates, in order.",
        },
        "reply_candidates": {
            "type": "array",
            "minItems": CANDIDATE_COUNT,
            "maxItems": CANDIDATE_COUNT,
            "items": {"type": "string"},
            "description": f"Exactly {CANDIDATE_COUNT} replies; reply N directly responds to main-post candidate N.",
        },
    },
    "required": ["trend", "angle", "candidates", "reply_candidates"],
}


class GeminiQuotaError(RuntimeError):
    pass


EMOJI_SET = "😂🤣😭😅💀🔥🤯😤🙃😈🚨"
REPLY_INSTRUCTIONS = """Write ONE respectful, polite, natural reply that a second X account could post underneath the generated main post.
It must clearly relate to the main post and its supplied X Trend.
Be conversational and sincere, not sycophantic, hostile, promotional, or generic.
Do not invent facts, personal experiences, or events.
Do not use hashtags or emojis."""


def _validate_candidate(text: str, post_type: str) -> bool:
    text = text.strip()
    if len(text.split()) != 17 or len(text) > 280:
        return False
    emoji_count = sum(ch in EMOJI_SET for ch in text)
    if emoji_count != 1:
        return False
    if post_type == "funny_ragebait":
        return True
    if post_type == "question":
        return text.count("?") == 1
    if post_type == "breaking_news":
        return text.startswith("Breaking news 🚨") and text.count("🚨") == 1 and "?" not in text
    return True


def _validate_reply(text: str) -> bool:
    text = text.strip()
    return len(text.split()) == 17 and len(text) <= 280 and not any(ch in text for ch in EMOJI_SET)


class GeminiWriter:
    def __init__(self, api_key, draft_count, backup_api_key=""):
        self.api_keys = [key.strip() for key in (api_key, backup_api_key) if key and key.strip()]
        if not self.api_keys:
            raise ValueError("At least one Gemini API key is required.")
        self.clients = [
            genai.Client(
                api_key=key,
                http_options=types.HttpOptions(
                    retry_options=types.HttpRetryOptions(attempts=2),
                    timeout=45000,
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
                thinking_config=types.ThinkingConfig(
                    thinking_level="low"
                    if model in {"gemini-3.8-flash", "gemini-3.7-flash"}
                    else "minimal"
                ),
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

    def generate(self, trends: list[Trend], post_type: str = "standard", recent_texts: list[str] | None = None, recent_trends: list[str] | None = None):
        if post_type not in POST_INSTRUCTIONS:
            raise ValueError(f"Unsupported post type: {post_type}")

        payload = {"trends": [{"rank": t.rank, "name": t.name} for t in trends]}
        recent_texts = recent_texts or []
        recent_trends = recent_trends or []
        prompt = f"""You are generating ONE publishable X post for the requested style.

STYLE:
{POST_INSTRUCTIONS[post_type]}

HARD RULES FOR EVERY MAIN POST CANDIDATE:
- Exactly 17 whitespace-separated words, counted before returning each candidate.
- Maximum 280 characters.
- Treat punctuation attached to a word as part of that word; whitespace is the only word separator.
- Do not add or remove words after counting; every returned candidate must already be exactly 17 words.
- Exactly ONE emoji in every main post candidate.
- No hashtags.
- No labels like "Post:", "Question:", or "Tweet:".
- For breaking_news, the candidate MUST begin exactly "Breaking news 🚨".
- Carefully count the words before returning the candidate.
- Return exactly one main post candidate.
- Before returning the candidate, explicitly count its whitespace-separated tokens from left to right and revise it until the count is exactly 17.
- Do the same exact count check for the paired reply.

REPLY RULES:
- Return exactly one reply_candidates item.
- Generate the reply only after the main post has been finalized at exactly 17 words.
- The reply must be exactly 17 whitespace-separated words and maximum 280 characters.
- The reply must respond specifically to the main post.
- Every reply must be respectful, polite, conversational, and directly relevant to the generated main-post topic.
- Replies must not use emojis or hashtags.
- Do not simply repeat the main post.

REPLY STYLE:
{REPLY_INSTRUCTIONS}

RECENT POSTS TO AVOID:
{json.dumps(recent_texts[-30:], ensure_ascii=False)}

RECENT X TRENDS ALREADY USED — DO NOT USE THESE TRENDS AGAIN:
{json.dumps(recent_trends[-100:], ensure_ascii=False)}

TREND SELECTION RULE:
- Choose a trend from INPUT X TRENDS that is NOT in RECENT X TRENDS ALREADY USED.
- Do not merely rephrase a recently used trend with different wording.
- If multiple fresh trends are available, prefer one that has not appeared recently.

INPUT X TRENDS:
{json.dumps(payload, ensure_ascii=False)}
"""

        last_error = None
        models = MODELS
        if os.getenv("ENABLE_GEMINI_PRO", "").strip().lower() in {"1", "true", "yes"}:
            models = MODELS + (PRO_MODEL,)
        for model in models:
            for attempt in range(MAX_ATTEMPTS_PER_KEY):
                for key_index, client in enumerate(self.clients):
                    try:
                        print(
                            f"Gemini generation attempt: model={model}, key={key_index + 1}, "
                            f"round={attempt + 1}/{MAX_ATTEMPTS_PER_KEY}"
                        )
                        data = self._generate(client, prompt, model)
                        candidates = [str(x).strip() for x in data.get("candidates", [])]
                        replies = [str(x).strip() for x in data.get("reply_candidates", [])]
                        valid_pairs = [
                            (post, reply)
                            for post, reply in zip(candidates, replies)
                            if _validate_candidate(post, post_type) and _validate_reply(reply)
                        ]
                        if not valid_pairs:
                            raise GeminiQuotaError(
                                "Gemini returned candidates, but none met the exact 17-word/style rules for both post and reply."
                            )

                        text, reply_text = valid_pairs[0]
                        now = datetime.now(timezone.utc).isoformat()
                        return [
                            Draft(
                                trend=str(data.get("trend") or trends[0].name),
                                angle=str(data.get("angle") or post_type),
                                text=text,
                                generated_at=now,
                                post_type=post_type,
                                reply_text=reply_text,
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
                        delay = min(30, 2 ** attempt) + random.uniform(0, 1)
                        print(
                            f"Gemini transient error; retrying after {delay:.1f}s: "
                            f"{message[:300]}"
                        )
                        time.sleep(delay)

        raise last_error or RuntimeError("Gemini generation failed.")
