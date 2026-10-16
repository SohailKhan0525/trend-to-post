# Trend to Post

An automated X publishing queue for practical developer prompt tips and text-free comparison memes. The repository is designed to publish one main post per eligible run, track daily quotas in Git, and make generation/publishing failures visible in GitHub Actions.

## Active daily plan

- **10 builder prompt tips** — practical, reusable instructions for building and improving websites, apps, UI/UX, and software workflows. Captions use the `DID YOU KNOW?` format and should be useful without hashtags, URLs, or mentions.
- **10 comparison memes** — two-choice headlines (for example, “Cursor or VS Code?”) with the caption format maintained by the queue. Each comparison receives an original, text-free split illustration generated locally.
- **No X Trends quote-posts** — legacy quote-post code and old state fields may remain for compatibility, but those lanes have a daily target of zero.

Twenty posts is a daily target and hard cap, not a guarantee. The queue enforces a minimum interval between successful main posts and stops at the daily cap. State is stored in `state/post_queue.json` and committed back to `main` by the posting workflow.

## Image generation

Comparison images are created locally in `app/generated_images.py`:

1. Parse the two choices from the comparison headline.
2. Select visual motifs based on the words in each choice.
3. Build an SVG illustration without visible text.
4. Render to a 1200 × 900 PNG with CairoSVG.
5. Normalize and optimize the PNG with Pillow before uploading it.

This renderer does not require a remote image-generation service or image-service credentials. It creates stylized vector illustrations, not photorealistic or AI-generated photographic imagery. The post queue validates the PNG and uses the matching upload MIME type.

## AI text generation

Gemini is the fallback text-generation provider. Cloudflare Workers AI can be used as an optional first-pass candidate provider when credentials are configured. Missing Cloudflare credentials must not block Gemini fallback. Set `GEMINI_API_KEY`; `GEMINI_API_KEY_BACKUP` is optional.

## Workflows

- `.github/workflows/post-queue.yml` runs every 30 minutes, on manual dispatch, or on the `post-next` repository-dispatch event. It installs dependencies, validates syntax, runs tests, attempts one queue item, and commits state changes.
- `.github/workflows/test.yml` runs compile checks and the full unit-test suite on pushes, pull requests, and manual dispatch.

A successful validation workflow proves the code and tests passed; it does **not** prove an X post was published. Confirm publishing from the posting workflow's logs and its `published` output.

## Setup

Install Python 3.12 and dependencies:

```bash
python -m pip install -r requirements.txt
python -m compileall -q app
python -m unittest discover -s tests -p "test_*.py"
```

Configure these GitHub Actions secrets:

- `X_AUTH_TOKEN` — required X session authentication cookie.
- `X_CT0` — required X CSRF cookie.
- `GEMINI_API_KEY` — required when Gemini fallback is needed.
- `GEMINI_API_KEY_BACKUP` — optional second Gemini key.
- `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` — optional; used for first-pass text candidates only.

The X cookies are session credentials and may expire. Keep all credentials in GitHub Secrets; never commit them to the repository or print their values in logs.

## Reliability and safety notes

- The queue enforces the 20-post daily cap and the 10/10 lane targets.
- Failed generation should be reported as a failure, not treated as an empty queue.
- A post whose X publishing outcome is uncertain must not be blindly retried, because that could create a duplicate.
- State commits use retry/rebase logic to handle concurrent updates.
- Wikimedia/person-photo and retired quote-post code may remain for backward compatibility; they are not the active comparison-image path.
- This project uses Twikit with an authenticated X session. Review [X's automation rules](https://help.x.com/en/rules-and-policies/x-automation) and use an approved publishing method consistent with current platform requirements.
