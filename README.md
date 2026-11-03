# Dash Rattle

An openly AI-run humor column about technology, SaaS, AI assistants and models, cars, and the people who design products. The [profile kit](PROFILE.md) includes the avatar, banner, bio and pinned introduction.

## X

Every full UTC day has 50 different, date-seeded slots. The language mix is 22 English, 8 Hindi, 7 Simplified Chinese, 7 Japanese and 6 Spanish. Each half hour has one slot and two half hours have a second one. Posts are at least 15 minutes apart. Missed or rejected slots can reduce the total. No catch-up bursts. The daily boundary is 05:30 in India.

Cloudflare Workers AI writes an original post and reviews the actual text without seeing the writer’s intended meaning. It must be fluent and make sense on its own. Deterministic checks and the reviewer exclude politics, countries, national stereotypes, links, personal attacks, made-up statistics and recycled jokes. Company-life posts are fictional. New model releases and alleged company secrets are outside the brief. All generated text uses the multilingual Workers AI model `@cf/qwen/qwen3-30b-a3b-fp8` by default. `DASH_RATTLE_MODEL` can override it with another compatible model.

The free X-account path saves each approved draft in [the latest 50 drafts](state/DRAFTS.md). Click a draft to open the official X composer. Posting is a manual action in this mode. Older public posts and ledger entries are preserved.

For automatic delivery, use a write-enabled X developer app and add GitHub Actions secrets `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN` and `X_ACCESS_TOKEN_SECRET`. Enable the repository variable `X_API_PUBLISH=true` only after accepting the [official API cost](https://docs.x.com/x-api/getting-started/pricing). The mode change starts with the next available slot. Historical manual drafts are never sent automatically. Plain text goes directly to the official create-post endpoint with no URLs, media, self-replies or cards. This workflow does not use browser cookies.

An approved send is reserved in Git before X is contacted. A timeout or ambiguous response keeps the reservation and stops further sends until it is reconciled against the account. A confirmed ID is saved before the next slot. Definitive API rejections report their HTTP status. Secrets never appear in job summaries.

## LinkedIn company page

[Copy today's three posts](linkedin/latest.txt). The dated files in [linkedin/](linkedin/) are the archive. Each first workflow wake of a new Asia/Kolkata date prepares three original English posts in a single plain-text file. The launch set introduces the AI columnist. Future sets rotate through three different niches and avoid prior posts. The files are for manual publication on the company page.

## Verification

Run `python -m unittest discover -s tests -v`. Run `python -m app preview` with Cloudflare credentials to review five language samples without writing state or contacting X. The GitHub Actions publisher reports whether the four official credentials exist, which delivery mode is active, and each stored draft or confirmed X ID.
