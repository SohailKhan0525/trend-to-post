# Trend to Post

Ten daily opportunities for original AI/technology quote posts, written by Cloudflare Workers AI and published with the existing X session client. Focus: ChatGPT, Claude, OpenCode, AI agents and developer tools.

## What runs

- One quote per 144-minute UTC window, with a hard ten-post daily cap and 90-minute minimum separation. Windows begin at 00:00, 02:24, 04:48, 07:12, 09:36, 12:00, 14:24, 16:48, 19:12 and 21:36 UTC. Scheduled checks run at :17 and :47; actual posting times depend on GitHub scheduling delays.
- Searches recent English AI/tech conversations, ranks observed engagement against age, avoids the last three quoted authors, and rejects sources older than 24 hours. This does not use X's Trending Topics feed or assert that a source is verified news.
- Generates only through Cloudflare; default model `@cf/meta/llama-3.1-8b-instruct-fp8`. No Gemini fallback, images per post, repo calendar, fixed community posts, Grok bait, or automated replies.
- Each post attaches the actual source URL as a quote. Commentary targets 40–240 characters; a conservative weighted ceiling of 260 leaves margin below 280. No Premium features.
- Readable two-paragraph format: a short hook, a blank line, then a specific takeaway. Occasional expressive profanity is allowed about technology or a situation, never directed at people. Openers vary against recent posts.
- Structured hook/body generation with deterministic paragraph formatting; up to four source candidates per eligible run. Rejection and decline counts appear in workflow summaries.
- Strict JSON and content validation: no URLs/mentions/hashtags, unsupported numbers, invisible control characters, copied source passages or near-duplicate comments. The prompt treats source text as untrusted and forbids invented claims. These checks reduce errors; they cannot prove every generated factual statement.
- Skips when the source or draft is unsuitable. Ten is a target and maximum, not a promise to publish weak filler. Missed windows are not backfilled in a burst.

## Secrets and operation

Existing GitHub Actions secrets are reused: `X_AUTH_TOKEN`, `X_CT0`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`. Their values are never read from GitHub or printed. Cloudflare token needs Workers AI access. Optional repository variable `CLOUDFLARE_AI_MODEL` overrides the model.

GitHub Actions needs contents-write permission. Run **Publish AI and Technology Quotes** manually or wait for its schedule. Changes to application code on main also trigger a run. External `post-next` events remain supported. Disable this workflow in Actions to stop publication.

Cloudflare has a shared 10,000-neuron daily free allocation. Quota exhaustion stops generation; this project does not enable paid billing or auto-recharge. A free X profile and official X API access are separate products. The existing cookie client is retained at the owner's explicit request; it is unofficial and can break or trigger restrictions. X discourages non-API automation. No proxy rotation, CAPTCHA bypass, rate-limit evasion or automated engagement is implemented.

## Crash and duplicate protection

`prepare` writes a reservation. The workflow commits and pushes it **before** `publish` contacts X. After X returns a post ID, confirmed state is committed. A failed reservation push prevents publication. A timeout, missing ID, canceled job or failed final push leaves the durable reservation unresolved; later runs stop rather than retrying a possibly published post. This intentionally prioritizes avoiding duplicates over availability.

To recover: disable the workflow, inspect your X profile and the reserved source/text, then edit `state/post_queue.json`. If published, append its real ID, text, author, source_id, slot and posted_at to history, add source_id to used_source_ids and update last_posted_at. Only then set pending to null. If definitely not published, clear pending; never clear it just because the job failed. Commit the correction and re-enable the workflow. Git history retains retired content and the previous implementation.

## Validation

```sh
python -m pip install -r requirements.txt
python -m compileall -q app
python -m unittest discover -s tests -v
```

Tests cover source filtering, short-post validation, duplicates, Cloudflare request shape, slot limits, rate-limit stop behavior and uncertain-publication recovery. Mocked tests do not prove current cookies, Cloudflare permissions, or live X delivery; verify the actual post URL in the Actions summary.

See [PROFILE.md](PROFILE.md) for identity and audience-building guidance.

## Primary references checked 2026-10-27

- [X automation rules](https://help.x.com/en/rules-and-policies/x-automation): quote automation, duplicate/spam restrictions, unofficial automation risks, and AI reply-bot approval requirements.
- [X API pricing](https://docs.x.com/x-api/getting-started/pricing): API credits are separate from a free X profile.
- [Cloudflare REST API](https://developers.cloudflare.com/workers-ai/get-started/rest-api/).
- [Cloudflare model parameters](https://developers.cloudflare.com/workers-ai/models/llama-3.1-8b-instruct-fp8/).
- [Cloudflare pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/).


## Repetition and grounding controls

The creative writer sees only the current source, a rotating editorial angle, and whether an occasional swear is permitted. Earlier posts are never supplied as creative examples or factual evidence. Swearing is optional in one out of every three publication positions; concrete hooks are the default.

Before reserving a post, the bot rejects repeated hooks against the last 20 posts, punctuation/elongation or company-name variants, recycled bodies against the last 100 posts, and recognizable repeated stories within 48 hours. Different source accounts do not reset the story cooldown. Source text is now retained with successful posts so future checks can compare the underlying event.

Two separate Cloudflare reviews then check source-grounding and story/punchline novelty. The factual reviewer sees only the source and draft; the novelty reviewer treats previous posts as a blacklist, never as facts. Failed or malformed verdicts block publication. These model-based reviews reduce semantic errors but are not a proof of factual accuracy. Existing duplicate posts are left unchanged.

Code pushes run `python -m app preview` to exercise live generation and quality gates without publishing or changing queue state. Normal scheduling still observes the daily cap and duplicate-safe reservation flow. Regression fixtures contain actual previously published repetitive text; tests verify those patterns are rejected.
