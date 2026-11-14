# Qofeno

An openly AI-run X account, **@qofenoai**, about AI coding and practical software tools. This repository publishes contextual replies to original posts and two separate invitations to follow the account. The former quote/repost and self-reply workflows have been removed. Existing posts, publication history and session secrets are preserved.

## Current publishing rules

- At most **one contextual reply every 30 minutes**, with **48 reply opportunities per UTC day**, plus **two own-profile follow posts** at least six hours apart. One every half hour means 48 replies, not 50.
- A source must be a public original post from the last 24 hours with **at least 10,000 measured post views**. Followers, video plays and missing counts do not qualify. No protected/blocked authors, sensitive posts, replies, reposts or quotes.
- Search covers AI coding tools and firsthand developer posts about debugging, shipping fixes, tests and code review. Each search gets a turn among the first candidates reviewed. Any public account can qualify through its post views, regardless of follower count or verification. Politics, countries and national flags are excluded. Each author is eligible once per 24 hours.
- Cloudflare Workers AI first identifies the author’s point. It can answer an author’s question, make a dry joke about a specific detail, or playfully challenge an idea. A practical question belongs when the post leaves a concrete problem open. A second review checks that the reply fits the original and that the joke or question preserves its facts.
- Source text is fetched again before sending. Changed, deleted or newly ineligible sources are skipped. Source IDs and edit aliases, specific article URLs, event fingerprints and text fingerprints are kept permanently to prevent reuse; recent similar wording is also rejected.
- Follow posts open with “Follow me now before it’s too late.” as a playful invitation, followed by a different coding/AI sentence each time. Full posts must remain distinct; actual deadlines, fake achievements and guaranteed outcomes are rejected.

## Delivery and scheduling

The active workflow is **Publish contextual replies and own follow posts**, in `.github/workflows/editorial.yml`. It accepts the existing `post-next` repository dispatcher, GitHub cron, manual runs and code pushes. A day-seeded offset varies scheduled times. Actual confirmed completion timestamps enforce the minimum 30-minute reply spacing, including across midnight. Missed opportunities are skipped; there are no catch-up bursts. Research time, GitHub scheduling delays, source quality, AI quota and X access can reduce the number actually sent.

The existing `X_AUTH_TOKEN` and `X_CT0` secrets authenticate the expected account. Each approved post and its duplicate identities are committed **before** the send. The returned ID is saved before readback verifies author, exact normalized copy and the intended reply parent. An uncertain send keeps its reservation and stops further publication rather than automatically retrying. A known wrong parent or author also pauses delivery. Explicit X permission errors skip the rejected source; rate limits pause publication. No alternate accounts, transport switching or restriction bypasses are attempted.

This repository uses the existing cookie-based client. Working credentials do not establish platform approval: X’s automation rules restrict unsolicited automated replies and require approval for AI reply bots, and its self-service API limits reply eligibility. The code cannot promise successful delivery or continued account access. See the current [automation rules](https://help.x.com/en/rules-and-policies/x-automation) and [API reply guidance](https://docs.x.com/x-api/posts/manage-tweets/introduction).

## Workers AI

Default model: `@cf/google/gemma-4-26b-a4b-it`. Credentials remain in `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. A retiring Qwen override maps to the current default; other explicitly supported priced model overrides are retained.

A local budget reserves estimated usage before requests: 9,000 neurons/day and 400/hour, adjusted when token usage is available. Other apps sharing the Cloudflare allowance are not visible to this ledger. Confirmed daily-quota errors pause until the next UTC day; other AI rate-limit errors pause for 15 minutes. Source review normally uses two model calls, with at most one format repair. Rejected sources are cached so the same unsuitable material is not repeatedly reviewed.

## Operations

- `python -m app run`: research and publish an eligible scheduled reply or own follow post.
- `python -m app inspect`: report ledger/schedule status without model calls or sends.
- `python -m app metrics`: read actual own-post views, likes, replies, reposts and quotes. Never publishes or writes the ledger.
- `python -m unittest discover -s tests -v`: validate scheduling, content rules, durable delivery and provider behavior.

Metrics distinguish Qofeno’s impressions from source-post views. Missing values remain unknown. Historic self-replies are shown separately from estimated audience replies. Publication success does not guarantee impressions, likes or replies.

Failures include the operation and source ID without exposing credentials. The ledger in `state/qofeno.json` must not be deleted or reset to force publication. Old ledger files are retained as historical records, not active publishers.
