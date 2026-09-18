# trend-to-post

Personal production content engine for **X Trends → Technology + Artificial Intelligence drafts → GitHub review**.

## Scope

- **Trend source:** X Trends only.
- **Niche:** Technology + Artificial Intelligence.
- **AI:** Gemini generates original draft candidates from the X Trends snapshot.
- **Output:** GitHub-ready Markdown for human review.
- **Publishing:** intentionally manual. This project does not automate posting to X.

X's current automation rules prohibit automated posting about X Trending Topics, so the system stops at reviewable drafts. See the official X Automation Rules.

## Production architecture

1. Authenticate to X with a locally stored browser-exported cookie file.
2. Collect the current X Trends snapshot.
3. Deduplicate the returned trend names.
4. Retry transient X failures with exponential backoff.
5. Generate a small set of original Technology/AI draft candidates with Gemini.
6. Write the run to a dated Markdown file for review.
7. Never call X write endpoints.

The collector uses **twifork 2.4.0**, a maintained drop-in fork of Twikit. Upstream Twikit has a documented get_trends breakage/deprecation issue; twifork rebuilt that method around X's newer trend flow.

## Android / Termux

Install:

    pkg update
    pkg install python
    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

Create `.env` from `.env.example` and keep it local. Never commit cookies or API keys.

### X cookies

Current X web login flows are not reliable for this kind of non-browser client, so this project uses an exported authenticated cookie file rather than storing an X password. The cookie file must contain a valid authenticated X session.

Set:

    X_COOKIES_FILE=data/x_cookies.json
    X_TRENDS_LOCATION=worldwide

The location value is retained as a configuration label for the X account/session. The collector does not pretend to override X's server-side trend location.

### Gemini

Set:

    GEMINI_API_KEY=your_key

## Commands

    python -m app health
    python -m app trends
    python -m app generate
    python -m app run

The `trends` command is the safest first end-to-end check because it verifies X authentication and trend collection without calling Gemini.

## Safety / platform boundary

This project is deliberately a **research + drafting workflow**, not an automated X posting bot. It does not automate likes, follows, replies, reposts, or publishing.

For best results, review each draft before posting it manually.
