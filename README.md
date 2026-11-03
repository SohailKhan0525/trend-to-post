# Kit's Bookmarks — Useful Links

The current format is a short recommendation for a specific audience, with **exactly two real URLs**. Cloudflare Workers AI writes grounded labels. Code supplies the checked URLs, layout, limits and history.

```text
For vibecoders

[short label from the actual page]
[first verified URL]

[short label from the actual page]
[second verified URL]

Thank me later.
Follow for more useful links.
```

The conversation/question workflow and quote/repost workflow have been removed. Existing X posts and retired state files are kept. Nothing deletes old posts.

## Daily rotation and limits

The target and hard cap are **50 posts per UTC day**: vibecoders 6, developers 6, students 7, founders 5, business owners 6, CEOs 4, designers 4, marketers 4, creators 4 and job seekers 4. These are broad useful-resource audiences, without political or geographic targeting.

Every full day has 50 date-seeded opportunities in 28.8-minute bands, with a varying 11–17-minute offset. Planned separation is at least 22.8 minutes; actual publications must be at least 15 minutes apart. Adjacent formats target different audiences. Old posts from the rollout day count toward the 50-post cap.

`Publish Useful Links` checks every five minutes and supports the existing `post-next` dispatcher. GitHub scheduling delays, runner outages, exhausted quotas or lack of suitable fresh URLs can reduce delivery. Missed opportunities expire; there is no catch-up burst. Fifty posts requires 100 genuinely fresh URLs daily, so a finite library cannot guarantee this volume forever. The bot discovers additional pages on approved sites and skips rather than inventing or reusing links.

## How links are found and checked

`data/sources.json` is the reviewed first-party source list. It covers practical documentation, tools, tutorials, learning material and work guides. Each source has exact allowed hosts, path prefixes and suitable audiences.

Discovery reads actual links from source pages and sitemaps advertised in their `robots.txt`. It never asks a language model to invent URLs or fabricates article paths. HTTPS, hostname, DNS and redirect checks prevent requests to unapproved/private destinations. Reads, redirects, probe batches and catalog size are bounded. Challenge pages, access failures, soft 404s, insufficient content and obvious login gates are rejected. No bypass of blocked sources is attempted.

Before reservation, each selected page must return HTTP 200 with readable HTML, a title and content. Two different sites are required, with at most six published URLs from one domain per UTC day. Local excluded-topic checks scan extracted page text; Cloudflare separately reviews both excerpts and the proposed labels for topic restrictions, audience fit and factual support.

Both links are fetched again immediately before sending. A changed destination or failed review cancels that attempt before contacting X. A resource working now can change or fail later; verification is a point-in-time check, not a lifetime guarantee. The bot does not claim everything is free, personally tested or endorsed.

## Permanent non-repetition

`state/resources_queue.json` holds permanent `used_urls` and `used_fingerprints` ledgers. They are **never trimmed** when the recent post history is shortened.

The ledger records original URLs, redirects, declared canonical aliases and content fingerprints. Tracking parameters, fragments and trailing-slash variants collapse to the same identity. Clean, query-free URLs are used in posts. URL claims are committed before sending to X; uncertain outcomes can never automatically recycle those links. A definitively rejected pre-send attempt burns its claims conservatively as well.

Migration preserves the old post history, counts it toward rollout-day limits and imports known outbound URLs from retired state. An unresolved old reservation blocks migration until reconciled. The old files remain untouched. Undisclosed or unrecorded posts made outside this bot cannot be deduplicated without importing their destination URLs into the ledger.

## Strict writing rules

Code extracts factual label options from each matching page title. Cloudflare selects one option per resource using exactly two validated letter keys. There are at most three attempts. Each resulting label is 2–6 words and at most 42 characters. Local checks reject invented wording, invalid choices and swapped details before the separate semantic review. Code constructs the whole post, so the model cannot add a third link, alter URLs, change the CTA or turn it into an old question post. Automatic deployment previews check one pair; the optional all-audience preview is an editorial audit, not a dependency that can block an unrelated due post.

