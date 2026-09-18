import json
from datetime import datetime, timezone

from google import genai
from tenacity import retry, stop_after_attempt, wait_exponential

from .models import Draft, Trend

MODEL = "gemini-3.8-flash"

INSTRUCTION = """You write one original short-form post for a technology and artificial intelligence account.
The input is an X Trend used only as research input. Do not try to manipulate X Trends.
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

class GeminiWriter:
    def __init__(self, api_key, draft_count):
        self.client = genai.Client(api_key=api_key)
        self.draft_count = 1

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def generate(self, trends: list[Trend]):
        payload = {"trends": [{"rank": t.rank, "name": t.name} for t in trends]}
        prompt = INSTRUCTION + "\nINPUT:\n" + json.dumps(payload, ensure_ascii=False)

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

        data = json.loads(interaction.output_text)
        now = datetime.now(timezone.utc).isoformat()
        text = str(data["text"]).strip()
        if len(text) > 280:
            raise ValueError("Gemini returned a post longer than 280 characters.")

        return [Draft(
            trend=str(data["trend"]),
            angle=str(data["angle"]),
            text=text,
            generated_at=str(data.get("generated_at") or now),
        )]
