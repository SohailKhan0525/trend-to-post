import argparse
import asyncio
import sys
from .queue import prepare,publish


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','publish','preview'])
    parser.add_argument('--start-now',action='store_true')
    args=parser.parse_args()
    try:
        asyncio.run(publish() if args.command=='publish' else prepare(preview=args.command=='preview',start_now=args.start_now))
    except Exception as exc:
        # Third-party error strings can expose cookies or headers.
        print(f'::error::Bot stopped ({type(exc).__name__}). Check configuration, service status and reservations.',file=sys.stderr)
        raise SystemExit(1) from None
