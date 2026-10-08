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
- Enforces a separate zero-country output guard; generated quote posts cannot contain country names or common country aliases, including inside the quoted fragment.
- Creates five rotating famous-company targets per UTC day with a strict four-day cooldown so the same company is not targeted again until eligible.
- Generates one original, company-specific reply-bait draft for each target and saves them to `state/brand_drafts.md` for manual approval.

## Publishing limits

- Maximum 20 automated posts per UTC day.
- Maximum 20 AI generation calls per UTC day for the automated Quote Post lane.
- Five additional company-original drafts are generated per UTC day for manual approval.
- Minimum 72 minutes between successful automated posting events.
- Company drafts are not auto-published and do not consume the automated 20-post publishing slots.
- Each automated publishing event is a single Quote Post; there are no automated self-replies.
- Run `python -m app brand-drafts` locally or use the daily `Generate Brand Drafts` workflow to refresh the five copy-ready company originals.

These are operating limits, not a guarantee of 20 posts every day. X failures, rate limits, unavailable search results, or the AI rejecting a weak source can reduce output.

## Reach strategy

The bot is optimized for organic distribution, not artificial trend manipulation.

The creative goal is to make the quote comment worth reacting to: a sharp observation, a surprising framing, a compact invented artifact, a small interaction primitive, or a joke with enough tension that another user wants to add their own take.

### Brand targets

The account has a separate company-reply lane. Five famous companies are selected each UTC day with a four-day cooldown. For each target, the AI writes an ORIGINAL post specifically shaped to give that company's social team, builders, or knowledgeable fans a reason to answer: a playful challenge, absurd product request, specific roast, ridiculous buying condition, or another concrete hook.

The target handle is included exactly once in the saved draft, but the bot does not auto-publish unsolicited company mentions. Drafts land in `state/brand_drafts.md` for manual approval. The main automated publishing lane remains Quote Posts only.

Example shape:

`@BMW the M4 needs a button that deletes my group chat.`

The point is specificity and a replyable premise, not "repost this" or empty engagement bait.

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

Keep the external scheduler sending repository_dispatch with event type post-next for the automated Quote Post lane. The `Generate Brand Drafts` workflow also runs once per UTC day and supports manual `workflow_dispatch` runs.

## State

state/post_queue.json stores:

- daily post and AI-generation counters
- last successful post/source
- bounded source history
- bounded Format Lab memory
- recent structure fingerprints
- daily five-company brand target queue and four-day cooldown records
- daily five-company original drafts, their generation metadata, and the separate company-format lab

The research pass behind the current design used primary X automation/ranking material, GitHub Actions security guidance, current Twifork documentation, creator/posting studies, and recent LLM creativity research. It was a broad cross-section rather than a mechanically counted 200 unique websites; low-quality duplicate pages were not treated as 200 independent confirmations.
