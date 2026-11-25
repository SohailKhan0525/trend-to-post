"""Count the daily answer goal from verified publication receipts only."""
from datetime import datetime, timezone


DAILY_REASONED_GOAL = 5


def reasoned_reply_count(state, now):
    if now.tzinfo is None:
        raise ValueError('The daily answer count requires a timezone-aware clock.')
    day = now.astimezone(timezone.utc).date()
    identities = set()
    for row in state.get('history', []):
        if (not isinstance(row, dict) or row.get('kind') != 'reply'
                or row.get('reply_style') != 'reasoned_answer'):
            continue
        ident = row.get('tweet_id')
        if not isinstance(ident, str) or not ident.isascii() or not ident.isdecimal() or len(ident) > 20:
            continue
        try:
            posted = datetime.fromisoformat(row['posted_at'].replace('Z', '+00:00'))
            verified = datetime.fromisoformat(row['verified_at'].replace('Z', '+00:00'))
            if posted.tzinfo is None or verified.tzinfo is None or int(ident) <= 0:
                continue
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if posted.astimezone(timezone.utc).date() == day:
            identities.add(int(ident))
    return len(identities)
