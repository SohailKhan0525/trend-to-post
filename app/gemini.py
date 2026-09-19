import json
import time

import httpx
from datetime import datetime, timezone

from google import genai
from google.genai import types

from .models import Draft, Trend

MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash")

POST_INSTRUCTIONS = {
    "funny_ragebait": """Write one original funny technology/AI X post designed to spark debate.
Use only the supplied X Trend as the topic. Use playful ragebait framing and 1-2 relevant emojis.
Make it witty and relatable, not hateful, abusive, deceptive, or based on invented facts.
Do not invent facts, quotes, personal experiences, or events.""",
    "breaking_news": """Write one original breaking-news-style technology/AI X post.
Use only the supplied X Trend as the source signal. Be urgent and concise, but NEVER invent facts, numbers, quotes, timelines, launches, causes, or details.
If the trend name does not establish a specific event, clearly frame it as a topic/trend gaining attention instead of claiming unverified news.""",
    "question": """Write one original technology/AI X post built around ONE specific, thoughtful question.
Use only the supplied X Trend as the topic. Make the question natural and useful, not generic engagement bait.
Do not invent facts, quotes, personal experiences, or events.""",
}

INSTRUCTION_SUFFIX = """
Avoid generic AI phrasing, excessive hashtags, and empty summaries.
The final text must be natural and immediately publishable on X, exactly 17 whitespace-separated words, and no longer than 280 characters including spaces and emojis.
Do not start with labels such as "Post:", "Breaking:", "Question:", or "Tweet:".
Return exactly one JSON object with trend, angle, text, generated_at.
"""
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "trend": {"type": "string"},
        "angle": {"type": "string"},
        "text": {"type": "string"},
        "generated_at": {"type": "string"},
    },
    "required": ["trend", "angle", "text", "generated_at"],
}


class GeminiQuotaError(RuntimeError):
    """A Gemini 429 caused by exhausted project quota."""


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

    def _generate_with_client(self, client, prompt, model):
        last_error = None
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=RESPONSE_SCHEMA,
                        temperature=0.7,
                        max_output_tokens=256,
                    ),
                )
                parsed = getattr(response, "parsed", None)
                text = getattr(response, "text", None)
                if parsed is None and not (text and text.strip()):
                    raise GeminiQuotaError(
                        "Gemini returned HTTP 200 but no structured/text content."
                    )
                return response
            except Exception as exc:
                last_error = exc
                message = str(exc)
                is_key_error = (
                    "401" in message
                    or "403" in message
                    or "authentication" in message.lower()
                    or "permission_denied" in message.lower()
                )
                is_transient = (
                    isinstance(exc, httpx.RemoteProtocolError)
                    or isinstance(exc, httpx.ReadTimeout)
                    or "429" in message
                    or "RESOURCE_EXHAUSTED" in message
                    or "503" in message
                    or "UNAVAILABLE" in message
                    or "500" in message
                    or "502" in message
                    or "504" in message
                )
                if is_key_error:
                    raise GeminiQuotaError(
                        "Gemini API key/authentication failed. "
                        f"Google error: {message[:1000]}"
                    ) from exc
                if not is_transient:
                    raise
                if attempt == 1:
                    raise GeminiQuotaError(
                        "Gemini transient service/network failure; "
                        "the next model/key will be tried. "
                        f"Google error: {message[:1000]}"
                    ) from exc
                delay = 2 * (2 ** attempt)
                print(
                    f"Gemini transient error on attempt {attempt + 1}/4; "
                    f"retrying in {delay}s: {message[:300]}"
                )
                time.sleep(delay)
        raise last_error or RuntimeError("Gemini generation failed.")

    def generate(self, trends: list[Trend], post_type: str = "standard", recent_texts: list[str] | None = None):
        if post_type not in POST_INSTRUCTIONS:
            raise ValueError(f"Unsupported post type: {post_type}")
        payload = {"trends": [{"rank": t.rank, "name": t.name} for t in trends]}
        recent_texts = recent_texts or []
        diversity_instruction = """
IMPORTANT: This post must be substantially different from every recent post below.
Do not reuse their wording, sentence structure, hook, joke, question, or angle.
The post type is mandatory and must be obvious from the writing.
""" + "\nRECENT POSTS TO AVOID REPEATING:\n" + json.dumps(recent_texts[-30:], ensure_ascii=False)
        prompt = POST_INSTRUCTIONS[post_type] + INSTRUCTION_SUFFIX + diversity_instruction + "\nINPUT:\n" + json.dumps(
            payload, ensure_ascii=False
        )

        last_error = None
        for model in MODELS:
            for key_index, client in enumerate(self.clients):
                try:
                    print(
                        f"Gemini generation attempt: model={model}, "
                        f"key={key_index + 1}"
                    )
                    interaction = self._generate_with_client(client, prompt, model)
                    parsed = getattr(interaction, "parsed", None)
                    if parsed is not None:
                        if hasattr(parsed, "model_dump"):
                            data = parsed.model_dump()
                        elif isinstance(parsed, dict):
                            data = parsed
                        else:
                            data = dict(parsed)
                    else:
                        raw_text = (getattr(interaction, "text", None) or "").strip()
                        if not raw_text:
                            raise GeminiQuotaError(
                                "Gemini returned HTTP 200 with no usable content."
                            )
                        data = json.loads(raw_text)
                    required = {"trend", "angle", "text", "generated_at"}
                    if not required.issubset(data):
                        raise GeminiQuotaError("Gemini returned incomplete structured content.")
                    now = datetime.now(timezone.utc).isoformat()
                    break
                except (GeminiQuotaError, json.JSONDecodeError) as exc:
                    last_error = exc
                    print(f"Gemini attempt failed; trying next model/key: {exc}")
            else:
                continue
            break
        else:
            raise last_error or RuntimeError("Gemini generation failed.")
        required = {"trend", "angle", "text", "generated_at"}
        if not required.issubset(data):
            raise GeminiQuotaError(
                "Gemini returned incomplete structured content; "
                "the next model/key will be tried."
            )
        text = str(data["text"]).strip()
        word_count = len(text.split())
        if not text or word_count != 17 or len(text) > 280:
            raise ValueError(
                f"Gemini returned an invalid X post: expected exactly 17 words and <=280 characters; "
                f"got {word_count} words and {len(text)} characters."
            )
        if post_type == "funny_ragebait" and not any(ch in text for ch in "😂🤣😭😅💀🔥🤯😤🙃😈"):
            raise ValueError("Funny ragebait post must contain an emoji.")
        if post_type == "question" and "?" not in text:
            raise ValueError("Question post must contain a question mark.")

        return [
            Draft(
                trend=str(data["trend"]),
                angle=str(data["angle"]),
                text=text,
                generated_at=str(data.get("generated_at") or now),
            )
        ]
