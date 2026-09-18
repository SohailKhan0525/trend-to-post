import json
from datetime import datetime, timezone

from google import genai
from google.genai import types

from .models import Draft, Trend

MODEL = "gemini-3.8-flash"

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
        self.clients = [genai.Client(api_key=key) for key in self.api_keys]
        self.draft_count = 1

    def _generate_with_client(self, client, prompt):
        try:
            return client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    temperature=0.7,
                    max_output_tokens=256,
                ),
            )
        except Exception as exc:
            message = str(exc)
            if (
                "429" in message
                or "RESOURCE_EXHAUSTED" in message
                or "quota" in message.lower()
                or "401" in message
                or "403" in message
                or "authentication" in message.lower()
                or "permission_denied" in message.lower()
            ):
                raise GeminiQuotaError(
                    "Gemini API request failed with a retryable key/quota/auth error. "
                    f"Google error: {message[:1000]}"
                ) from exc
            raise

    def generate(self, trends: list[Trend]):
        payload = {"trends": [{"rank": t.rank, "name": t.name} for t in trends]}
        prompt = INSTRUCTION + "\nINPUT:\n" + json.dumps(
            payload, ensure_ascii=False
        )

        last_error = None
        interaction = None
        for index, client in enumerate(self.clients):
            try:
                interaction = self._generate_with_client(client, prompt)
                break
            except GeminiQuotaError as exc:
                last_error = exc
                if index + 1 < len(self.clients):
                    print(f"Gemini primary key failed; trying backup key ({index + 2}/{len(self.clients)}).")
                else:
                    raise

        if interaction is None:
            raise last_error or RuntimeError("Gemini generation failed.")

        data = json.loads(interaction.text)
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
