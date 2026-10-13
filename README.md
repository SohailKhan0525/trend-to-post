# AI/Technology X Trends Quote-Post Bot

This repository runs an experimental X account focused on AI and technology: AI labs and models, model releases, developer tools, software, chips and GPUs, cloud infrastructure, robotics, programming, and related digital products.

## Daily publishing plan

The queue has **20 main-post slots per UTC day**, split into two formats:

- **12 AI/technology X Trends quote-posts** — each is anchored to a real topic from X Explore/Trends, with original commentary attached to a recent relevant post. No unrelated trend or generic keyword-search fallback is allowed.
- **8 developer-community originals** — short, two-line prompts such as “Devs be honest…” followed by a relatable hypothetical: what you would build if ChatGPT were free forever, what you would ask a developer if you sat beside them, or a founder/developer trade-off. The post uses a real, reusable photo discovered through Wikimedia Commons, with license checks and attribution where required.

The Trends portion can publish fewer than 12 if X does not surface enough eligible AI/technology trends. The eight original prompts are a separate lane, so an empty Trends feed no longer means the whole queue has nothing to publish.

**Mention cap:** no more than two original posts with an @mention in any rolling 48-hour period. Each tagged post can mention only the verified account pictured in its attached photo. The state file records timestamps so the cap survives workflow runs.

## Images and licenses

## Emoji and caption rules

Automated authored captions and quote-post comments may use **only** these emojis. Quote-post comments are standalone jokes: they must not copy the source's words or put phrases in quotation marks.

👀 🔥 😭 ❤️‍🩹 😂 😙 🥀 🤣 🥳 🫠 😤 💀

The number of emojis is randomized per caption (normally 2–5, with at most one extra when it fits). Repeated emoji tokens and emojis outside the approved palette are rejected. The prompt generator and validators also reject @mentions, hashtags, URLs, country references, blocked political/current-affairs content, and recent-format duplicates.

The AI/technology Quote Post lane is restricted to AI, models, AI labs, software, developer tools, coding, computing hardware, chips, cloud, apps, robotics, data, and digital product behavior. It only uses topics found in X's live Trends feed and never silently substitutes direct keyword searches.

## Queue and workflow behavior

The publishing workflow is .github/workflows/post-queue.yml. It runs on a 30-minute GitHub Actions schedule, manual dispatch, or the existing post-next repository-dispatch event. The schedule is a fallback if the external dispatcher stops; concurrency and the post interval guard prevent overlapping events from publishing duplicates. Every run checks credentials, compiles the Python application, runs the full unit-test suite, and only then attempts one new main post.

The queue rotates between 12 AI/technology X Trends quote-posts and 8 developer-community original prompts. It publishes at most one new main post per dispatch and enforces a **58-minute minimum between successful main posts**. The workflow runs every 30 minutes; interval guards avoid duplicate posts from overlapping/manual dispatches. Native trend context and grouped-topic metadata classify relevant trend topics. Developer prompts use existing, licensed Commons portraits rather than scraping Google Images or inventing photorealistic images of real people.

The queue tracks daily counters, previous source IDs, format memory, and last-post metadata in the post_queue state JSON. Up to 40 generation attempts are allowed per UTC day so transient provider/source failures do not immediately consume the daily plan. Twenty posts is the daily cap and target, not a guarantee: X needs to surface enough relevant trends and eligible source posts, and authentication/provider restrictions can reduce publishing. When no relevant X trend exists, the quote lane skips and the developer-original lane may still publish. No more than two original posts with @mentions are allowed in any rolling 48-hour period.

The Validate Bot workflow checks Python syntax and unit tests on pushes/pull requests. Changes should not be deployed until that workflow passes.

## Required GitHub Secrets

- X_AUTH_TOKEN
- X_CT0
- CLOUDFLARE_ACCOUNT_ID
- CLOUDFLARE_API_TOKEN
- GEMINI_API_KEY (fallback)
- GEMINI_API_KEY_BACKUP (optional fallback)

## Important platform note

This repository currently publishes through Twikit using an authenticated X session. X can restrict automated or scripted website interactions, and X's automation policy may require API-based automation for a compliant production bot. Review current X rules and use an approved API-based publishing integration where required: https://help.x.com/en/rules-and-policies/x-automation.
