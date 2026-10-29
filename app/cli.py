import argparse
import asyncio
import sys
from .queue import prepare,publish


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','publish','preview'])
    args=parser.parse_args()
    try:
        asyncio.run(prepare(preview=args.command=='preview') if args.command!='publish' else publish())
    except Exception as exc:
        # Third-party exceptions can contain HTTP headers/cookies. Never print them.
        print(f'::error::Bot stopped ({type(exc).__name__}). Check secret configuration, service status, and unresolved state reservations.',file=sys.stderr)
        raise SystemExit(1) from None
