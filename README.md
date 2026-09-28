# X Post Queue

This repository posts the supplied `x.json` queue directly to X.

- 5,000 prewritten posts are kept in `x.json`.
- An external cron job triggers GitHub Actions every 30 minutes through `repository_dispatch`.
- The queue advances only after X accepts the post.
- X session credentials are read only from `X_AUTH_TOKEN` and `X_CT0` GitHub Secrets.
- Each normal 24-hour posting cycle contains 48 queue posts. Exactly 5 eligible non-`@grok` posts are selected pseudo-randomly for images; the remaining posts are text-only.
- The 5 image slots are selected deterministically from the cycle, so retries do not reshuffle the schedule or consume extra daily image slots.
- `@grok` posts are always posted without an image and do not consume one of the 5 image slots.
- The generated image uses Cloudflare Workers AI with FLUX.2 [klein] 4B. The prompt is based on the post's meaning and explicitly excludes readable post text.
- The generated image is created in the GitHub Actions runner, uploaded to X, attached to the post, and then removed from the runner.
- No Gemini, trend collection, generation, or automatic rewriting is used.

## Required GitHub Secrets

Keep the existing X secrets:

- `X_AUTH_TOKEN`
- `X_CT0`

Add these Cloudflare Workers AI secrets:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`

The Cloudflare API token must have Workers AI access. Cloudflare documents creating a Workers AI API token from the Workers AI dashboard and using the account ID with the Workers AI REST API.

The queue is exhausted after all 5,000 entries are published.

## Image quota behavior

The scheduler treats the normal 30-minute cadence as 48 posts per 24-hour cycle. For each cycle it chooses 5 eligible queue entries at random for images. Because the selection is seeded from the cycle, the same queue item stays selected on retries. Once those 5 image slots are used, all later posts in that cycle are text-only.

