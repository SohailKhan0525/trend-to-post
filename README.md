# X Text-Only Original Post Bot

This repository runs an experimental X account focused on AI, technology, major products and companies, gaming, sports, and internet culture.

## What it does

- Publishes text-only standalone originals and real X Quote Posts; it does not auto-reply or publish images.
- Uses AI for every authored caption. Eleven daily originals are invented from broad topic seeds; four additional AI captions are automatically published as Quote Posts attached to recent original posts from curated official brand/company accounts.
- Automatic original posts may use ONLY these emojis: 👀 🔥 😭 ❤️‍🩹 😂 😙 🥀 🤣 🥳 🫠 😤 💀. The randomized target is 2–5 distinct emojis per post (at most one extra when it fits); repeats and emojis outside this list are rejected.
- Favors unusual formats, absurd rules, sharp observations, fictional product behaviour, and punchy one-liners over generic corporate phrasing.
- Prepares five manually publishable, text-only company-tag captions per UTC day. Their formats rotate between conditional “if” lines, “imagine” scenarios, an occasional “I'll buy” condition, choose-one prompts, feature challenges, and other product-specific jokes. Company captions may use ONLY 😂 👀 🥳 🫠; other emojis are rejected.
- Automatically publishes eleven invented text-only originals plus four text-only AI Quote Posts attached to recent posts from real brand accounts. Quote commentary contains no @mention; X displays the source post as the quote attachment.
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

## Brand-grounded Quote Posts

Four posts each UTC day are automatically published as real X Quote Posts, using `attachment_url` to attach a source post from a curated list of first-party technology, software, gaming, sports, and product brands. The bot searches recent posts directly from those official handles, verifies the author matches the account allowlist, excludes replies/retweets/quote posts and media-bearing posts, and generates an original AI comment grounded in the source. It does **not** query or target X's Trending Topics list. This distinction matters because X's automation rules prohibit automatically posting about trending topics, while they expressly allow automated Quote Posts for entertainment, informational, or novelty purposes when non-spammy and otherwise compliant: https://help.x.com/en/rules-and-policies/x-automation.

The four quotes are automatically published, not left as manual drafts. They do not add an `@mention` to the authored caption; the company appears because its original post is attached. Each source ID is tracked to prevent reuse, and the recent-format memory is used to reduce repeated jokes. Sources with images or video are skipped to preserve the text-only account requirement.
## Daily limits

- **20 total daily content slots**: 11 auto-published invented originals + 4 auto-published official-brand Quote Posts + 5 manual company-tagged captions.
- **20 AI text generations per UTC day** across all three lanes: 11 invented originals, 4 official-brand Quote Posts, and 5 company captions.
- Minimum **72 minutes between successful automated X posts**.
- Five company targets rotate with a four-day cooldown.
- The first queue run each UTC day prepares five company captions for manual publication. Eligible queue runs then publish the four official-brand Quote Posts first, one per run under the 72-minute interval guard, followed by up to eleven invented originals.
- Manual X publishing cannot be detected automatically. The queue reserves those content slots when it generates the captions, even before you manually publish them.
- These are caps, not a guarantee of twenty completed posts. Search, provider, validation, or X failures can reduce actual output.

## Workflow

The Actions workflow `Post X Text Originals` runs on manual dispatch or the existing `post-next` repository-dispatch event. Each UTC day, the queue prepares five company captions in `state/company_manual_posts.md` for manual publication. It automatically publishes four AI Quote Posts attached to fresh text-only posts from official company accounts, then publishes invented text-only originals when the cooldown and quota allow. Published quote-post metadata is saved in `state/post_queue.json`.

The external scheduler should continue sending the existing `post-next` event at the desired cadence. The bot itself enforces the 72-minute minimum for posts that it publishes automatically.

## Required GitHub Secrets

- `X_AUTH_TOKEN`
- `X_CT0`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`
- `GEMINI_API_KEY` (fallback)
- `GEMINI_API_KEY_BACKUP` (optional fallback)

## State

`state/post_queue.json` stores daily slot and generation counters, official-brand source IDs, company target rotation and cooldowns, last automated post metadata, and recent format memory (including prior post text for duplicate checks). Copy-ready manual company captions live in `state/company_manual_posts.md`. The legacy `state/trend_manual_posts.md` file is no longer used for the quote-post lane.
