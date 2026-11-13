"""Read-only, bounded inspection of public source evidence and editorial decisions."""
import json
from datetime import datetime, timedelta, timezone
from statistics import median

from twikit.errors import InvalidSession, TooManyRequests

from . import queue, sources, writer
from .cloudflare import DraftError
from .ai_budget import budget_scope


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
            counts = {name: public_count(post, field) for name, field in METRIC_FIELDS.items()}
            known_self = int(bool(row.get('reply_id')))
            self_uncertain = row.get('reply_status') == 'unconfirmed' and not known_self
            report["posts"].append({
                "tweet_id": ident, "url": "https://x.com/i/status/" + ident,
                "posted_at": posted.isoformat(),
                "age_hours": round((now - posted).total_seconds() / 3600, 1),
                "text": str(row.get("text") or "")[:1000],
                "source_id": str(row.get("source_id") or ""),
                "source_author": str(row.get("author") or "")[:80],
                "editorial_version": row.get("editorial_version", "legacy"),
                "body_style": row.get("body_style", "question" if '?' in str(row.get("text", "")) else "detail"),
                "source_views_at_publication": row.get("source_views"),
                "metrics": counts,
                "known_self_replies": known_self,
                "self_reply_uncertain": self_uncertain,
                "other_replies_estimate": (max(0, counts['replies'] - known_self)
                                            if counts['replies'] is not None and not self_uncertain else None),
            })
    report["summary"] = {}
    for name in METRIC_FIELDS:
        known = [row["metrics"][name] for row in report["posts"]
                 if row["metrics"][name] is not None]
        report["summary"][name] = {"available_posts": len(known),
                                   "median": median(known) if known else None,
                                   "total": sum(known) if known else None}
    report['summary']['known_self_replies'] = sum(row['known_self_replies'] for row in report['posts'])
    external = [row['other_replies_estimate'] for row in report['posts'] if row['other_replies_estimate'] is not None]
    report['summary']['other_replies_estimate'] = {'available_posts': len(external),
        'total': sum(external) if external else None, 'median': median(external) if external else None}
    report['reply_note'] = 'Public reply totals include our self-replies. Other replies subtract known sent self-replies and remain an estimate; unresolved self-reply sends make that estimate unavailable.'
    queue.summary("Own-post performance snapshot (views are not unique viewers; null means unavailable).\n\n"
                  "```json\n" + json.dumps(report, ensure_ascii=True, indent=2) + "\n```")
    queue.summary("Read-only metrics audit finished; no AI calls, posts, or publication-state changes.")
    return report


async def diagnose():
    state = queue.load()
    queue.require_open(state)
    if queue.report_pause(state):
        return
    connection = queue.client()
    candidates = queue.unreviewed_candidates(state, await sources.discover(connection, state, queue.utcnow()), queue.utcnow())
    original_complete = writer.complete

    def traced_complete(system, data, **kwargs):
        result = original_complete(system, data, **kwargs)
        stage = "classify" if system == writer.SOURCE_GATE else "write" if system == writer.VOICE else "review"
        keys = {"subject", "event_key", "facts", "headline", "body", "text", "format", "reply",
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
                with budget_scope(state, queue.save_and_push, queue.utcnow):
                    draft = writer.generate(candidate, state, queue.utcnow())
            except DraftError as exc:
                queue.remember_rejection(state, candidate, exc, queue.utcnow())
                queue.save_and_push(state)
                queue.summary("Diagnostic rejection: " + str(exc)[:600])
            else:
                queue.save_and_push(state)
                queue.summary("Diagnostic approved draft\n\n" + draft["text"])
    finally:
        writer.complete = original_complete
    queue.summary("Diagnostic finished; no post sent. Only AI spending and editorial cooldowns were saved.")
