# X Text-Only Original Post Bot

This repository runs an experimental X account focused on AI, technology, major products and companies, gaming, sports, and internet culture.

## What it does

- Publishes standalone ORIGINAL text posts, not Quote Posts, replies, or image memes.
- Uses recent public AI/tech/software/gaming/sports conversations only as optional topical seeds; it does not quote, attach, or mention the source author.
- Automatic original posts may use ONLY these emojis: 👀 🔥 😭 ❤️‍🩹 😂 😙 🥀 🤣 🥳 🫠 😤 💀. The randomized target is 2–5 distinct emojis per post (at most one extra when it fits); repeats and emojis outside this list are rejected.
- Favors unusual formats, absurd rules, sharp observations, fictional product behaviour, and punchy one-liners over generic corporate phrasing.
- Prepares five manually publishable, text-only company-tag captions per UTC day. Their formats rotate between conditional “if” lines, “imagine” scenarios, an occasional “I'll buy” condition, choose-one prompts, feature challenges, and other product-specific jokes. Company captions may use ONLY 😂 👀 🥳 🫠; other emojis are rejected.
- Automatically publishes the other fifteen original text posts without @mentions.
- Keeps a bounded format memory to avoid repeating recent structures.
- Rejects political/current-affairs content, country references, fabricated company claims, hashtags, and links.
- Does not automatically like, follow, or reply to other accounts.

## The company-post format

Company captions should not all share one template. The bot rotates formats across the daily batch, such as conditional “if” lines, “imagine...” scenarios, the occasional purchase condition, choose-one questions, absurd product challenges, mock ultimatums, and mini-quests. Truth-or-dare is not used, and company captions never ask for reposts, likes, follows, or boosts.

Example shapes (the bot should invent fresh, company-specific versions):
- `@BMW if the M4 can parallel park by itself, I'll forgive my driving 😂`
- `@Microsoft imagine Windows error messages entering like a final boss with theme music 👀`
- `@adidas I'll buy the Ultraboost if it can detect when I walk toward the fridge instead of the gym 🫠`

Company captions may use ONLY these emojis: 😂 👀 🥳 🫠. These five posts are **manual**: copy the caption from `state/company_manual_posts.md` and publish it yourself. The bot never submits company-tagged captions to X.

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
