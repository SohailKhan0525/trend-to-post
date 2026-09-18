import json
from datetime import datetime, timezone

from google import genai

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
    def __init__(self, api_key, draft_count):
        self.client = genai.Client(api_key=api_key)
        self.draft_count = 1

    def generate(self, trends: list[Trend]):
        payload = {"trends": [{"rank": t.rank, "name": t.name} for t in trends]}
        prompt = INSTRUCTION + "\nINPUT:\n" + json.dumps(
            payload, ensure_ascii=False
        )

        try:
            interaction = self.client.interactions.create(
                model=MODEL,
                input=prompt,
                response_format=[
                    {
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": RESPONSE_SCHEMA,
                    }
                ],
            )
        except Exception as exc:
            message = str(exc)
            if "429" in message or "RESOURCE_EXHAUSTED" in message or "quota" in message.lower():
                raise GeminiQuotaError(
                    "Gemini API quota/rate limit was exhausted. "
                    "Check Google AI Studio > Usage/Rates for this project. "
                    f"Google error: {message[:1000]}"
                ) from exc
            raise

        data = json.loads(interaction.output_text)
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
