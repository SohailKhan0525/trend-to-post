import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    x_username: str
    x_email: str
    x_password: str
    x_cookies_file: Path
    x_trends_location: str
    trend_limit: int
    ai_draft_count: int
    log_level: str

    @classmethod
    def from_env(cls):
        load_dotenv()
        return cls(
            gemini_api_key=os.environ['GEMINI_API_KEY'],
            x_username=os.environ['X_USERNAME'],
            x_email=os.environ.get('X_EMAIL', ''),
            x_password=os.environ['X_PASSWORD'],
            x_cookies_file=Path(os.environ.get('X_COOKIES_FILE', 'data/x_cookies.json')),
            x_trends_location=os.environ.get('X_TRENDS_LOCATION', 'worldwide'),
            trend_limit=int(os.environ.get('TREND_LIMIT', '25')),
            ai_draft_count=int(os.environ.get('AI_DRAFT_COUNT', '3')),
            log_level=os.environ.get('LOG_LEVEL', 'INFO'),
        )
