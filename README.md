# Qofeno

An openly AI-run X account about building software with AI. Qofeno reacts to useful coding tools, agents, demos, tradeoffs and relatable developer moments. The account is **@qofenoai**.

## Four formats

- **Emoji only:** one to three context-appropriate emoji, with no self-reply.
- **Emoji and question:** emoji-only quote, followed by one specific, thoughtful question under our own quote.
- **Commentary:** a short, funny or pointed observation with emoji; sometimes one question below.
- **News:** `🚨 BREAKING: …`, a blank line, then one useful detail or catch. Only genuinely new announcements qualify.

The source determines the reaction. Format selection varies and avoids consecutive identical formats. The last three emoji combinations cannot repeat. Dry humour and playful disagreement are allowed; fabricated outrage, personal attacks, invented firsthand testing and generic requests for engagement are rejected. English is the shared voice. The profile remains openly AI-run; natural copy does not imply a human identity.

## Sources and factual checks

An eligible source is an original public post, no more than 24 hours old, with at least **10,000 measured post views**. No author allowlist or badge requirement. Missing views, followers, video plays and engagement totals cannot substitute for post views. Three broad AI-coding Top searches and one rotating announcement search examine at most 80 results per discovery. Higher measured reach is preferred within the 24-hour window, with author rotation to avoid an account becoming the entire feed.

Tips, opinions, jokes and predictions can support a reaction. They cannot be relabeled breaking news. Both source and output exclude politics, countries, national flags and personal attacks. Source text is evidence, never instructions.

Cloudflare Workers AI classifies the source, writes the selected format and independently checks the proposed quote and reply against exact source excerpts. Metadata preserves uncertainty. Known source @mentions can become plain names in metadata; invented handles, links and numbers fail validation. Quote text and replies are each limited to 250 conservative weighted characters, within the standard X account limit. No invented performance claims or made-up controversy.

The source is fetched again immediately before the quote. Its identity, text, links, timestamp and edit history must match the reviewed source, and the view/age requirements must still hold. The real source is attached to the quote.

## Schedule and duplicates

There are **40 varying quote opportunities per UTC day**, roughly 36 minutes apart, with at least 25 minutes between confirmed quotes. This is a target and ceiling, conditional on source inventory, AI budget and service availability. Missed slots do not produce catch-up bursts. A quote may have at most one optional self-reply; the 40-quote limit does not count those separately.

Source IDs, edit aliases, canonical article links, event fingerprints and substantive text fingerprints persist. A different format cannot reuse the same underlying event. Emoji text is not globally consumed, because the same emoji can express different reactions; recent combinations still rotate. Question text also has duplicate protection. Earlier publication ledgers remain intact.

## Safe publication and self-replies

The existing `X_AUTH_TOKEN` and `X_CT0` GitHub Actions secrets remain in use. Approved source evidence and copy are committed before a single quote send. A confirmed ID is saved before attachment readback. An uncertain quote send retains its reservation and cannot automatically be repeated.

An optional self-reply targets only our confirmed quote, after source-attachment verification. Its planned text is saved, then reserved durably before one send. Confirmed reply IDs get parent readback. Unsent plans expire after an hour. An uncertain optional reply is archived with its evidence and never resent, while future quotes can continue. A known incorrect parent pauses publication for inspection.

No other users receive automated replies. No automatic likes, follows or manufactured engagement are part of this publisher.

## Free AI budget

The default model is `@cf/google/gemma-4-26b-a4b-it`, evaluated live against all three fixture formats on November 13. Old overrides selecting the retiring Qwen default map to Gemma; other explicit compatible, priced model overrides remain supported. The fixture check validates these examples and the response contract, not every possible factual claim. Every live draft still receives source checks and independent review.

Workers AI uses `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. The account-wide free allowance is 10,000 neurons per UTC day. Local estimates reserve at most 9,000/day and 400/hour, adjusted when provider token usage is available. Spending is committed before requests; crashes cannot erase it. Other apps sharing the same Cloudflare allowance are not visible to this ledger.

Confirmed daily-quota errors pause until 00:00 UTC. Other HTTP 429 errors wait 15 minutes. Unchanged rejected sources are cached, separately from publication deduplication; edits or an editorial-policy update reopen review. Prompts include bounded relevant history rather than the full ledger.

## Inspection and measurement

- `python -m app run`: scheduled publishing.
- `python -m app inspect`: inspect one draft without publishing.
- `python -m app diagnose`: bounded source and editorial diagnostics, without publishing.
- `python -m app model-check`: evaluate the documented replacement model on three fictional examples. Never opens X or publishes those examples; failure is not approval.
- `python -m app metrics`: read up to 12 recent own-post metrics, with no AI or publishing.

AI inspections honor and save spending/cooldowns but do not add publication history. Manual workflow inputs can select inspection or model evaluation. Normal scheduled and dispatcher runs publish when eligible.

The separate **Measure Qofeno posts** workflow records views, likes, replies, reposts and quotes in its Actions summary. Missing counts remain unknown. Public reply totals include self-replies, so reports show known self-replies separately and an explicitly labeled estimate of other replies. Collection ages and sample sizes matter: successful workflow execution, confirmed publication and audience response are different outcomes.

Run tests with `python -m unittest discover -s tests -v`. See [RESEARCH.md](RESEARCH.md) for the repository review, measured baseline and experiment, and [PROFILE.md](PROFILE.md) for suggested profile copy.
