# X Text-Only Original Post Bot

This repository runs an experimental X account focused on AI, technology, major products and companies, gaming, sports, and internet culture.

## What it does

- Publishes standalone ORIGINAL text posts, not Quote Posts, replies, or image memes.
- Uses recent public AI/tech/software/gaming/sports conversations only as optional topical seeds; it does not quote, attach, or mention the source author.
- Writes compact, funny, specific text posts with a randomized mix of 2–5 distinct emojis per automated post; repeated emoji tokens are rejected.
- Favors unusual formats, absurd rules, sharp observations, fictional product behaviour, and punchy one-liners over generic corporate phrasing.
- Prepares five manually publishable, text-only company-tag captions per UTC day, using varied truths, dares, mini-quests, product challenges, absurd demos, and playful verdicts. They contain the exact `@handle` and at least one emoji; they do not default to purchase offers or repost requests.
- Automatically publishes the other fifteen original text posts without @mentions.
- Keeps a bounded format memory to avoid repeating recent structures.
- Rejects political/current-affairs content, country references, fabricated company claims, hashtags, and links.
- Does not automatically like, follow, or reply to other accounts.

## The company-post format

These five captions should give the company something funny to do, admit, choose, or settle—not repeat the same “I'll buy…” line.

Example shapes (the bot should invent fresh, company-specific versions):
- `@BMW truth or dare: truth—admit the M4 has side-quest energy; dare—make its lights blink in morse code 😭`
- `@Microsoft I dare you to make the next Windows error arrive with a boss-fight health bar 💀`
- `@Steam settle this under oath: is a game left open on its title screen for 9 hours still gaming? 🎮😭`

A purchase-condition joke is allowed occasionally, but it must not be the default. No repost, like, follow, or boost requests. These five posts are **manual**: copy the caption from `state/company_manual_posts.md` and publish it yourself. The bot never submits company-tagged captions to X.

## Daily limits

- **20 total daily content slots**: 15 auto-published text originals + 5 manual company-tagged originals.
- **20 AI text generations per UTC day** across both lanes.
- Minimum **72 minutes between successful automated X posts**.
- Five company targets rotate with a four-day cooldown.
- The first queue run each UTC day prepares all five company captions together and reserves those five slots in the total of 20.
- Manual X publishing cannot be detected automatically. The queue reserves those content slots when it generates the captions, even before you manually publish them.
- These are caps, not a guarantee of twenty completed posts. Search, provider, validation, or X failures can reduce actual output.

## Workflow

The Actions workflow `Post X Text Originals` runs on manual dispatch or the existing `post-next` repository-dispatch event. The first run each UTC day generates the five tagged company captions and writes them to `state/company_manual_posts.md`; it then publishes one text-only original if the cooldown and quota allow.

The external scheduler should continue sending the existing `post-next` event at the desired cadence. The bot itself enforces the 72-minute minimum for posts that it publishes automatically.

## Required GitHub Secrets

- `X_AUTH_TOKEN`
- `X_CT0`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`
- `GEMINI_API_KEY` (fallback)
- `GEMINI_API_KEY_BACKUP` (optional fallback)

## State

`state/post_queue.json` stores daily slot and generation counters, company target rotation and cooldowns, recent source IDs, last automated post metadata, and recent format memory. Copy-ready manual company captions are stored in `state/company_manual_posts.md`.
