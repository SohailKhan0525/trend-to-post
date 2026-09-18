from pathlib import Path

from .config import Settings
from .gemini import GeminiWriter
from .storage import filter_new_drafts, write_daily_markdown
from .x_trends import XTrendClient


class TrendPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.x = XTrendClient(
            settings.x_auth_token,
            settings.x_ct0,
            settings.x_cookies_file,
            location=settings.x_trends_location,
        )
        self.gemini = GeminiWriter(settings.gemini_api_key, settings.ai_draft_count)

    async def collect(self):
        return await self.x.get_trends(self.settings.trend_limit)

    async def print_trends(self):
        for trend in await self.collect():
            print(f"{trend.rank:>2}. {trend.name}")

    async def run(self, generate=True):
        trends = await self.collect()
        drafts = self.gemini.generate(trends) if generate else []
        drafts = filter_new_drafts(drafts)
        path = write_daily_markdown(Path("."), trends, drafts)
        print(f"Wrote {len(drafts)} new drafts to {path}")

    def health(self):
        print("trend-to-post: configuration loaded")
        print("X Trends only; niche: Technology + Artificial Intelligence")
        print(f"location setting: {self.settings.x_trends_location}")
        print(f"trend limit: {self.settings.trend_limit}")
        print(f"draft count: {self.settings.ai_draft_count}")
