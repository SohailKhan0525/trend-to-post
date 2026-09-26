import argparse
import asyncio
import os

from .post_queue import post_next


def main() -> None:
    parser = argparse.ArgumentParser(prog="trend-to-post")
    parser.add_argument("command", choices=["post"])
    args = parser.parse_args()

    if args.command == "post":
        asyncio.run(
            post_next(
                os.environ.get("X_AUTH_TOKEN", "").strip(),
                os.environ.get("X_CT0", "").strip(),
            )
        )
