from pathlib import Path
from .config import Settings
from .gemini import GeminiWriter
from .storage import filter_new_drafts, load_recent_texts, write_daily_markdown
from .x_trends import XTrendClient, XTrendError, filter_technology_ai_trends

class TrendPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.x = XTrendClient(
            settings.x_auth_token,
            settings.x_ct0,
            settings.x_cookies_file,
            location=settings.x_trends_location,
        )
        self.gemini = GeminiWriter(
            settings.gemini_api_key,
            settings.ai_draft_count,
            backup_api_key=settings.gemini_api_key_backup,
        )

    async def collect(self):
        return await self.x.get_trends(self.settings.trend_limit)

    async def print_trends(self):
        for trend in filter_technology_ai_trends(await self.collect()):
            print(f"{trend.rank:>2}. {trend.name}")

    async def collect_niche_trends(self):
        """Collect enough X Trends to find the requested Technology/AI niche.

        X's top Trends can contain no Technology/AI item at a low limit. In that
        case, expand the same X Trends request to the API's supported maximum
        instead of failing the scheduled job.
        """
        trends = filter_technology_ai_trends(await self.collect())
        if trends:
            return trends

        expanded_limit = 50
        if self.settings.trend_limit < expanded_limit:
            print(
                f"No Technology/AI trends in the first {self.settings.trend_limit} X Trends; "
                f"expanding the X Trends collection to {expanded_limit}."
            )
            trends = filter_technology_ai_trends(
                await self.x.get_trends(expanded_limit)
            )
        return trends

    async def run(self, generate=True, post_type="standard"):
        trends = await self.collect_niche_trends()
        if not trends:
            raise XTrendError(
                "X returned no Technology/Artificial Intelligence trends in the "
                "available Trends window. No non-niche content was generated."
            )
        print(f"Post style: {post_type}")
        recent_texts = load_recent_texts(100)
        drafts = []
        if generate:
            for attempt in range(3):
                candidate = self.gemini.generate(
                    trends,
                    post_type=post_type,
                    recent_texts=recent_texts + [d.text for d in drafts],
                )
                fresh = filter_new_drafts(candidate)
                if fresh:
                    drafts = fresh
                    break
                recent_texts.extend(d.text for d in candidate)
                print(f"Generated duplicate; requesting a different {post_type} post (attempt {attempt + 2}/3).")
        path = write_daily_markdown(Path("."), trends, drafts)
        print(f"Wrote {len(drafts)} new drafts to {path}")

    def health(self):
        print("trend-to-post: configuration loaded")
        print("X Trends only; niche: Technology + Artificial Intelligence")
        print(f"location setting: {self.settings.x_trends_location}")
        print(f"trend limit: {self.settings.trend_limit}")
        print(f"draft count: {self.settings.ai_draft_count}")
