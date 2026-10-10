# X AI, Tech, Photo-Prompt and Quote-Post Bot

This repository runs an experimental X account focused on AI, technology, major products and companies, gaming, sports culture, and internet culture.

## Daily publishing plan

The bot has **20 automatically published main-post slots per UTC day**:

- **4 official-company Quote Posts** — the existing lane is preserved. It attaches a recent eligible text-only post from a curated first-party company account and adds an AI-written comment.
- **8 real-person photo prompts** — the bot finds and downloads an existing, real photograph of a public-facing person, checks the source/license metadata, writes a funny prompt in the style of “You are sitting next to this person and get three words… what do you say?”, and attaches that real photo. It does **not** generate images.
- **8 AI/technology Quote Posts** — the bot finds recent text-only X posts about AI and technology using direct keyword searches (not X's Trends feed), then publishes a humorous AI-written text comment as a real Quote Post. No AI-generated meme images are used.

The five old manually prepared company captions and the eleven invented standalone text originals are no longer part of the active daily queue. Legacy state fields remain only for backward compatibility; those lanes are not selected for new posts.

## Real-photo sourcing and licenses

The person-photo lane uses the Wikimedia Commons API and downloads only existing image files with machine-readable reuse terms. It accepts public-domain/CC0 images and attribution/share-alike Creative Commons licenses, while rejecting non-commercial, no-derivatives, fair-use, all-rights-reserved, and missing-license candidates. It also checks that the file metadata matches the chosen subject and accepts only common raster image formats.

For a license that requires attribution, the bot posts a self-reply with creator credit, the license, and the original Commons file page. If that reply fails after the photo post publishes, the main post is still recorded and the attribution failure is logged in the post_queue state JSON; check it before manually retrying the credit.

The search catalog rotates public-facing people from AI/technology, gaming, sports, and entertainment. Only photos with a reusable license are candidates. If no suitable image is found, the bot skips that lane attempt and tries another eligible lane instead of downloading an arbitrary Google Images result. The bot never scrapes Google Images and never assumes that an image is reusable merely because it is publicly visible.

## Emoji and caption rules

Automated authored captions and quote-post comments may use **only** these emojis:

👀 🔥 😭 ❤️‍🩹 😂 😙 🥀 🤣 🥳 🫠 😤 💀

The number of emojis is randomized per caption (normally 2–5, with at most one extra when it fits). Repeated emoji tokens and emojis outside the approved palette are rejected. The prompt generator and validators also reject @mentions, hashtags, URLs, country references, blocked political/current-affairs content, and recent-format duplicates.

The AI/technology Quote Post lane is restricted to AI, models, AI labs, software, developer tools, coding, computing hardware, chips, cloud, apps, robotics, data, and digital product behavior. It intentionally does not quote unrelated sports/entertainment content. It uses direct latest-post keyword searches and never calls X's Trending Topics endpoint.

## Queue and workflow behavior

The publishing workflow is .github/workflows/post-queue.yml. It runs on manual dispatch or the existing post-next repository-dispatch event. Every run checks credentials, compiles the Python application, runs the full unit-test suite, and only then attempts one new main post.

The queue rotates between the three lanes so a temporary image search, source search, or generation failure in one lane does not block all other content types. It publishes at most one new main post per dispatch and enforces a **72-minute minimum between successful main posts**. A required photo-attribution reply is sent immediately after its photo post and does not consume another main-post slot.

The queue tracks daily counters, prior source IDs, photo file titles, recently used person names, format memory, attribution status, and last-post metadata in the post_queue state JSON. Up to 40 generation attempts are allowed per UTC day so transient provider/source failures do not immediately consume the entire 20-post plan. These are caps, not guarantees: provider errors, unavailable eligible content, X restrictions, or expired authentication can reduce the number published.

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
