# Qofeno

**@qofenoai** is an openly AI-run X account about software and AI. Qofeno likes a good debugging story, has opinions about tools, and enjoys a joke when the author has already supplied the setup. Cloudflare Workers AI chooses whether to publish an original, reply to a specific conversation, or sit out the opportunity. The account runs on GitHub Actions and works while the owner’s phone is offline.

## How Qofeno decides

1. The publisher syncs verified posting history to Cloudflare D1 and searches X using its existing Twikit-compatible client, `twifork[impersonate]==2.4.0`. Rotating Top and Latest searches cover AI coding tools, debugging, tests and code review. Discovery works without mentions. Multiple search topics get a turn, and recent posts with more measured views per hour rise within their topic.
2. Workers AI reads a shortlist of complete eligible posts, recent Qofeno writing and relevant public conversation history. It can choose a source and an angle for a reply, propose a standalone thought or choose silence. A deliberate silence uses up that opportunity.
3. An original can be an opinion, joke or observation about a software design choice, up to 220 weighted characters. A separate review checks the draft for unsupported claims and repetition. There is no scheduled invitation to follow the account.
4. Replies aim for 4–12 words, with a hard limit of 100 weighted characters. A playful post can get a dry punchline. A real unknown can earn one focused question. Someone asking for help can get a direct suggestion. Questions and emoji each have a one-reply cooldown. A second review checks the wording and tone against the actual source.

Reply candidates must be public original posts from the last 24 hours with at least 10,000 measured post views. The publisher excludes political or country-related subjects, protected or blocked accounts, sensitive posts, quotes and reposts. The same author is eligible at most once per 24 hours. Before sending a reply, the publisher reads the source again to check its text, identity and eligibility.

## Scheduling and delivery

**Qofeno autonomous publisher** runs from `.github/workflows/editorial.yml` on the default branch. GitHub cron, the existing `post-next` repository dispatcher, manual runs and relevant code pushes can wake it. Each UTC day has 48 half-hour decision windows with a daily offset. Only one public action fits a window, and consecutive publications must be at least 30 minutes apart, including across midnight. Missed windows expire.

The ceilings are two originals and 48 replies per UTC day, subject to the 48 shared windows. Originals are at least six hours apart. Follow posts verified by the older publisher count toward the original limit. These are upper bounds. Silence, source quality, provider budgets and scheduling delays can reduce output.

The publisher reserves a draft and its duplicate identities in `state/qofeno.json` before sending. It reads the post back from X to verify the account, text and intended reply parent. If X returns no ID, the publisher looks for that exact publication on the account’s own timeline. Ambiguous sends remain reserved. An explicit `CouldNotTweet` error can be archived as unconfirmed after the timeline check, with its source and text still reserved. An unresolved reservation older than an hour surfaces as a failed run after applicable cooldowns. Preserve the ledger when diagnosing failures. Earlier quote, self-reply and follow records remain part of the account’s history.

X documents its account automation and reply requirements in its [automation rules](https://help.x.com/en/rules-and-policies/x-automation) and [posting API guidance](https://docs.x.com/x-api/posts/manage-tweets/introduction).

## Cloudflare memory and configuration

D1 stores verified public posts, including Qofeno’s text and topic and, for replies, the original author’s public ID and handle, parent post ID and a short source excerpt. This lets the writer recognize someone it has replied to and avoid repeating its own take. Only verified ledger rows enter memory. Syncing can safely be repeated after an interrupted run. If D1 is unavailable, live decisions pause until memory is accessible.

| GitHub Actions setting | Purpose |
| --- | --- |
| Secrets `X_AUTH_TOKEN` and `X_CT0` | Existing X session for discovery, publication and readback |
| Secrets `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` | Workers AI access and default D1 credentials |
| Optional secret `CLOUDFLARE_D1_API_TOKEN` | Separate token with account D1 Write (Edit) permission if the Workers AI token lacks it |
| Optional variable `D1_DATABASE_ID` | UUID of an existing D1 database. Otherwise the publisher finds or creates `qofeno-memory` in the configured account |
| Optional variable `QOFENO_AI_MODEL` | Supported model override. The default is `@cf/google/gemma-4-26b-a4b-it` |

The workflow needs repository contents write access to persist reservations. The local AI budget reserves 400 neurons per hour and 9,000 per UTC day and uses reported token usage when available. Workers AI quota responses and X rate-limit responses trigger cooldowns. Other applications using the same Cloudflare account can affect its available quota.

## Operations

From a checkout with `requirements.txt` installed, use the following commands.

| Command | Effect |
| --- | --- |
| `python -m app inspect` | Show scheduling and delivery status without publishing or calling the model |
| `python -m app memory` | Connect to D1, sync verified history and read back recent posts. It can initialize the database and schema. It does not publish to X |
| `python -m app run` | Run an eligible live decision and publish the selected action if all checks pass |
| `python -m app metrics` | Read available impressions, likes, replies and reposts for recent verified publications |
| `python -m unittest discover -s tests -v` | Run the test suite |

On GitHub, open the publisher run’s job summary to see its decision, any blocking condition and any verified X link. The separate metrics workflow distinguishes impressions on Qofeno’s posts from views on the original source. Missing metrics remain unknown.
