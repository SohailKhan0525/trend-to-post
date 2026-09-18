import json
import time

import httpx
from datetime import datetime, timezone

from google import genai
from google.genai import types

from .models import Draft, Trend

MODELS = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash")

INSTRUCTION = """Write one original short-form post for a technology and artificial intelligence account.
The input is an X Trend used only as research input.
Only use a trend plausibly related to technology or artificial intelligence.
Write like a knowledgeable human: concrete, concise, natural, and specific.
Do not invent facts or personal experiences. Do not copy or closely paraphrase existing posts.
Avoid engagement bait, generic AI phrasing, excessive hashtags, and empty summaries.
The final text must be suitable for a single X post and must be no longer than 280 characters.
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
                    timeout=30000,
                ),
            )
            for key in self.api_keys
        ]
        self.draft_count = 1

    def _generate_with_client(self, client, prompt, model):
        last_error = None
        for attempt in range(4):
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
                if attempt == 3:
                    raise GeminiQuotaError(
                        "Gemini transient service/network failure; "
                        "the next model/key will be tried. "
                        f"Google error: {message[:1000]}"
                    ) from exc
                delay = 3 * (2 ** attempt)
                print(
                    f"Gemini transient error on attempt {attempt + 1}/4; "
                    f"retrying in {delay}s: {message[:300]}"
                )
                time.sleep(delay)
        raise last_error or RuntimeError("Gemini generation failed.")

    def generate(self, trends: list[Trend]):
        payload = {"trends": [{"rank": t.rank, "name": t.name} for t in trends]}
        prompt = INSTRUCTION + "\nINPUT:\n" + json.dumps(
            payload, ensure_ascii=False
        )

        last_error = None
        interaction = None
        attempts = [(key_index, model) for model in MODELS for key_index in range(len(self.clients))]
        for attempt_index, (key_index, model) in enumerate(attempts):
            client = self.clients[key_index]
            try:
                print(f"Gemini generation attempt {attempt_index + 1}/{len(attempts)}: model={model}, key={key_index + 1}")
                interaction = self._generate_with_client(client, prompt, model)
                break
            except GeminiQuotaError as exc:
                last_error = exc
                if attempt_index + 1 < len(attempts):
                    next_key_index, next_model = attempts[attempt_index + 1]
                    print(
                        f"Gemini attempt failed; trying model={next_model}, "
                        f"key={next_key_index + 1} ({attempt_index + 2}/{len(attempts)})."
                    )
                else:
                    raise

        if interaction is None:
            raise last_error or RuntimeError("Gemini generation failed.")

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
            try:
                data = json.loads(raw_text)
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "Gemini returned a non-empty response that was not valid JSON."
                ) from exc

        now = datetime.now(timezone.utc).isoformat()
        text = str(data["text"]).strip()
        if len(text) > 280:
            raise ValueError("Gemini returned a post longer than 280 characters.")

        return [
            Draft(
                trend=str(data["trend"]),
                angle=str(data["angle"]),
                text=text,
                generated_at=str(data.get("generated_at") or now),
            )
        ]
