# Qofeno

[@qofenoai](https://x.com/qofenoai) is an openly AI-run X account about software, coding tools and the people who use them. GitHub Actions checks for publishing work every ten minutes, with publication limits applied to half-hour windows. Each bounded runner starts its successor directly; cron provides a recovery trigger. Cloudflare Workers AI drafts posts and replies. Cloudflare D1 preserves publication history between runs.

## What it publishes

| Per UTC day | Target | What qualifies |
| --- | ---: | --- |
| Original posts | 40 | A distinct opinion, observation or joke that makes sense on its own and passes editorial review |
| Replies | 70 | A relevant response to a direct invitation or an eligible discovered public post, grounded in the post or its verified parent |

The publisher spreads opportunities across the day, with up to two replies and one original in each half-hour window. These are daily targets and hard ceilings, not guaranteed counts. Actual counts depend on GitHub starting the scheduled runs, eligible conversations, fresh ideas, the model budget and X accepting each publication. The cron is `7,17,27,37,47,57 * * * *`; GitHub may delay or skip scheduled runs, so each half-hour has three wake-up opportunities. Priority account posts are checked first, followed by direct invitations and discovered conversations. Each person can receive up to two replies in the same original conversation, and Qofeno answers any individual post once. To opt out, post `@qofenoai stop`.

`@zaheer0525` is a priority account. The first successful timeline check saves its stable account ID and establishes a baseline in D1; later checks consider new original public posts, even below the discovery view threshold. Old posts, replies and reposts are excluded. Pending posts and answered IDs survive runner restarts. Priority replies share the existing daily and half-hour limits, duplicate protection, opt-outs, source checks and editorial review.

The existing Twifork client reads public mentions, direct responses and recent threads. Public conversations about software and AI can inspire standalone originals or qualify for a reply through discovery. Discovered posts must pass the existing freshness, reach, author-contact and editorial checks. A joke can earn a one-line comeback. A real question can get a concrete answer. Qofeno skips political or country-related subjects, protected accounts and sensitive posts.

## Memory and delivery

D1 holds the account’s publication ledger, pending sends, source identities, text fingerprints and public conversation history. The writer can see recent topics and earlier exchanges. Before posting, the publisher checks content and sources for reuse and reserves the draft in D1. It reads the result back from X to verify the actual account, text and reply parent. An ambiguous send stays reserved for reconciliation on a later run.

The scheduled and manually dispatched GitHub jobs share a publishing lock. During migration, stop the former publisher and let its D1 lease expire. The first GitHub run claims the existing ledger and reconciles outstanding deliveries. Keep the existing D1 database when changing runners or rotating credentials.

Each GitHub runner checks every ten minutes for 55 minutes, finishes any bounded cycle already in progress, then dispatches `publish.yml` on `main` using its temporary `GITHUB_TOKEN` with `actions: write`. No personal token or external scheduler is required. The publishing child does not receive that token. Workflow concurrency coalesces pending cron and handoff runs; D1 provides the separate publication lock and duplicate checks. A workflow-file deployment uses a short first run so its successor can be verified immediately. Cancellation stops the runner without requesting another handoff. To stop publishing, disable the workflow and cancel its active and pending runs. Failed dispatches are visible as failed jobs, and the ten-minute cron can recover the chain.

## Configuration

| Repository Actions setting | Purpose |
| --- | --- |
| Secrets `X_AUTH_TOKEN`, `X_CT0` | Existing X session for discovery, publication and readback |
| Secrets `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | Workers AI and D1 access |
| Optional secret `CLOUDFLARE_D1_API_TOKEN` | Separate account D1 Edit token |
| Optional variable `D1_DATABASE_ID` | UUID of the existing D1 database |
| Optional variable `QOFENO_AI_MODEL` | Model override. The default is `@cf/google/gemma-4-26b-a4b-it` |

The publisher accounts for model calls against its daily allowance and honors model and X cooldowns. The publisher runs on the repository’s default branch. Its GitHub Actions log records decisions, skipped opportunities and verified X links. The separate metrics workflow reads engagement on verified publications.

To run the tests locally, install the project dependencies and use Python 3.12.

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -q
```
