import argparse
import asyncio
import os

from .brand_drafts import generate_daily_brand_drafts
from .post_queue import post_next


def main() -> None:
    parser = argparse.ArgumentParser(prog="trend-to-post")
    parser.add_argument("command", choices=["post", "brand-drafts"])
    args = parser.parse_args()

    if args.command == "post":
        asyncio.run(
            post_next(
                os.environ.get("X_AUTH_TOKEN", "").strip(),
                os.environ.get("X_CT0", "").strip(),
                os.environ.get("GEMINI_API_KEY", "").strip(),
            )
        )
    elif args.command == "brand-drafts":
        generate_daily_brand_drafts()
