# X Post Queue

This repository posts the supplied `x.json` queue directly to X.

- 5,000 prewritten posts are kept in `x.json`.
- GitHub Actions publishes one post every 3 hours in Asia/Kolkata.
- The queue advances only after X accepts the post.
- X session credentials are read only from `X_AUTH_TOKEN` and `X_CT0` GitHub Secrets.
- No Gemini, trend collection, generation, or automatic rewriting is used.

The queue is exhausted after all 5,000 entries are published.
