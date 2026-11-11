import argparse
import asyncio
import os
import sys
from . import queue
from twikit.errors import InvalidSession


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("inspect","diagnose","metrics","run"))
    args=parser.parse_args()
    try:
        if args.command in ("diagnose","metrics"):
            from .diagnostics import diagnose, metrics
            asyncio.run(metrics() if args.command=="metrics" else diagnose())
        else:
            asyncio.run(queue.inspect() if args.command=="inspect" else queue.run(os.environ.get("GITHUB_EVENT_NAME","manual")))
    except (queue.SessionUnavailable,InvalidSession):
        print("::error::X did not accept the session. Check account access and the stored X_AUTH_TOKEN and X_CT0 secrets.",file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as exc:
        print(f"::error::Qofeno stopped ({type(exc).__name__}). Inspect this stage and the quote ledger.",file=sys.stderr)
        raise SystemExit(1) from None
