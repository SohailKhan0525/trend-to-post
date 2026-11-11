# Qofeno research and experiment — November 11, 2026

## Finding and niche

The failed runs on November 11 report Cloudflare HTTP 429: the daily free allocation of 10,000 neurons was exhausted. Searches still found eligible sources. This is an AI budget failure, not evidence that the saved X cookies stopped working. Repeatedly classifying rejected candidates wastes the same allowance needed to publish.

**Audience hypothesis: people building software with AI coding tools.** Cover usable changes to coding agents, editors, model integrations, MCP and developer workflows. Each quote should answer “what changed, and what can a builder do with it?” Cost, access limits and practical tradeoffs matter when the source supplies them. Random cars, broad company gossip and unsupported model-launch predictions dilute that promise. This niche is a testable choice, not a proven winner for this account.

## What the algorithm research supports

[X's official repository](https://github.com/xai-org/x-algorithm/blob/main/README.md) describes personalized retrieval and ranking, including predicted replies, shares, reading time and negative feedback. [The scoring code](https://github.com/xai-org/x-algorithm/blob/main/xai-value-model/scoring.rs) multiplies predictions by weights; it does **not** award a fixed number of points for each actual like or reply. It also applies repeated-author diversity adjustments. More posts are therefore not a demonstrated reach multiplier.

The source's 100,000 views do not transfer to Qofeno's quote. That threshold remains a source-selection rule. Forty daily opportunities remain a cap and target, conditional on useful distinct sources, budget and successful publication. They are not a promise of forty sends or any level of engagement. Public view counts are also [not unique people](https://help.x.com/en/using-x/view-counts).

## Repositories inspected

| Repository | Useful idea | Why it is not an impressions shortcut |
| --- | --- | --- |
| [xai-org/x-algorithm](https://github.com/xai-org/x-algorithm) | Understand personalized ranking, negative feedback and author diversity. | Published defaults are not a universal formula; some configuration and anti-abuse details are not public. |
| [devploit/Xplore](https://github.com/devploit/Xplore) | Its [metric curves](https://github.com/devploit/Xplore/blob/main/src/analytics/curve.ts) align snapshots to post age. Its analytics distinguish insufficient exposure and partial data. | Browser-session dependency; only captured data is known. Follower attribution is an estimate, not causation. No extension was installed. |
| [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app/blob/main/libraries/nestjs-libraries/src/integrations/social/x.provider.ts) | Per-post views, likes, replies, reposts and quotes, with unavailable metrics handled separately. | Its X integration needs API credentials and access. Installing a scheduler does not provide distribution or remove X service costs. |
| [AM1403x/x-growth-dashboard](https://github.com/AM1403x/x-growth-dashboard/blob/main/dashboard.py) | Topic medians, sample counts and content comparisons. | Rejected its “algorithm-weighted” raw-count score: old fixed weights contradict X's current explanation. Its fallback also guesses self-replies from a 5,000-view threshold. |

Use the measurement ideas without importing these projects, handing them cookies, or copying unreliable scoring. Do not automate fake likes, reciprocal engagement, unsolicited replies or repeated self-replies to inflate counters.

## Seven-day experiment

Baseline collected November 11 at 11:44 UTC: 12 recent quotes, ages 7.6–75.6 hours, had **373 measured views, 20 likes, zero replies, zero reposts and zero quotes**. Median views were **22**. All counts were available. The largest source had over five million views, but its Qofeno quote had eleven. These varying ages and small sample prevent a causal topic comparison. [Full snapshot](reports/baseline-2026-11-11.json) and [read-only Actions audit](https://github.com/SohailKhan0525/trend-to-post/actions/runs/43014461966).

1. Establish a baseline from Qofeno's own published posts. Record collection time, post age, views, likes, replies, reposts and quotes; unavailable means unknown, not zero. Workflow success, confirmed publication and audience response are separate outcomes.
2. Keep one English voice and the AI coding niche for the experiment. Retain the headline, blank line and grounded detail. When a source presents a real decision, an optional specific question can invite experience; avoid generic “thoughts?”, follow requests and manufactured controversy.
3. Tag the editorial version and topic. Compare useful-detail posts with posts containing a relevant question, using snapshots near 24 and 72 hours and their actual ages. This is observational unless variants are assigned independently of topic and timing.
4. Review median views, the share of posts receiving a reply, and replies per 1,000 measured views. Show sample sizes. Very low exposure cannot establish that readers disliked the writing; one viral result cannot establish an optimal hour.
5. At day seven, keep the stronger useful topics only if there is enough comparable data. Otherwise continue collecting instead of declaring a winner. Answer genuine reader questions with relevant help; do not treat a reply quota as a growth tactic.

## Cloudflare limits and maintenance

[Cloudflare pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/) confirms the free 10,000-neuron allowance resets at **00:00 UTC / 05:30 IST**. A quota-aware pause should resume after reset, while cached editorial rejections and bounded generation reduce waste. A different model cannot restore an already exhausted account-wide allowance.

[Cloudflare's November 5 notice](https://developers.cloudflare.com/changelog/post/2026-11-05-planned-model-deprecations/) retires the current `@cf/qwen/qwen3-30b-a3b-fp8` on **November 20, 2026**, after which requests return HTTP 410. Evaluate a replacement before then. The recommended smaller [Gemma 4 model](https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/) costs 9,091 input / 27,273 output neurons per million tokens; the current Qwen costs 4,625 / 30,475. [GLM-4.7-Flash](https://developers.cloudflare.com/workers-ai/models/glm-4.7-flash/) is another candidate at 5,500 / 36,400. Neither is validated for this writer merely because its documentation exists. Test JSON parsing, unsupported claims, speculative sources, useful copy and actual token usage before changing the production model.
