# X Post Queue

This repository posts the supplied `x.json` queue directly to X.

- 5,000 prewritten posts are kept in `x.json`.
- An external cron job triggers GitHub Actions every 30 minutes through `repository_dispatch`.
- The queue advances only after X accepts the post.
- X session credentials are read only from `X_AUTH_TOKEN` and `X_CT0` GitHub Secrets.
- Non-`@grok` posts get one generated image from Cloudflare Workers AI using FLUX.1 [schnell].
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
