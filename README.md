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
- Publishes five company-specific ORIGINAL posts inside the same 20-post daily quota, alongside fifteen Quote Posts.

## Publishing limits

- Maximum 20 automated posts per UTC day.
- The 20 posts are split into 15 Quote Posts and 5 company-specific ORIGINAL posts.
- Maximum 20 AI generation calls per UTC day across both lanes combined.
- Minimum 72 minutes between successful automated posting events.
- Company posts are real publishing events; they do not sit in a separate draft quota.
- Company slots are distributed as posts #4, #8, #12, #16, and #20 when the queue succeeds normally.
- There are no automated self-replies.
- X policy requires automated mentions to be opt-in; the automated company lane therefore does not send unsolicited `@handle` mentions. The target handle remains attached to the queue/state for manual use.

These are operating limits, not a guarantee of 20 posts every day. X failures, rate limits, unavailable search results, or the AI rejecting a weak source can reduce output.

## Reach strategy

The bot is optimized for organic distribution, not artificial trend manipulation.

The creative goal is to make the quote comment worth reacting to: a sharp observation, a surprising framing, a compact invented artifact, a small interaction primitive, or a joke with enough tension that another user wants to add their own take.

### Company posts

Five famous companies are selected each UTC day with a four-day cooldown. For each target, the AI writes an ORIGINAL standalone post specifically shaped around that company's products, software, games, or recognizable brand behavior.

The company name is used directly in the copy when natural. The automated lane does not add unsolicited `@mentions`, because X currently prohibits automated mentions sent to users on an unsolicited basis. See the official [X automation rules](https://help.x.com/en/rules-and-policies/x-automation).

Example shape:

`BMW needs a button in the M4 that instantly deletes my group chat.`

The point is specificity and a concrete premise another person can joke about or challenge, not "repost this" or empty engagement bait.

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

Keep the external scheduler sending repository_dispatch with event type `post-next`. The single queue decides whether the next successful slot is a company post or a Quote Post.

## State

state/post_queue.json stores:

- daily total post and AI-generation counters
- company-post count and the five company handles used that day
- last successful post metadata
- bounded source history
- bounded Quote Post Format Lab memory
- recent structure fingerprints
- daily five-company target queue and four-day cooldown records
- company-format lab memory

The research pass behind the current design used primary X automation/ranking material, GitHub Actions security guidance, current Twifork documentation, creator/posting studies, and recent LLM creativity research. It was a broad cross-section rather than a mechanically counted 200 unique websites; low-quality duplicate pages were not treated as 200 independent confirmations.
