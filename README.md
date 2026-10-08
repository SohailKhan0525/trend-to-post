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
- Reserves five company-specific ORIGINAL post slots inside the same 20-content daily quota, alongside fifteen automatically published Quote Posts.
- The five company posts are generated with their exact @company mentions and written to `state/company_manual_posts.md` for you to publish manually.

## Publishing limits

- Maximum 20 total daily content slots: 15 automatically published Quote Posts + 5 manually published company posts.
- Maximum 20 AI generation calls per UTC day across both lanes combined.
- Minimum 72 minutes between successful automated posting events.
- Five company content slots are reserved inside the 20/day quota; they are manual posting slots, not extra posts.
- Company slots are distributed as content slots #4, #8, #12, #16, and #20 when generation succeeds normally.
- There are no automated self-replies.
- The generated company copy includes the exact `@handle`, but the bot never sends that mention to X automatically. You copy it from `state/company_manual_posts.md` and post it yourself.

These are operating limits, not a guarantee of 20 posts every day. X failures, rate limits, unavailable search results, or the AI rejecting a weak source can reduce output.

## Reach strategy

The bot is optimized for organic distribution, not artificial trend manipulation.

The creative goal is to make the quote comment worth reacting to: a sharp observation, a surprising framing, a compact invented artifact, a small interaction primitive, or a joke with enough tension that another user wants to add their own take.

### Company posts

Five famous companies are selected each UTC day with a four-day cooldown. For each target, the AI writes an ORIGINAL standalone post specifically shaped around that company's products, software, games, or recognizable brand behavior.

The generated company copy contains the exact `@handle` because you will publish these five posts manually. The bot itself does not transmit those mentions to X. See the official [X automation rules](https://help.x.com/en/rules-and-policies/x-automation).

Example shape:

`@BMW the M4 needs a button that deletes my group chat.`

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

Keep the external scheduler sending repository_dispatch with event type `post-next`. The single queue reserves five manual company-content slots and automatically publishes the other fifteen Quote Post slots. The company copies are stored in `state/company_manual_posts.md`.

## State

state/post_queue.json stores:

- daily total content-slot and AI-generation counters
- company-slot count and the five company handles used that day
- five manual company post copies in `state/company_manual_posts.md`
- last successful post metadata
- bounded source history
- bounded Quote Post Format Lab memory
- recent structure fingerprints
- daily five-company target queue and four-day cooldown records
- company-format lab memory

The research pass behind the current design used primary X automation/ranking material, GitHub Actions security guidance, current Twifork documentation, creator/posting studies, and recent LLM creativity research. It was a broad cross-section rather than a mechanically counted 200 unique websites; low-quality duplicate pages were not treated as 200 independent confirmations.
