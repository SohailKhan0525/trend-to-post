# Dash Rattle

An anonymous AI columnist on X. Cloudflare Workers AI writes jokes about technology, SaaS, assistants and AI models, cars, and the decisions behind products. The [X profile kit](PROFILE.md) includes the avatar, banner, bio and pinned introduction.

## Publishing

GitHub Actions publishes eligible text posts with the repository's existing `X_AUTH_TOKEN` and `X_CT0` secrets. `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` provide Workers AI access. The default model is `@cf/qwen/qwen3-30b-a3b-fp8`. Set `DASH_RATTLE_MODEL` to use a compatible alternative.

A full UTC day has 50 slots at varying times. The normal plan includes 22 English, 8 Hindi, 7 Simplified Chinese, 7 Japanese and 6 Spanish slots. The half-hour dispatcher and five-minute scheduled checks share a lock. Confirmed sends must be at least 15 minutes apart. A missed wake, provider outage or X rejection can reduce the day's total. The day resets at 05:30 India time.

Every post is reserved in Git before it is sent. A confirmed post ID is recorded before checking whether X attached a website card. An ambiguous result leaves the reservation in place for reconciliation. Only confirmed sends count toward the publishing limit. Historical draft wording and premises are retained for duplicate detection.

## Writing rules

Use short, natural lines with a concrete setup and a punchline. Roast products, processes and the AI itself. Skip politics, politicians, countries, national stereotypes, links and engagement requests. Company scenes are fictional. A second model call reads the actual text and must approve its language, subject, safety and novelty. A proofread reserve can be used only once per language and topic. If it has already been used, the writer can try one different language for that slot. If editorial checks still fail, nothing is sent.

## Verification

Run `python -m unittest discover -s tests -v`. The optional `python -m app preview` command uses Cloudflare to print samples without contacting X. The publish workflow records the status URL for each confirmed post.
