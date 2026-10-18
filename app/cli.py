import argparse
import asyncio
import os

from .community_posts import post_community_post
from .post_queue import post_next


def main() -> None:
    parser = argparse.ArgumentParser(prog="trend-to-post")
    parser.add_argument("command", choices=["post", "community-connect", "community-building"])
    args = parser.parse_args()

    if args.command in {"community-connect", "community-building"}:
        slot = "connect" if args.command == "community-connect" else "building"
        asyncio.run(
            post_community_post(
                os.environ.get("X_AUTH_TOKEN", "").strip(),
                os.environ.get("X_CT0", "").strip(),
                slot,
            )
        )
        return

    if args.command == "post":
        published = asyncio.run(
            post_next(
                os.environ.get("X_AUTH_TOKEN", "").strip(),
                os.environ.get("X_CT0", "").strip(),
                os.environ.get("GEMINI_API_KEY", "").strip(),
            )
        )
        if not published:
            message = (
                "The queue completed without publishing a post. "
                "Check the lane messages above for text-generation, validation, or publishing failures."
            )
            print(f"::warning title=No X post published::{message}")
            summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
            if summary_path:
                with open(summary_path, "a", encoding="utf-8") as summary:
                    summary.write(
                        "## X post queue: no post published\n\n"
                        f"{message}\n"
                    )


if __name__ == "__main__":
    main()
