# X Text-Only Original Post Bot

This repository runs an experimental X account focused on AI, technology, major products and companies, gaming, sports, and internet culture.

## What it does

- Publishes standalone ORIGINAL text posts, not Quote Posts, replies, or image memes.
- Uses an AI model for every generated caption. Eleven daily originals are invented from broad topic seeds; four more are grounded in real X conversations and queued for manual review with the source link.
- Automatic original posts may use ONLY these emojis: 👀 🔥 😭 ❤️‍🩹 😂 😙 🥀 🤣 🥳 🫠 😤 💀. The randomized target is 2–5 distinct emojis per post (at most one extra when it fits); repeats and emojis outside this list are rejected.
- Favors unusual formats, absurd rules, sharp observations, fictional product behaviour, and punchy one-liners over generic corporate phrasing.
- Prepares five manually publishable, text-only company-tag captions per UTC day. Their formats rotate between conditional “if” lines, “imagine” scenarios, an occasional “I'll buy” condition, choose-one prompts, feature challenges, and other product-specific jokes. Company captions may use ONLY 😂 👀 🥳 🫠; other emojis are rejected.
- Automatically publishes eleven invented, text-only original posts without @mentions. Trend-grounded posts are never auto-published.
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

## Trend-grounded drafts and X policy

The queue checks X's official Trends feed when available, filters to permitted AI, technology, gaming, and sports topics, and searches for recent posts within those topics. If the Trends feed is unavailable, it falls back to recent Top-search conversations. Each trend-grounded draft includes a source link and is for **manual review and publication only**. X's automation rules prohibit automatically posting about trending topics, so the bot never auto-publishes these drafts (https://help.x.com/en/rules-and-policies/x-automation).

## Daily limits

- **20 total daily content slots**: 11 auto-published invented originals + 4 manual trend-grounded drafts + 5 manual company-tagged captions.
- **20 AI text generations per UTC day** across all three lanes: 11 invented originals, 4 trend-grounded drafts, and 5 company captions.
- Minimum **72 minutes between successful automated X posts**.
- Five company targets rotate with a four-day cooldown.
- The first successful queue run each UTC day prepares five company captions and four trend-grounded manual drafts, reserving nine content slots. Subsequent eligible queue runs publish one invented original at a time, up to eleven successful automatic posts.
- Manual X publishing cannot be detected automatically. The queue reserves those content slots when it generates the captions, even before you manually publish them.
- These are caps, not a guarantee of twenty completed posts. Search, provider, validation, or X failures can reduce actual output.

## Workflow

The Actions workflow `Post X Text Originals` runs on manual dispatch or the existing `post-next` repository-dispatch event. Each UTC day, the queue prepares five company captions in `state/company_manual_posts.md` and four trend-grounded drafts in `state/trend_manual_posts.md`, with source URLs for human review. It then publishes one invented text-only original when the cooldown and quota allow. Trend-grounded drafts are never auto-published.

The external scheduler should continue sending the existing `post-next` event at the desired cadence. The bot itself enforces the 72-minute minimum for posts that it publishes automatically.

## Required GitHub Secrets

- `X_AUTH_TOKEN`
- `X_CT0`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`
- `GEMINI_API_KEY` (fallback)
- `GEMINI_API_KEY_BACKUP` (optional fallback)

## State

`state/post_queue.json` stores daily slot and generation counters, company target rotation and cooldowns, recently used source IDs, last automated post metadata, and recent format memory (including prior post text for duplicate checks). Copy-ready manual company captions live in `state/company_manual_posts.md`; the four source-grounded manual drafts live in `state/trend_manual_posts.md`.
