# X Experimental Culture Bot

This repository runs an automated, text-first X account focused on AI, technology, major tech companies/products, gaming, and sports.

## What it does

- Discovers live conversations with targeted AI/tech/product/gaming/sports searches instead of automatically posting because an X trend is trending.
- Prefers recent original posts that already show conversation velocity and public engagement, because the account adds a comment to an existing conversation rather than starting a disconnected thread.
- Gives established/high-signal authors a soft ranking boost using public profile signals such as follower count, verification, and professional bio language.
- Rejects replies, retweets, quote-posts, sensitive/non-English posts, political/current-affairs topics, recently used sources, and stale posts.
- Sends the source to the creative engine as untrusted data, never as instructions.
- The creative engine is locked to quote_post: every successful post attaches the selected source and adds one short, verbatim source fragment plus an original comment.
- Uses a mutation engine with four modes: premise inversion, cross-domain collision, invented rule, and structural break.
- Maintains a bounded Format Lab so recent structures are not repeatedly recycled.
- Does not automate self-replies; each publishing slot is reserved for a single quote post.
- Keeps political/current-affairs content blocked at both source-selection and output-validation stages.

## Publishing limits

- Maximum 20 total posts per UTC day.
- Maximum 20 AI generation calls per UTC day.
- Minimum 72 minutes between successful posting events.
- Each successful publishing event consumes one post slot; there are no automated self-replies.

These are operating limits, not a guarantee of 20 posts every day. X failures, rate limits, unavailable search results, or the AI rejecting a weak source can reduce output.

## Reach strategy

The bot is optimized for organic distribution, not artificial trend manipulation.

The creative goal is to make the post worth reacting to on its own: a sharp observation, a surprising framing, a compact invented artifact, a small interaction primitive, or a joke with enough tension that another user wants to add their own take.

The account is deliberately text-first; image generation is not required by the runtime.

## Security

- GitHub Actions uses least-privilege workflow permissions and scopes sensitive secrets only to the steps that need them.
- Third-party GitHub Actions are pinned to immutable commit SHAs.
- Source posts are explicitly treated as untrusted data to reduce prompt-injection risk.
- Source text is bounded before being placed in an AI prompt.
- Political/current-affairs output is validated again after generation.
- The workflow retries transient GitHub fetch/push failures rather than treating one 5xx response as a permanent bot failure.

## Required GitHub Secrets

- X_AUTH_TOKEN
- X_CT0
- GEMINI_API_KEY
- GEMINI_API_KEY_BACKUP (recommended)
- CLOUDFLARE_ACCOUNT_ID
- CLOUDFLARE_API_TOKEN

## Scheduling

Keep the external scheduler sending repository_dispatch with event type post-next. The workflow also supports manual workflow_dispatch runs.

## State

state/post_queue.json stores:

- daily post and AI-generation counters
- last successful post/source
- bounded source history
- bounded Format Lab memory
- recent structure fingerprints

The research pass behind the current design used primary X automation/ranking material, GitHub Actions security guidance, current Twifork documentation, creator/posting studies, and recent LLM creativity research. It was a broad cross-section rather than a mechanically counted 200 unique websites; low-quality duplicate pages were not treated as 200 independent confirmations.
