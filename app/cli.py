import argparse
import asyncio
import os

from .post_queue import post_next


def main() -> None:
    parser = argparse.ArgumentParser(prog="trend-to-post")
    parser.add_argument("command", choices=["post"])
    args = parser.parse_args()

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
                "Check the lane messages above for source, photo-license, or generation failures."
            )
            print(f"::warning title=No X post published::{message}")
            summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
            if summary_path:
                with open(summary_path, "a", encoding="utf-8") as summary:
                    summary.write(
                        "## X post queue: no post published\n\n"
                        f"{message}\n"
                    )
