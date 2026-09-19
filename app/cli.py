import argparse
import asyncio
from .config import Settings
from .logging_setup import configure_logging
from .pipeline import TrendPipeline

def main():
    parser = argparse.ArgumentParser(prog='trend-to-post')
    parser.add_argument('command', choices=['health', 'trends', 'generate', 'run'])
    parser.add_argument('--post-type', choices=['standard', 'funny', 'breaking_news', 'question'], default='standard')
    args = parser.parse_args()
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    pipeline = TrendPipeline(settings)
    if args.command == 'health':
        pipeline.health(); return
    if args.command == 'trends':
        asyncio.run(pipeline.print_trends()); return
    asyncio.run(pipeline.run(generate=True, post_type=args.post_type))
