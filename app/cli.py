import argparse
import asyncio
import os
import sys
from . import queue
from .ai_budget import AIPaused
from twikit.errors import InvalidSession


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("inspect","diagnose","metrics","model-check","run"))
    args=parser.parse_args()
    try:
        if args.command == 'model-check':
            from .model_check import check
            check()
        elif args.command in ("diagnose","metrics"):
            from .diagnostics import diagnose, metrics
            asyncio.run(metrics() if args.command=="metrics" else diagnose())
        else:
            asyncio.run(queue.inspect() if args.command=="inspect" else queue.run(os.environ.get("GITHUB_EVENT_NAME","manual")))
    except AIPaused as exc:
        queue.summary(f"AI paused until {exc.until.isoformat()}: {exc.reason} No post sent.")
    except (queue.SessionUnavailable,InvalidSession):
        print("::error::X did not accept the session. Check account access and the stored X_AUTH_TOKEN and X_CT0 secrets.",file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as exc:
        print(f"::error::Qofeno stopped ({type(exc).__name__}). Inspect this stage and the quote ledger.",file=sys.stderr)
        raise SystemExit(1) from None
