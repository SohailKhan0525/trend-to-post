import argparse
import asyncio
import os
import re
import sys
from . import editorial
from .store import SessionUnavailable, summary
from .ai_budget import AIPaused
from twikit.errors import InvalidSession


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("inspect","metrics","run"))
    args=parser.parse_args()
    try:
        if args.command == 'metrics':
            from .metrics import metrics
            asyncio.run(metrics())
        else:
            asyncio.run(editorial.inspect() if args.command=="inspect" else editorial.run(os.environ.get("GITHUB_EVENT_NAME","manual")))
    except AIPaused as exc:
        summary(f"AI paused until {exc.until.isoformat()}: {exc.reason}")
    except (SessionUnavailable,InvalidSession):
        print("::error::X did not accept the session. Check account access and the stored X_AUTH_TOKEN and X_CT0 secrets.",file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as exc:
        stage = editorial.STAGE
        # X numeric error codes are actionable; raw response bodies and headers
        # can contain credentials or interstitial markup and must never be logged.
        codes = ','.join(sorted(set(re.findall(r'[\"\x27]code[\"\x27]\s*:\s*(\d{1,4})\b', str(exc))))) or 'unavailable'
        print(f"::error::Qofeno stopped ({type(exc).__name__}); stage={stage['name']}; source={stage['source_id'] or 'none'}; X error codes={codes}. Inspect the preserved ledger before retrying an unknown send.",file=sys.stderr)
        raise SystemExit(1) from None
