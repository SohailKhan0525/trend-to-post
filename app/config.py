import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_api_key_backup: str
    x_auth_token: str
    x_ct0: str
    x_cookies_file: Path
    x_trends_location: str
    trend_limit: int
    ai_draft_count: int
    log_level: str

    @classmethod
    def from_env(cls):
        load_dotenv()
        return cls(
            gemini_api_key=os.environ["GEMINI_API_KEY"],
            gemini_api_key_backup=os.environ.get("GEMINI_API_KEY_BACKUP", "").strip(),
            x_auth_token=os.environ.get("X_AUTH_TOKEN", "").strip(),
            x_ct0=os.environ.get("X_CT0", "").strip(),
            x_cookies_file=Path(os.environ.get("X_COOKIES_FILE", "data/x_cookies.json")),
            x_trends_location=os.environ.get("X_TRENDS_LOCATION", "worldwide").strip(),
            trend_limit=max(1, min(int(os.environ.get("TREND_LIMIT", "25")), 50)),
            ai_draft_count=max(1, min(int(os.environ.get("AI_DRAFT_COUNT", "3")), 10)),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )
