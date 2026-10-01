# X Trend Quote Bot

This repository runs an automated X quote-posting bot.

- Reads current X trends, then finds actual posts behind those trends.
- Checks Top and Latest results and prefers recent original posts with the highest public view count.
- Skips replies, retweets, quote-posts, sensitive/non-English posts, and recently used sources.
- Gemini 2.5 Flash researches the source with Google Search, then writes a fresh quote-post.
- Gemini may reject weak sources; those are remembered so later runs can try another source.
- Publishes an actual X quote-post using the source tweet URL.
- Maximum 10 quote-posts per UTC day, with about 144 minutes between successful posts.
- No prewritten queue, AI images, Cloudflare, or old trend-report generation remains in the runtime path.

## Required GitHub Secrets

- `X_AUTH_TOKEN`
- `X_CT0`
- `GEMINI_API_KEY`

## Scheduling

Keep the external cron job sending `repository_dispatch` with event type `post-next`. The workflow also supports manual `workflow_dispatch` runs.

## State

`state/post_queue.json` stores the UTC-day count, last successful quote-post, and bounded source history.
