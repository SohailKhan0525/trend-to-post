import json
from datetime import datetime, timezone
from google import genai
from tenacity import retry, stop_after_attempt, wait_exponential
from .models import Draft, Trend

INSTRUCTION = '''You write original short-form posts for one person's technology and artificial intelligence account.
The input is an X Trend used only as research input. Do not try to manipulate X Trends.
Only use trends plausibly related to technology or artificial intelligence.
Write like a knowledgeable human: concrete, concise, natural, and specific.
Do not invent facts or personal experiences. Do not copy or closely paraphrase existing posts.
Avoid engagement bait, generic AI phrasing, excessive hashtags, and empty summaries.
Return JSON only as an array of objects with trend, angle, text, generated_at.'''

class GeminiWriter:
    def __init__(self, api_key, draft_count):
        self.client = genai.Client(api_key=api_key)
        self.draft_count = draft_count

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def generate(self, trends: list[Trend]):
        payload = {'trends': [{'rank': t.rank, 'name': t.name} for t in trends], 'draft_count': self.draft_count}
        prompt = INSTRUCTION + '\nGenerate ' + str(self.draft_count) + ' distinct candidates from the relevant trends.\nINPUT:\n' + json.dumps(payload, ensure_ascii=False)
        response = self.client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config={'response_mime_type': 'application/json'})
        data = json.loads(response.text)
        now = datetime.now(timezone.utc).isoformat()
        return [Draft(trend=str(x['trend']), angle=str(x['angle']), text=str(x['text']).strip(), generated_at=str(x.get('generated_at', now))) for x in data]
