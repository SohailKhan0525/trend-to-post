# AI/Technology X Trends Quote-Post Bot

This repository runs an experimental X account focused on AI and technology: AI labs and models, model releases, developer tools, software, chips and GPUs, cloud infrastructure, robotics, programming, and related digital products.

## Daily publishing plan

The active queue targets **up to 20 AI/technology X Trends quote posts per UTC day**. Each post must be based on an actual topic returned by X's Explore/Trends views or X's location-based Trends lists. The bot finds a recent, original post related to that trend and attaches an original AI-written meme-style comment.

The queue does **not** substitute ordinary keyword-search results, invented topics, sports, entertainment, politics, or cultural trends when X has no eligible AI/technology trend. In that case, it skips the run rather than publishing something unrelated. This strict source rule means 20 posts is a daily target/cap, not a guarantee: the available X Trends feed must contain enough eligible topics and source posts.

Only the `x_trends_quote` lane is active. The legacy photo-prompt and direct AI/technology keyword-search lanes remain disabled; their code/state fields are retained only for compatibility.

## Disabled legacy photo lane

The photo-prompt lane is currently disabled and cannot publish through the active queue. Its implementation uses the Wikimedia Commons API and downloads only existing image files with machine-readable reuse terms. It accepts public-domain/CC0 images and attribution/share-alike Creative Commons licenses, while rejecting non-commercial, no-derivatives, fair-use, all-rights-reserved, and missing-license candidates. It also checks that the file metadata matches the chosen subject and accepts only common raster image formats.

For a license that requires attribution, the bot posts a self-reply with creator credit, the license, and the original Commons file page. If that reply fails after the photo post publishes, the main post is still recorded and the attribution failure is logged in the post_queue state JSON; check it before manually retrying the credit.

The candidate catalog prioritizes public-facing people across AI/technology leadership, research, coding tools, and product teams—not just CEOs. The active X account is verified before searching for a licensed photograph, and the profile-name check helps prevent mismatching a real person to a recycled or unrelated handle. Only photos with a reusable license are candidates. If no suitable image is found, the bot skips that lane attempt and tries another eligible lane instead of downloading an arbitrary Google Images result. The bot never scrapes Google Images and never assumes that an image is reusable merely because it is publicly visible.

## Emoji and caption rules

Automated authored captions and quote-post comments may use **only** these emojis. Quote-post comments are standalone jokes: they must not copy the source's words or put phrases in quotation marks.

👀 🔥 😭 ❤️‍🩹 😂 😙 🥀 🤣 🥳 🫠 😤 💀

The number of emojis is randomized per caption (normally 2–5, with at most one extra when it fits). Repeated emoji tokens and emojis outside the approved palette are rejected. The prompt generator and validators also reject @mentions, hashtags, URLs, country references, blocked political/current-affairs content, and recent-format duplicates.

The AI/technology Quote Post lane is restricted to AI, models, AI labs, software, developer tools, coding, computing hardware, chips, cloud, apps, robotics, data, and digital product behavior. It only uses topics found in X's live Trends feed and never silently substitutes direct keyword searches.

## Queue and workflow behavior

The publishing workflow is .github/workflows/post-queue.yml. It runs on a 30-minute GitHub Actions schedule, manual dispatch, or the existing post-next repository-dispatch event. The schedule is a fallback if the external dispatcher stops; concurrency and the post interval guard prevent overlapping events from publishing duplicates. Every run checks credentials, compiles the Python application, runs the full unit-test suite, and only then attempts one new main post.

The active queue has one publishing lane: actual AI/technology X Trends quote posts. It publishes at most one new main post per dispatch and enforces a **58-minute minimum between successful main posts**. The workflow runs every 30 minutes; interval guards avoid duplicate posts from overlapping/manual dispatches. Native trend context and grouped-topic metadata are used to classify and search relevant trend topics. Regional Trends responses are read from the mapping shape returned by Twifork. These sources remain X Trends—not ordinary keyword-search fallback.

The queue tracks daily counters, previous source IDs, format memory, and last-post metadata in the post_queue state JSON. Up to 40 generation attempts are allowed per UTC day so transient provider/source failures do not immediately consume the daily plan. Twenty posts is a cap and target, not a guarantee: X needs to surface enough relevant trends and eligible source posts, and authentication/provider restrictions can also reduce publishing. When no relevant X trend exists, the lane skips rather than posting unrelated content.

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
