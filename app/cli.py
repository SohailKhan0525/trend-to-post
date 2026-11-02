import argparse
import asyncio
import sys
from .queue import prepare,publish


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','publish','preview'])
    parser.add_argument('--start-now',action='store_true')
    parser.add_argument('--all-categories',action='store_true',help='Preview all audiences without publishing or saving state')
    args=parser.parse_args()
    try:
        asyncio.run(publish() if args.command=='publish' else prepare(preview=args.command=='preview',start_now=args.start_now,all_categories=args.all_categories))
    except Exception as exc:
        # Third-party exception text may include credentials or HTTP headers.
        print(f'::error::Resource bot stopped ({type(exc).__name__}). Check link supply, service health and reservations.',file=sys.stderr)
        raise SystemExit(1) from None
