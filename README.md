# X Post Queue

This repository posts the supplied `x.json` queue directly to X.

- 5,000 prewritten posts are kept in `x.json`.
- An external cron job triggers GitHub Actions every 30 minutes through `repository_dispatch`.
- The queue advances only after X accepts the post.
- X session credentials are read only from `X_AUTH_TOKEN` and `X_CT0` GitHub Secrets.
- Non-`@grok` posts get one generated image from Cloudflare Workers AI using FLUX.2 [klein] 4B. The image is generated from the post's meaning and instructed not to contain post text.
- `@grok` posts are posted without an image.
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

## Free-plan usage

Cloudflare Workers AI currently includes 10,000 Neurons per day on Workers Free. This repository uses FLUX.2 [klein] 4B at 1024x1024; at the configured 30-minute cadence, the repository's maximum of 48 image generations per day remains below that daily allocation. Cloudflare states that requests beyond the Free allocation fail rather than automatically becoming paid usage.
