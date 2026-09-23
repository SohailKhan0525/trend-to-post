from pathlib import Path
from difflib import SequenceMatcher

from .config import Settings
from .gemini import GeminiWriter
from .storage import filter_new_drafts, load_recent_texts, load_recent_trends, write_daily_markdown
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
        trends = filter_technology_ai_trends(await self.collect())
        recent_trends = load_recent_trends(7)

        def is_recent_duplicate(name: str) -> bool:
            normalized = name.casefold().strip()
            return any(
                normalized == recent.casefold().strip()
                or SequenceMatcher(None, normalized, recent.casefold().strip()).ratio() >= 0.88
                for recent in recent_trends
                if recent
            )

        fresh = [trend for trend in trends if not is_recent_duplicate(trend.name)]
        if fresh:
            return fresh

        expanded_limit = 50
        if self.settings.trend_limit < expanded_limit:
            print(
                f"No Technology/AI trends in the first {self.settings.trend_limit} X Trends; "
                f"expanding the X Trends collection to {expanded_limit}."
            )
            trends = filter_technology_ai_trends(
                await self.x.get_trends(expanded_limit)
            )
            fresh = [trend for trend in trends if not is_recent_duplicate(trend.name)]
        return fresh

    async def run(self, generate=True, post_type="question"):
        if post_type not in {"funny_ragebait", "breaking_news", "question"}:
            raise ValueError(f"Unsupported post type: {post_type}")

        trends = await self.collect_niche_trends()
        if not trends:
            raise XTrendError(
                "X returned no Technology/Artificial Intelligence trends in the "
                "available Trends window. No non-niche content was generated."
            )

        print(f"Post style: {post_type}")
        recent_texts = load_recent_texts(100)
        recent_trends = load_recent_trends(100)
        drafts = []

        if generate:
            for attempt in range(3):
                candidate = self.gemini.generate(
                    trends,
                    post_type=post_type,
                    recent_texts=recent_texts + [d.text for d in drafts],
                    recent_trends=recent_trends,
                )
                fresh = filter_new_drafts(candidate)
                if fresh:
                    drafts = fresh
                    break
                recent_texts.extend(d.text for d in candidate)
                print(
                    f"Generated duplicate; requesting a different {post_type} "
                    f"post (attempt {attempt + 2}/3)."
                )

            if not drafts:
                raise RuntimeError(
                    f"Could not generate a fresh {post_type} post after 3 duplicate checks."
                )

        path = write_daily_markdown(Path("."), trends, drafts)
        print(f"Wrote {len(drafts)} new {post_type} draft(s) to {path}")

    def health(self):
        print("trend-to-post: configuration loaded")
        print("X Trends only; niche: Technology + Artificial Intelligence")
        print("Daily schedule: 02:00 funny+ragebait+emoji; 08:00 breaking-news; 14:00 question; 20:00 question")
        print(f"location setting: {self.settings.x_trends_location}")
        print(f"trend limit: {self.settings.trend_limit}")
        print(f"draft count: {self.settings.ai_draft_count}")
