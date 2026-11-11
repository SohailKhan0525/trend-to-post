# Qofeno

Qofeno covers **practical AI coding tools for solo builders**: coding agents, editors, model integrations, MCP and developer workflows. Posts can come from any public account. The [profile copy](PROFILE.md) uses **Qofeno** and **@qofenoai**. See the [research, repository review and measured baseline](RESEARCH.md).

## What qualifies

The source must be an original public post, no more than 24 hours old, with a measured post-view count of at least 100,000. There is no account allowlist or badge requirement. Missing counts, video-play counts, follower counts and engagement totals cannot satisfy this threshold. Discovery uses three bounded Top searches covering coding assistants, coding-related models and developer infrastructure, plus one rotating search in those subjects. Per-query diagnostics show the number examined, excluded and deduplicated, with reasons for exclusions. High views establish source reach, not the truth of a claim or reach for our quote.

Cloudflare Workers AI must identify a concrete new announcement or news report supported by the source. Third-party reporting must be explicitly attributed to the source account; the quote must not present it as independently verified or remove uncertainty. Teasers, retrospectively described launches, tips, rumours and generic promotional posts are rejected. The writer then drafts a short headline, a blank line, and a useful practical observation or specific question. An independent review checks the exact source against every claim and checks prior events. English is the initial shared voice. All parts of the source, available linked titles, and the quote are checked for politics and countries.

The writing contract follows the practical advice in [Maran's November 7 guide](https://x.com/TheMaran/status/2119073139082846389): lead with a named product and concrete change, add a different useful fact, and include a relevant limitation when the source establishes one. The application renders the headline and body with exactly one blank line. The reviewer must identify exact source evidence for the added value. Repeated facts, vague product references, artificial suspense, invented firsthand testing, and requests for likes or follows are rejected. A genuine question is optional; a useful sourced detail takes priority over a forced question.

The source is fetched again immediately before publication. Its measured count must still be at least 100,000 and its identity, text, links, timestamp and edit history must match the reviewed version. The quote attaches the actual source post. A source attachment is an essential part of a quote.

## Schedule and duplicates

There are 40 varying daily opportunities, spread roughly 36 minutes apart, with at least 25 minutes between confirmed sends. The UTC day resets at 05:30 India time. A delayed run expires previous opportunities. This is a target and cap, conditional on sufficient fresh, distinct qualifying news and working services. A failed source check can retry after ten minutes while the opportunity remains current. The filter is never reduced to fill a quota.

The ledger retains source IDs, edit aliases, canonical article links, normalized event fingerprints and commentary fingerprints indefinitely. A reworded quote about the same release is a duplicate. Recent event summaries also go to the independent reviewer. The source history from the retired quote feed is imported. The old post ledgers remain as publication history.

## Publication and recovery

The existing GitHub Actions secrets `X_AUTH_TOKEN` and `X_CT0` authorize the session client. `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` authorize Workers AI. `QOFENO_AI_MODEL` selects a compatible model and the previous `DASH_RATTLE_MODEL` override remains supported. The default is `@cf/qwen/qwen3-30b-a3b-fp8`.

The workflow shares the prior publishing concurrency group. Approved source evidence and copy are committed before one send. A confirmed X ID is committed before attachment readback. A timeout or unknown send retains its reservation, preventing a retry that could create a duplicate. A proven-unsent attempt releases only its own duplicate claims; the pinned client's initial rejected-session error also requires a session check next time. An unavailable attachment keeps verification pending, blocking new sends until readback succeeds. A confirmed incorrect attachment pauses delivery for inspection.

`python -m app inspect` checks sources and one sample without publishing. `python -m app diagnose` logs bounded public-source evidence and editorial decisions for up to four candidates without sending. Both honor and persist AI spending/cooldowns but leave publication history unchanged. Code pushes, scheduled and dispatcher wakes run `python -m app run`, which processes the current opportunity with the same spacing and duplicate checks. Manual workflow runs have an inspect-only option. Tests run with `python -m unittest discover -s tests -v`.

## Free AI budget and measurement

Cloudflare's daily free allowance is account-wide: 10,000 neurons, reset at 00:00 UTC. A confirmed daily-quota error is saved as a pause until that reset; subsequent wakes make no AI or discovery calls while paused. Other HTTP 429 responses wait 15 minutes. Local estimated reservations are capped at 9,000 neurons/day and 400/hour to spread research. Input UTF-8 size and maximum output establish a conservative estimate; available provider token counts adjust it. Spending is committed before each request so crashes cannot erase it. This estimate is not Cloudflare billing and cannot see other apps sharing the allowance. Unknown model costs stop publication until configured.

Unchanged semantically rejected sources are cached until source expiry; failed drafts cool down for six hours. Source edits or an editorial-policy version change reopen review. These records never consume publication duplicate claims. Prompts send at most 20 relevant/recent event summaries and four recent quotes, while permanent fingerprints and the local 160-post comparison remain.

`python -m app metrics` makes at most 12 read-only X lookups for recent own posts aged at least six hours. The separate daily **Measure Qofeno posts** workflow reports available views, likes, replies, reposts and quotes in its Actions summary. Missing counts remain unknown. It needs no AI and cannot publish. Ages vary; use the reported ages and sample sizes rather than treating totals as an equal-age comparison.

About one-third of event fingerprints prefer a specific practical question; the rest prefer a useful sourced detail. A detail remains the fallback when no honest question fits. History records the editorial version and actual body style. This is a content experiment, not a claim of increased reach.

The current Qwen model is scheduled to retire on November 20, 2026. The [research notes](RESEARCH.md#cloudflare-limits-and-maintenance) list documented replacements to evaluate after today's account-wide quota reset; no untested replacement is silently enabled.

## Editorial basis

X recommends concise, conversational copy and describes quotes as a way to add a comment. Its authenticity policy rejects repetitive, bulk or minimally useful content. Qofeno adds a concrete implication or question to a source, checks duplicate events, and records skipped opportunities. Selecting a popular source cannot guarantee reach for its quote.

The numerical changes in Maran's guide match the published ranking defaults between commits `b508926` and `021afa4` in `xai-org/x-algorithm`. Those coefficients multiply personalized predictions, not raw engagement counts. They do not establish a posting-frequency rule or guarantee impressions, and Qofeno does not use them as an engagement score.

- [X posting and engagement guidance](https://business.x.com/en/basics/get-your-business-started-with-x)
- [How X counts post views](https://help.x.com/en/using-x/view-counts)
- [Authenticity and content spam](https://help.x.com/en/rules-and-policies/authenticity)
- [Published ranking defaults](https://github.com/xai-org/x-algorithm/blob/021afa4/home-mixer/params/param.rs)
- [How the ranking coefficients are applied](https://github.com/xai-org/x-algorithm/blob/main/xai-value-model/scoring.rs)
