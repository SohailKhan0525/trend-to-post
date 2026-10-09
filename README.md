# X Experimental Culture Bot

This repository runs an automated, text-first X account focused on AI, technology, major tech companies/products, gaming, and sports.

## What it does

- Creates standalone ORIGINAL X posts, not Quote Posts or replies.
- Uses fresh AI/tech/software/gaming/sports conversations as loose topical seeds; source posts are untrusted context and are never attached or quoted.
- Writes a short caption plus a punchy 3–5 bubble fictional chat that can land like the “permanent character update” meme format.
- Uses Cloudflare Workers AI text generation to create the concept and FLUX.1 Schnell for optional text-free illustration art.
- Renders exact chat text locally with Pillow so words are legible; every image is explicitly labeled “FICTIONAL CHAT • PARODY” and “NOT A REAL CHAT OR COMPANY STATEMENT.”
- Targets five rotating major companies per UTC day with one specific, original, manually publishable post for each, including the exact `@handle` in the caption.
- Keeps those five tagged company posts as manual copy-and-post assets; the bot never sends those company mentions to X.
- Automatically publishes the other fifteen original posts with attached meme images.
- Maintains separate creative-format memory so jokes and structures are not repeatedly recycled.
- Keeps political/current-affairs content blocked and rejects country references in generated copy, dialogue, and image prompts.
- Does not automatically like, follow, reply to, or mention other accounts.
\n## Publishing limits

- Maximum 20 daily content slots: 15 automatically published original meme posts + 5 company-tagged posts for you to publish manually.
- Maximum 20 text-generation calls per UTC day across both lanes.
- Maximum 20 image-generation attempts per UTC day across both lanes.
- Minimum 72 minutes between successful automated X posts.
- The five manually published company items count inside the 20-slot plan; they are not another five.
- Company items appear in `state/company_manual_posts.md`, with PNG files in `state/generated/company/`.
- The GitHub Actions run uploads a `company-meme-assets` artifact so you can download the copy-ready captions and images without adding binary files to the repository.
- Text generation, image generation and posting can fail independently, so these are upper bounds rather than guarantees of 20 completed posts.
- A manual X post cannot be detected or reconciled automatically by this queue, so the 72-minute guard strictly governs the fifteen automated posts, not posts you publish yourself.
\n## Reach strategy

The bot focuses on original, visual jokes built for quick comprehension and organic reactions—not fabricated claims, empty engagement bait or attempts to manipulate trends.

### Meme format

Each post is a self-contained original. The caption sets up or sharpens the bit; the attached image shows a fictional chat with an escalation and a punchline. A text-free illustration from FLUX may add visual texture, while the message text is drawn separately so it remains readable.

The template uses a generic dark-mode chat design and a prominent fictional/parody label. It does not imitate an official company screenshot or imply that an actual interaction occurred.

### Company targets

Five famous companies are selected each UTC day with a four-day cooldown. Each manual item is written specifically for that product or brand, includes the exact company handle in its caption and includes a matching meme image. Example shape:

`@BMW the M4 needs a button that deletes my group chat.`

You manually publish the caption and its matching PNG. The remaining fifteen original meme posts are automatically published without unsolicited company mentions.

Cloudflare's [FLUX.1 Schnell model](https://developers.cloudflare.com/workers-ai/models/flux-1-schnell/) supplies optional text-free artwork; the final chat layout and text are rendered locally for legibility.
\n## Security

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

Keep the external scheduler sending repository_dispatch with event type `post-next`. The single queue generates five manual company meme assets and automatically publishes the other fifteen original meme posts. Open the workflow run's `company-meme-assets` artifact to download the five captions and PNGs.

## State

state/post_queue.json stores:

- daily content-slot, text-generation and image-generation counters
- company-slot count and the five target handles used that day
- five manual company post records and their image paths
- last successful automated post metadata
- bounded topical source history
- bounded original-format memory
- recent structure fingerprints
- daily company target queue and four-day cooldown records

The research pass behind the current design used primary X automation/ranking material, GitHub Actions security guidance, current Twifork documentation, creator/posting studies, and recent LLM creativity research. It was a broad cross-section rather than a mechanically counted 200 unique websites; low-quality duplicate pages were not treated as 200 independent confirmations.
