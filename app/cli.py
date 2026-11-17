import argparse
import asyncio
import os
import re
import sys
from . import editorial
from .store import SessionUnavailable, summary
from .ai_budget import AIPaused
from .memory import Memory, MemoryAccessDenied, MemoryUnavailable
from .store import ledger_mode, load, read_state
from twikit.errors import InvalidSession


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("inspect","inbox","memory","metrics","run","serve","server-check","server-status","seed-server"))
    parser.add_argument("--allow-ledger", action="store_true", help="For the memory check, allow verified-history reads if D1 denies access.")
    parser.add_argument("--release", help="Expected 40-character worker release for server-status.")
    parser.add_argument("--ready", action="store_true", help="Allow a preflight-verified server waiting for handover.")
    parser.add_argument("--mode", choices=("inbox", "original"), help="Decision type for a supervised run.")
    args=parser.parse_args()
    if args.mode and args.command != 'run':
        parser.error('--mode requires run')
    try:
        if args.command in ('run', 'inbox'):
            from .server_ledger import connect
            ledger = connect()
            if ledger_mode() == 'd1':
                ledger.check_lease(os.environ.get('QOFENO_INSTANCE'))
            elif ledger.active():
                summary('Azure owns the publication ledger. GitHub skipped this wake.')
                return
        if args.command in ('serve', 'server-check'):
            from . import server
            asyncio.run(server.serve() if args.command == 'serve' else server.check())
        elif args.command in ('seed-server', 'server-status'):
            from .server_ledger import connect
            ledger = connect()
            if args.command == 'seed-server':
                if not ledger.active() and ledger.seed(read_state(), args.release or os.environ.get('QOFENO_RELEASE')):
                    summary('Transferred the complete GitHub publication ledger and outbox to D1.')
                else:
                    summary('Azure already owns the publication ledger.')
            else:
                phase = ledger.status(args.release, args.ready)
                if not phase:
                    print('No recent healthy heartbeat for the requested release and phase.', file=sys.stderr)
                    raise SystemExit(1)
                summary(f'Azure worker release {args.release}: {phase}.')
        elif args.command == 'memory':
            history = load()['history']
            try:
                memory = Memory.connect()
                inserted = memory.sync(history)
                recent = memory.context([])['recent_own']
                summary(f'D1 connected. Synced {inserted} additional verified posts. Read back {len(recent)} recent posts.')
            except MemoryAccessDenied:
                if not args.allow_ledger:
                    raise
                memory = editorial.ledger_after_denial(history)
                recent = memory.context([])['recent_own']
                summary(f'Verified-history memory ready. Read back {len(recent)} recent posts.')
        elif args.command == 'inbox':
            asyncio.run(editorial.check_inbox())
        elif args.command == 'metrics':
            from .metrics import metrics
            asyncio.run(metrics())
        else:
            event = args.mode or "original"
            asyncio.run(editorial.inspect() if args.command == "inspect" else editorial.run(event))
    except AIPaused as exc:
        summary(f"AI paused until {exc.until.isoformat()}: {exc.reason}")
        if args.command in ('serve','server-check'):
            raise SystemExit(1) from None
    except (editorial.DeliveryUnresolved, MemoryUnavailable) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        raise SystemExit(1) from None
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
