# Conversation Posts

Cloudflare Workers AI writes short, playful standalone posts for X. The owner-requested target is **50 posts per UTC day**, spread across changing daily times. Quote/repost automation remains removed.

## Voice and content

Write like a curious person texting a friend: one clear thought, simple English, occasional slang, no forced catchphrase. Lowercase is welcome. Mild profanity is optional, never a compulsory opening. English is the default for a consistent global tech audience; switching languages is not treated as a reach trick.

The owner's new reference is **“is there a word between yes and no?”**. Other shapes include a small everyday puzzle, a choice between two relatable options, a recognizable tech frustration, or a playful coding opinion. References guide the tone; they are not a bank to recycle daily.

| Format | Daily opportunities |
| --- | ---: |
| Curiosity / everyday word questions | 12 |
| Two concrete choices | 10 |
| Relatable observations / humor | 10 |
| Playful coding takes | 8 |
| Specific AI-tool questions | 6 |
| Product invitations | 3 |
| Connection invitation | 1 |

Topics are sampled from larger pools every day and adjacent slots use different formats. Most posts have one line. A question gets one question mark and no answer or explanation. Greetings retain the owner's two-paragraph layout. Coding takes no longer require a particular opening phrase, which previously rejected otherwise valid drafts.

No invented personal struggles, achievements, product ownership, statistics, news or capabilities. No generic coaching, fake urgency, engagement trades, requests for likes/follows, or copied viral posts. A product invitation cannot promise to try every product. Text stays within a conservative 200-character weighted ceiling, below the standard X post limit.

## Excluded topics and duplication

**No politics, political parties, politicians, elections, government, public policy, countries, nationalities, flags, national comparisons, geopolitics, wars or country-related places/events.** Neutral references and jokes are excluded too.

Enforcement has three layers:

1. Explicit generation instructions and brief pools limited to ordinary life, language, coding, AI and building.
2. Local format, keyword, flag and repetition checks.
3. A separate Cloudflare topic classifier that must return exactly `{"allowed":true}`. A negative, ambiguous, malformed or unavailable review cannot approve a draft. The reviewer checks only excluded topics, not novelty or popularity.

The exact approved text and policy revision are hashed into the durable reservation. Publication repeats local validation and checks that approval matches the text; an edited or old unreviewed reservation cannot be published automatically. These layers reduce mistakes; an AI classifier is not a guarantee of perfect semantic judgment.

Exact normalized repeats are checked against up to 1,000 confirmed posts (roughly 20 full days). Near-duplicates and recycled fragments are checked against 150. Unicode words remain intact during normalization. The model sees recent posts and recent examples from its own category, plus the rejected text when correcting a draft. There are at most two generation attempts; no unsafe fallback or recycled filler to meet a quota.

## What the research supports

Research checked on 1 November 2026:

- [X's posting guidance](https://business.x.com/en/basics/get-your-business-started-with-x) recommends concise, conversational writing and engaging with other accounts.
- [X's community guidance](https://business.x.com/en/basics/community-management) recommends questions and genuine one-to-one conversation. Replies can help create relationships and discovery.
- [The published For You ranking system](https://github.com/xai-org/x-algorithm) predicts a viewer's reactions, including positive and negative actions. Its weights do not multiply raw engagement counts. It also reduces successive appearances from the same author within a feed. There is no supported “one reply equals N likes” shortcut or guaranteed viral wording.
- [X's authenticity policy](https://help.x.com/en/rules-and-policies/authenticity) prohibits repetitive content spam and artificial amplification. [Automation rules](https://help.x.com/en/rules-and-policies/x-automation) also restrict duplicate automated posts and unsolicited automated engagement.

**Editorial inference:** questions that are easy to understand and worth answering, concrete shared experiences and varied wording are better experiments than fifty variants of “let's connect.” This is a hypothesis for this account, not a proven uplift. Fifty is the owner's requested volume, not a research-backed optimum or promise of impressions.

For a seven-day trial, manually record each post's impressions, likes, replies and profile visits at the same age (for example, after 24 hours), where X exposes those metrics. Compare the median results for each category and replies/likes per 1,000 impressions, not just total activity. Keep formats that produce relevant conversations; revise or reduce weak ones. Missing metrics are unknown, not zero. The bot records category and topic but does not claim to measure or optimize impressions automatically.

Spend time answering real replies and joining relevant conversations personally. No automatic likes, follow exchanges, unsolicited replies, DMs or fabricated engagement are added. No growth outcome is guaranteed.

## Variable daily timing

The UTC day has 50 bands of 28.8 minutes. A date-seeded offset of 11–17 minutes inside each band creates a new, reproducible timetable every day. Planned slots are at least 22.8 minutes apart, including midnight. Actual publication has a 15-minute minimum gap and a hard daily cap of 50, including posts from the old plan on the rollout day.

The **Publish Conversation Posts** workflow checks every five minutes, offset from the top of the hour. The existing external `post-next` dispatcher still works. GitHub [documents schedule delays and dropped runs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), so 50 is a target and maximum, not a delivery guarantee. A dispatcher running only every 30 minutes cannot deliver all 50 opportunities by itself.

Only the latest due slot can run. Older missed opportunities expire; downtime cannot create a catch-up burst. A partial rollout day has fewer opportunities. Versioned slot IDs avoid collisions with the retired 20-slot timetable. The UTC count resets at 05:30 in Asia/Kolkata.

## Setup and operation

Existing GitHub Actions secrets are reused:

- `X_AUTH_TOKEN` and `X_CT0` for the existing X session client.
- `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` for Workers AI.
- Optional repository variable `CLOUDFLARE_AI_MODEL`; default `@cf/meta/llama-3.1-8b-instruct-fp8`.

No secret values are read from GitHub or printed. No paid plan or billing upgrade is enabled. Generation plus topic review normally uses two Workers AI calls per accepted post, with bounded correction attempts. Allocation, permissions and X session validity still apply. The existing session client is retained at the owner's request; X's automation rules restrict non-API automation and the client may break or be restricted.

Code pushes run tests and preview all seven styles before preparing a due post. Scheduled runs and `post-next` dispatches use the same content and topic checks. Disable the workflow in Actions to stop it.

```sh
python -m app preview                   # One preview, no publishing or state changes
python -m app preview --all-categories  # Seven previews, no publishing or state changes
python -m app prepare --start-now       # Reserve one due post; launch exception only on an empty history
```

## Publication and crash protection

Active state is `state/community_queue.json`. The old `state/post_queue.json` is a retired record and is never read by the publisher. Do not overwrite live state when deploying code changes.

The workflow saves and commits a reservation before calling `create_tweet(text=...)` exactly once. It then commits confirmation with the real X post ID. No quote attachment or automated reply is sent. A timeout, missing ID, cancelled run or failed final state push leaves an unresolved reservation; later runs stop to avoid duplicate publication.

To recover, disable the workflow and inspect X. If the reserved text was published, record its real `tweet_id`, `slot`, `category`, `text` and `posted_at` in history, update `last_posted_at`, and clear `pending`. If definitely not published, clear `pending`. Commit the correction before re-enabling. Never clear a reservation merely because a job failed. Posts already on X are not deleted.

## Verification

```sh
python -m pip install -r requirements.txt
python -m compileall -q app
python -m unittest discover -s tests -v
```

Tests cover a three-day five-minute schedule simulation, the 50-post cap, rollout migration, category variety, duplicate rejection, requested writing formats, excluded-topic checks, malformed/outage review decisions, approval-bound reservations, previews and uncertain publication outcomes. A successful unit test is not evidence of a live post; use the workflow's real X link to confirm delivery.
