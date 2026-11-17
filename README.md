# Qofeno

**@qofenoai** is an openly AI-run X account about software and AI. Qofeno likes a good debugging story, has opinions about tools, and enjoys a joke when the author has already supplied the setup. Cloudflare Workers AI chooses whether to publish an original, reply to a specific conversation, or sit out the opportunity. The Azure deployment runs one persistent Linux process. Cloudflare supplies the model and database. The account operates while the owner's phone is offline.

## How Qofeno decides

1. The publisher syncs verified posts to Cloudflare D1. Its existing Twikit-compatible client, `twifork[impersonate]==2.4.0`, checks for public mentions and direct responses to Qofeno, and searches Top and Latest conversations about AI coding tools, debugging, tests and code review. Recent posts with more measured views per hour rise within each search topic.
2. Workers AI reads a shortlist of complete eligible posts, recent Qofeno writing and relevant public conversation history. It can choose an invited conversation and an angle for a reply, use what it reads to form a standalone thought, or choose silence. A deliberate silence uses up that opportunity.
3. An original can be an opinion, joke or observation about a software design choice, up to 220 weighted characters. A separate review checks the draft for unsupported claims and repetition. There is no scheduled invitation to follow the account.
4. Replies aim for 4–12 words, with a hard limit of 100 weighted characters. A playful post can get a dry punchline. A real unknown can earn one focused question. Someone asking for help can get a direct suggestion. Questions and emoji each have a one-reply cooldown. A second review checks the wording and tone against the actual source.

For proactive discovery, a source must be a public original post from the last 24 hours with at least 10,000 measured views. Discoveries can inform original posts. To qualify for an automated reply, someone must invite Qofeno directly or comment on a verified Qofeno post or reply. Direct interactions can qualify at any view count. The publisher excludes political or country-related subjects, protected or blocked accounts, sensitive posts, quotes and reposts. Before sending a reply it reads the source again and verifies its text, identity and eligibility.

To invite Qofeno, tag it directly, ask to hear its take, or comment on one of its posts. A simple `@qofenoai` can start a conversation. A correction, a story or a joke can continue one, even when X leaves the handle out of the visible reply. Someone crediting Qofeno in passing can do so without starting a conversation.

On publishing opportunities the worker checks the mention inbox, searches for the handle and reads replies to a small rotating set of its own recent posts. It verifies the exact parent before continuing a thread. A reader can receive one answer per incoming post and up to three in the same thread in a rolling 24 hours. To opt out, post `@qofenoai stop`.

## Scheduling and delivery

On Azure, systemd runs Qofeno continuously and restarts it after a crash or reboot. The worker checks the next eligible publishing window and renews its D1 lease every 20 seconds. During migration, the Azure worker passed live D1, Workers AI and X mention checks before the complete GitHub ledger moved to D1. D1 now grants publishing authority to one worker instance. The old GitHub publishing workflow is retired. GitHub continues to run tests and collect metrics. Each UTC day has 48 half-hour decision windows with a daily offset. Only one public action fits a window, and consecutive publications must be at least 30 minutes apart, including across midnight. Missed windows expire.

The ceilings are two originals and 48 replies per UTC day, subject to the 48 shared windows. Originals are at least six hours apart. Follow posts verified by the older publisher count toward the original limit. These are upper bounds. Silence, source quality, provider budgets and scheduling delays can reduce output.

The publisher reserves each draft and its duplicate identities before sending. On Azure the reservation lives in D1. The original GitHub ledger in `state/qofeno.json` supplies the one-time migration snapshot. It reads the post back from X to verify the account, text and intended reply parent. If X returns no ID, the publisher looks for that exact publication on the account’s own timeline. Ambiguous sends remain reserved. An explicit `CouldNotTweet` error can be archived as unconfirmed after the timeline check, with its source and text still reserved. An unresolved reservation older than an hour surfaces as a failed run after applicable cooldowns. Preserve the ledger when diagnosing failures. Earlier quote, self-reply and follow records remain part of the account’s history.

X documents its account automation and reply requirements in its [automation rules](https://help.x.com/en/rules-and-policies/x-automation) and [posting API guidance](https://docs.x.com/x-api/posts/manage-tweets/introduction).

## Cloudflare memory and configuration

D1 stores verified public posts, including Qofeno’s text and topic and, for replies, the original author’s public ID and handle, parent post ID and a short source excerpt. This lets the writer recognize someone it has replied to and avoid repeating its own take. Only verified ledger rows enter memory. Syncing can safely be repeated after an interrupted run. On Azure, losing the D1 lease or access to the ledger pauses publishing. This preserves outstanding reservations when a VM restarts or another instance attempts to take over.

| GitHub Actions setting | Purpose |
| --- | --- |
| Secrets `X_AUTH_TOKEN` and `X_CT0` | Existing X session for discovery, publication and readback |
| Secrets `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` | Workers AI access and default D1 credentials |
| Optional secret `CLOUDFLARE_D1_API_TOKEN` | Separate token with account D1 Write (Edit) permission if the Workers AI token lacks it |
| Optional variable `D1_DATABASE_ID` | UUID of an existing D1 database. Otherwise the publisher finds or creates `qofeno-memory` in the configured account |
| Optional variable `QOFENO_AI_MODEL` | Supported model override. The default is `@cf/google/gemma-4-26b-a4b-it` |

Azure reads its X and Cloudflare settings from a dedicated Key Vault using the VM’s managed identity. Only the deploy workflow on the repository’s main branch receives vault write access. The worker has no GitHub write credential. The local AI budget reserves 400 neurons per hour and 9,000 per UTC day and uses reported token usage when available. Workers AI quota responses and X rate-limit responses trigger cooldowns. Other applications using the same Cloudflare account can affect its available quota.

## Operations

To deploy the worker, use the [Azure VM template](infra/azure/main.json) in the dedicated resource group. Supply a commit containing the bootstrap, an RSA public key and the exact GitHub OIDC subject reported by the deployment workflow. All inbound ports on the VM are closed. Use the template’s `githubTarget` output plus a full `release` commit SHA in `infra/azure-target.json` to start the deployment. The workflow transfers settings to Key Vault, verifies the standby worker, and hands off publication to D1. To deploy an update, change the release SHA to a tested commit on main. The worker retrieves versioned settings and checks out that exact commit.

From a checkout with `requirements.txt` installed, use these commands.

| Command | Effect |
| --- | --- |
| `python -m app server-status --release <commit>` | Verify a recent active D1 heartbeat for that exact Azure release |
| `python scripts/azure-price.py --location centralindia --currency INR` | Look up retail prices and estimate monthly spend before and after eligible allowances |
| `python -m app inspect` | Show scheduling and delivery status without publishing or calling the model |
| `python -m app memory` | Connect to D1, sync verified history and read back recent posts. It can initialize the database and schema. It does not publish to X |
| `python -m app run` | Run an eligible live decision and publish the selected action if all checks pass |
| `python -m app metrics` | Read available impressions, likes, replies and reposts for recent verified publications |
| `python -m unittest discover -s tests -v` | Run the test suite |

On the VM, the systemd journal for `qofeno.service` records decisions and verified X links. The deployment workflow records the release and active heartbeat at cutover. The separate metrics workflow distinguishes impressions on Qofeno’s posts from views on the original source. Missing metrics remain unknown.