Candidate search checks that a page has usable labels before selecting it. A single-subject title such as `Button | shadcn/ui` is evaluated on `Button`, without requiring unrelated site-brand words in its caption. If a selected pair is rejected, preparation tries up to three different pairs without claiming their URLs or contacting X. Exhausting the bounded attempts remains a visible failure; there is no unreviewed fallback.

Both Cloudflare selection and review use [native JSON schemas](https://developers.cloudflare.com/workers-ai/features/json-mode/) and retain local validation. The default is the documented JSON-mode-compatible `@cf/meta/llama-3.1-8b-instruct` model. The review schema permits both true and false decisions. Documented JSON-mode generation failures can be retried within the attempt budget; authentication, quota and network failures are not treated as content approvals.

No politics, political parties, politicians, elections, government, countries, nationalities, national comparisons, geopolitics or wars. Country-topic restrictions apply to neutral mentions too. Software terms such as CSS borders and feature flags do not become political merely because of those words.

No fake experience, unsupported benefits, invented free plans, fabricated statistics, guarantees, copied viral posts, insults, hashtags, mentions, quote-posts or automated replies. A separate strict boolean review must approve topic, relevance and grounding. Malformed decisions and service outages do not approve a draft. AI judgment can still make mistakes; explicit rules and local checks supplement it.

Standard X account limits are respected: each URL counts as 23 weighted characters, and posts stay below a conservative 260-character weighted ceiling. No Premium subscription or new paid API service is enabled.

## Plain links without website preview cards

Every new publication explicitly sends X's `card_uri=tombstone://card` removal setting. The existing twifork client exposes this field through its historically named `poll_uri` argument; no poll, image attachment, quote or reply is created. Both verified HTTPS links remain intact and clickable.

After confirmation, the bot reads the real post back and records `website_card_status` as `absent`, `present` or `unverified`. An unexpected card pauses future publishing through `pause_reason`; readback failures cannot trigger a duplicate send. X controls final rendering, so this is checked in returned post metadata rather than assumed from the outgoing request. The change applies to future posts. Existing posts are preserved.

Labels also require specific title words and a resource type/task when the title supplies one. Generic captions such as "Social guide" are rejected when the page is specifically about social media dashboard tools. This improves reader clarity; it does not guarantee impressions, likes or replies.

## State and crash recovery

The workflow tests, prepares one due post, commits the permanent claims and reservation, rechecks both pages, sends one card-suppressed `create_tweet` request, then commits confirmation with the real X ID and preview-readback result.

A timeout, missing post ID, cancelled run or failed final push leaves a durable unresolved reservation. Later runs stop. Inspect X before reconciling; never clear a reservation just because a job failed. If published, record its real ID and timestamp and clear `pending`; if definitely unpublished, clear `pending` while keeping the URL claims retired. Commit the correction before resuming.

## Existing configuration

Existing Actions secrets are reused: `X_AUTH_TOKEN`, `X_CT0`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`. Optional `CLOUDFLARE_AI_MODEL` is a repository variable; default `@cf/meta/llama-3.1-8b-instruct`. An override must support Workers AI JSON Schema mode.

No secret values are read from GitHub or printed. The owner's existing X session client is retained; session expiry and platform restrictions still apply. Workers AI quotas and permissions also affect availability.

```sh
python -m app preview                   # Real URL checks + Cloudflare preview, no publication/state write
python -m app preview --all-categories  # One preview per audience; may take several minutes
python -m app prepare --start-now       # Reserve one due opportunity
python -m app publish                   # Requires this run's already-committed reservation
python -m unittest discover -s tests -v
```

Profile copy and assets are described in `PROFILE.md`. Profile changes require access to X's profile editor; committing profile copy does not itself update the account.
