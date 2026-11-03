# Dash Rattle

Dash Rattle publishes original, short-form humor on X. Cloudflare Workers AI writes and reviews each post. The voice is an openly AI-run columnist with an interest in cars, software, and the decisions behind them. The [profile kit](PROFILE.md) contains the bio, avatar, banner and suggested pinned introduction.

## What runs each day

Each full UTC day has 50 slots. English gets 22, Hindi 8, Simplified Chinese 7, Japanese 7 and Spanish 6. The independently shuffled topic mix covers technology (9), SaaS (8), artificial intelligence (9), model behavior (8), cars (8) and company life (8). Posts rotate among one-liners, short exchanges, fictional release notes, observations and playful roasts.

The timetable changes by date and stays stable across retries. Each half hour has a slot a few minutes after the boundary. Two separated half hours get a second slot with at least 15 minutes of spacing. The existing half-hour `post-next` dispatcher can wait for both slots in its current half hour. Native GitHub schedules and manual or push runs make one immediate check. Concurrent runs share the existing queue lock. The first rollout day counts posts already published that day toward the cap. The UTC day resets at 05:30 India time.

50 is both the daily target and maximum. A service outage, missed wake, rejected draft, expired session or unresolved send can reduce delivery. Missed windows expire. The delayed worker waits only inside its current half hour.

## Editorial checks

Each brief specifies the language, a concrete detail, a niche and a comic form. Cloudflare returns the post and a short English description of its premise. Local checks enforce standard-account length limits, script, clean text, excluded topics and duplicate detection. The independent Cloudflare reviewer must explicitly approve language, fit, safety and novelty. It compares recent premises across languages to catch recycled punchlines. Model checks reduce mistakes but cannot guarantee perfect judgment.

Politics, political parties, politicians, countries, national comparisons and geopolitics are excluded in every language. Roasts target products and decisions. Company scenes are fictional. The writer excludes personal attacks, invented insider claims, unverified news and dangerous driving advice. Posts contain no resource links or promotional sign-offs.

## Publication and recovery

`state/dash_rattle.json` is the active queue. The retired queues preserve the previous post history. Initial migration reads the former publisher's counts and last post time and stops if a previous send is unresolved. Exact wording and premise hashes stay in the ledger when the recent history rolls over.

Before sending, the runner commits the approved text, slot and hashes to GitHub. It checks the committed reservation and makes a single X request, retaining the card-suppression setting. The confirmed X ID and posting time are committed before the next slot. A timeout, missing ID or failed confirmation push can leave a pending reservation. Inspect X and reconcile that exact slot before clearing it. A failed job alone cannot establish that a post was never sent.

The workflow uses the existing X session and Cloudflare secrets. The default model is `@cf/meta/llama-3.1-8b-instruct`. A configured override must support Workers AI JSON schemas. Standard GitHub Actions runners execute this workflow and a dispatcher run can remain active while waiting for a slot.

## Verification

```sh
python -m unittest discover -s tests -v
python -m app preview
```

Preview requires Cloudflare credentials and generates one sample per language in memory. The publish workflow reports real X post IDs. The earlier bookmark workflow is retired and this repository performs no deletion of X posts.
