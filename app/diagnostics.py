"""Read-only, bounded inspection of public source evidence and editorial decisions."""
import json
from datetime import datetime, timedelta, timezone
from statistics import median

from twikit.errors import InvalidSession, TooManyRequests

from . import queue, sources, writer
from .cloudflare import DraftError


METRIC_FIELDS = {"views": "view_count", "likes": "favorite_count",
                 "replies": "reply_count", "reposts": "retweet_count",
                 "quotes": "quote_count"}
METRICS_LIMIT = 12
METRICS_MIN_AGE = timedelta(hours=6)


def metric_candidates(history, now):
    """Select recent published posts with time to circulate; never inspect pending sends."""
    candidates = []
    seen = set()
    for row in history:
        if not isinstance(row, dict):
            continue
        ident = str(row.get("tweet_id") or "")
        if not ident.isascii() or not ident.isdigit() or int(ident) <= 0:
            continue
        try:
            posted = datetime.fromisoformat(row["posted_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if posted.tzinfo is None or now - posted < METRICS_MIN_AGE:
            continue
        candidates.append((posted, ident, row))
    selected = []
    for posted, ident, row in sorted(candidates, key=lambda value: value[0], reverse=True):
        if ident in seen:
            continue
        seen.add(ident)
        selected.append((posted, ident, row))
        if len(selected) == METRICS_LIMIT:
            break
    return selected


def public_count(post, field):
    """X can withhold counts. Missing/invalid values must not become fabricated zeros."""
    try:
        value = getattr(post, field, None)
    except (AttributeError, KeyError, TypeError):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


async def metrics():
    """Collect a bounded, read-only snapshot of this account's actual post performance."""
    state = queue.load()
    now = queue.utcnow().astimezone(timezone.utc)
    selected = metric_candidates(state["history"], now)
    report = {"captured_at": now.isoformat(), "minimum_age_hours": 6,
              "maximum_reads": METRICS_LIMIT, "selected_posts": len(selected),
              "posts": [], "errors": []}
    if selected:
        connection = queue.client()
        for posted, ident, row in selected:
            try:
                post = await connection.get_tweet_by_id(ident)
                if str(getattr(post, "id", "")) != ident:
                    raise ValueError("Unexpected post identity")
            except InvalidSession:
                raise
            except TooManyRequests:
                report["errors"].append({"tweet_id": ident, "error": "TooManyRequests"})
                report["stopped"] = "X read rate limit; no retry was attempted."
                break
            except Exception as exc:
                # Exception messages can include requests or headers; only expose the class.
                report["errors"].append({"tweet_id": ident, "error": type(exc).__name__})
                continue
            report["posts"].append({
                "tweet_id": ident, "url": "https://x.com/i/status/" + ident,
                "posted_at": posted.isoformat(),
                "age_hours": round((now - posted).total_seconds() / 3600, 1),
                "text": str(row.get("text") or "")[:1000],
                "source_id": str(row.get("source_id") or ""),
                "source_author": str(row.get("author") or "")[:80],
                "source_views_at_publication": row.get("source_views"),
                "metrics": {name: public_count(post, field) for name, field in METRIC_FIELDS.items()},
            })
    report["summary"] = {}
    for name in METRIC_FIELDS:
        known = [row["metrics"][name] for row in report["posts"]
                 if row["metrics"][name] is not None]
        report["summary"][name] = {"available_posts": len(known),
                                   "median": median(known) if known else None,
                                   "total": sum(known) if known else None}
    queue.summary("Own-post performance snapshot (views are not unique viewers; null means unavailable).\n\n"
                  "```json\n" + json.dumps(report, ensure_ascii=True, indent=2) + "\n```")
    queue.summary("Read-only metrics audit finished; no AI calls, posts, or publication-state changes.")
    return report


async def diagnose():
    state = queue.load()
    queue.require_open(state)
    connection = queue.client()
    candidates = await sources.discover(connection, state, queue.utcnow())
    original_complete = writer.complete

    def traced_complete(system, data, **kwargs):
        result = original_complete(system, data, **kwargs)
        stage = "classify" if system == writer.SOURCE_GATE else "write" if system == writer.VOICE else "review"
        keys = {"subject", "event_key", "facts", "headline", "body", "text",
                *writer.GATE_FLAGS, *writer.REVIEW_FLAGS, *writer.REVIEW_EVIDENCE}
        selected = ({key: value for key, value in result.items() if key in keys}
                    if isinstance(result, dict) else {"result_type": type(result).__name__})
        # JSON encodes newlines/control characters; no headers, cookies, or environment are logged.
        print("Editorial diagnostic " + json.dumps({"stage": stage, "result": selected}, ensure_ascii=True)[:9000], flush=True)
        return result

    writer.complete = traced_complete
    try:
        for _, candidate in queue.candidate_batch(state, candidates):
            evidence = {key: candidate[key] for key in ("id", "author", "text", "views", "created_at")}
            print("Source diagnostic " + json.dumps(evidence, ensure_ascii=True)[:18000], flush=True)
            try:
                draft = writer.generate(candidate, state, queue.utcnow())
            except DraftError as exc:
                queue.summary("Diagnostic rejection: " + str(exc)[:600])
            else:
                queue.summary("Diagnostic approved draft\n\n" + draft["text"])
    finally:
        writer.complete = original_complete
    queue.summary("Read-only diagnostic finished; no post sent and no publication state changed.")
