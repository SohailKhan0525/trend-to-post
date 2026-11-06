# Qofeno

Qofeno quotes fresh announcements and clearly attributed news about AI, models, technology, SaaS, developer tools and cars. Posts can come from any public account. The [profile copy](PROFILE.md) uses **Qofeno** and **@qofenoai**.

## What qualifies

The source must be an original public post, no more than 24 hours old, with a measured post-view count of at least 100,000. There is no account allowlist or badge requirement. Missing counts, video-play counts, follower counts and engagement totals cannot satisfy this threshold. Discovery uses bounded topic searches across Top and Latest results. Its diagnostics show the number examined, excluded and deduplicated, with reasons for exclusions. High views establish reach, not the truth of a claim.

Cloudflare Workers AI must identify a concrete new announcement or news report supported by the source. Third-party reporting must be explicitly attributed to the source account; the quote must not present it as independently verified or remove uncertainty. Teasers, retrospectively described launches, tips, rumours and generic promotional posts are rejected. The writer then drafts a short headline, a blank line, and a useful practical observation or specific question. An independent review checks the exact source against every claim and checks prior events. English is the initial shared voice. All parts of the source, available linked titles, and the quote are checked for politics and countries.

The source is fetched again immediately before publication. Its measured count must still be at least 100,000 and its identity, text, links, timestamp and edit history must match the reviewed version. The quote attaches the actual source post. A source attachment is an essential part of a quote.

## Schedule and duplicates

There are 40 varying daily opportunities, spread roughly 36 minutes apart, with at least 25 minutes between confirmed sends. The UTC day resets at 05:30 India time. A delayed run expires previous opportunities. This is a target and cap, conditional on sufficient fresh, distinct qualifying news and working services. A failed source check can retry after ten minutes while the opportunity remains current. The filter is never reduced to fill a quota.

The ledger retains source IDs, edit aliases, canonical article links, normalized event fingerprints and commentary fingerprints indefinitely. A reworded quote about the same release is a duplicate. Recent event summaries also go to the independent reviewer. The source history from the retired quote feed is imported. The old post ledgers remain as publication history.

## Publication and recovery

The existing GitHub Actions secrets `X_AUTH_TOKEN` and `X_CT0` authorize the session client. `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` authorize Workers AI. `QOFENO_AI_MODEL` selects a compatible model and the previous `DASH_RATTLE_MODEL` override remains supported. The default is `@cf/qwen/qwen3-30b-a3b-fp8`.

The workflow shares the prior publishing concurrency group. Approved source evidence and copy are committed before one send. A confirmed X ID is committed before attachment readback. A timeout or unknown send retains its reservation, preventing a retry that could create a duplicate. A proven-unsent attempt releases only its own duplicate claims; the pinned client's initial rejected-session error also requires a session check next time. An unavailable attachment keeps verification pending, blocking new sends until readback succeeds. A confirmed incorrect attachment pauses delivery for inspection.

`python -m app inspect` checks sources and one sample without publishing. Code pushes, scheduled and dispatcher wakes process the current opportunity with the same spacing and duplicate checks. `python -m app run` processes that opportunity. Manual workflow runs have an inspect-only option. Tests run with `python -m unittest discover -s tests -v`.

## Editorial basis

X recommends concise, conversational copy and describes quotes as a way to add a comment. Its authenticity policy rejects repetitive, bulk or minimally useful content. Qofeno adds a concrete implication or question to a source, checks duplicate events, and records skipped opportunities. Selecting a popular source cannot guarantee reach for its quote.

- [X posting and engagement guidance](https://business.x.com/en/basics/get-your-business-started-with-x)
- [How X counts post views](https://help.x.com/en/using-x/view-counts)
- [Authenticity and content spam](https://help.x.com/en/rules-and-policies/authenticity)
