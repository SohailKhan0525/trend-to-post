import argparse
import asyncio
import os
import sys
from . import queue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command",choices=["preview","run"])
    args = parser.parse_args()
    try:
        asyncio.run(queue.preview() if args.command=="preview" else queue.run(os.environ.get("GITHUB_EVENT_NAME","manual")))
    except queue.SessionUnavailable:
        print("::error::X did not accept the browser session. No post was sent. "
              "Check X account access and refresh X_AUTH_TOKEN and X_CT0 in GitHub Actions secrets if needed. "
              "The next eligible run will check the session again.",file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as exc:
        # HTTP client exceptions may contain request credentials.
        print(f"::error::Dash Rattle stopped ({type(exc).__name__}). Check the failed stage and reservation.",file=sys.stderr)
        raise SystemExit(1) from None
