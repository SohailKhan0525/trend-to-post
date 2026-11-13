"""Read-only, bounded performance snapshots of Qofeno's published X posts."""
import json
from datetime import datetime, timedelta, timezone
from statistics import median

from twikit.errors import InvalidSession, TooManyRequests

from . import store


METRIC_FIELDS = {"views": "view_count", "likes": "favorite_count",
                 "replies": "reply_count", "reposts": "retweet_count",
                 "quotes": "quote_count"}
METRICS_LIMIT = 12
METRICS_MIN_AGE = timedelta(hours=6)
PUBLISHED_STATUSES = {None, "confirmed", "published", "verified", "sent"}


def post_id(value):
    """Accept only real-looking positive X IDs, never booleans or draft identifiers."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value)
    return value if value.isascii() and value.isdigit() and int(value) > 0 else None


def metric_candidates(history, now):
    """Select recent published posts; draft artifacts and pending sends are not posts."""
    candidates = []
    for row in history:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        if not isinstance(kind, (str, type(None))):
            continue
        if kind in {"draft", "reply_draft"} or row.get("published") is False:
            continue
        # Legacy publication rows have no status. New rows must not count an
        # unconfirmed send, even if a source/draft ID was accidentally copied in.
        status = row.get("status")
        if not isinstance(status, (str, type(None))) or status not in PUBLISHED_STATUSES:
            continue
        ident = post_id(row.get("tweet_id"))
        if ident is None:
            continue
        try:
            posted = datetime.fromisoformat(row["posted_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if posted.tzinfo is None or now - posted < METRICS_MIN_AGE:
            continue
        candidates.append((posted, ident, row))
    selected, seen = [], set()
    for posted, ident, row in sorted(candidates, key=lambda value: value[0], reverse=True):
        if ident in seen:
            continue
        seen.add(ident)
        selected.append((posted, ident, row))
        if len(selected) == METRICS_LIMIT:
            break
    return selected


def public_count(post, field):
    """Missing/invalid public counts remain unknown, never fabricated zeros."""
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


def aggregate(values):
    known = [value for value in values if value is not None]
    return {"available_posts": len(known), "median": median(known) if known else None,
            "total": sum(known) if known else None}


async def metrics():
    """Read at most twelve eligible own posts, with no AI calls or state writes."""
    state = store.load()
    now = store.utcnow().astimezone(timezone.utc)
    selected = metric_candidates(state.get("history", []), now)
    report = {"captured_at": now.isoformat(), "minimum_age_hours": 6,
              "maximum_reads": METRICS_LIMIT, "selected_posts": len(selected),
              "posts": [], "errors": []}
    if selected:
        connection = store.client()
        for posted, ident, row in selected:
            try:
                post = await connection.get_tweet_by_id(ident)
                if post_id(getattr(post, "id", None)) != ident:
                    raise ValueError("Unexpected post identity")
            except (InvalidSession, store.SessionUnavailable):
                raise
            except TooManyRequests:
                report["errors"].append({"tweet_id": ident, "error": "TooManyRequests"})
                report["stopped"] = "X read rate limit; no retry was attempted."
                break
            except Exception as exc:
                # Messages can contain request headers. Expose only the type.
                report["errors"].append({"tweet_id": ident, "error": type(exc).__name__})
                continue
            counts = {name: public_count(post, field) for name, field in METRIC_FIELDS.items()}
            known_self = int(post_id(row.get("reply_id")) is not None)
            self_uncertain = row.get("reply_status") == "unconfirmed" and not known_self
            text = str(row.get("text") or "")
            source = row.get("source") if isinstance(row.get("source"), dict) else {}
            source_id = row.get("source_id") or source.get("id")
            source_views = row.get("source_views")
            if source_views is None:
                source_views = source.get("views")
            report["posts"].append({
                "tweet_id": ident, "url": "https://x.com/i/status/" + ident,
                "posted_at": posted.isoformat(),
                "age_hours": round((now - posted).total_seconds() / 3600, 1),
                "kind": row.get("kind") or ("quote" if source_id else "post"),
                "text": text[:1000],
                "source_id": str(source_id or ""),
                "source_author": str(row.get("author") or source.get("author") or "")[:80],
                "editorial_version": row.get("editorial_version", "legacy"),
                "body_style": row.get("body_style", "question" if "?" in text else "detail"),
                "source_views_at_publication": source_views,
                "metrics": counts,
                "known_self_replies": known_self,
                "self_reply_uncertain": self_uncertain,
                "other_replies_estimate": (max(0, counts["replies"] - known_self)
                                            if counts["replies"] is not None and not self_uncertain else None),
            })
    report["summary"] = {name: aggregate(row["metrics"][name] for row in report["posts"])
                         for name in METRIC_FIELDS}
    report["summary"]["known_self_replies"] = sum(row["known_self_replies"] for row in report["posts"])
    report["summary"]["other_replies_estimate"] = aggregate(
        row["other_replies_estimate"] for row in report["posts"])
    report["reply_note"] = ("Public reply totals include our self-replies. Other replies subtract known "
                            "sent self-replies and remain an estimate; unresolved self-reply sends make "
                            "that estimate unavailable.")
    store.summary("Own-post performance snapshot (views are not unique viewers; null means unavailable).\n\n"
                  "```json\n" + json.dumps(report, ensure_ascii=True, indent=2) + "\n```")
    store.summary("Read-only metrics audit finished; unconfirmed publication reservations are excluded. "
                  "No AI calls, posts, or publication-state changes.")
    return report
