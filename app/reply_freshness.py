"""Check incoming post and edit times against the active publisher's intake window."""
from datetime import datetime, timedelta, timezone

from .reply_schedule import timestamp


INBOX_START_AT = datetime(2026, 11, 17, 14, 5, 11, tzinfo=timezone.utc)
MAX_REPLY_AGE = timedelta(minutes=5)
SCHEDULED_REPLY_AGE = timedelta(hours=1)
# X Snowflake IDs contain milliseconds since this epoch above the low 22 bits.
X_EPOCH = datetime(2010, 11, 4, 1, 42, 54, 657000, tzinfo=timezone.utc)


def post_created_at(value):
    ident = str(value)
    if not 1 <= len(ident) <= 19 or not ident.isascii() or not ident.isdecimal() or not 0 < int(ident) < 2**63:
        raise ValueError('Invalid X post ID')
    return X_EPOCH + timedelta(milliseconds=int(ident) >> 22)


def invitation_hold(row, now, cutoff=INBOX_START_AT, *, state=None):
    """Use the child's native creation and edit identities, including the original edition."""
    try:
        scheduled = (state or {}).get('actions_inbox_since')
        if scheduled:
            cutoff = max(cutoff, timestamp(scheduled))
        reported = timestamp(row['created_at'])
        current = post_created_at(row['id'])
        aliases = row['source_ids']
        if not isinstance(aliases, list) or not aliases:
            raise ValueError('Missing edit identities')
        versions = [post_created_at(ident) for ident in aliases]
        # X's text timestamp has second precision. Its own ID resolves that second.
        if current.replace(microsecond=0) == reported:
            reported = current
        earliest, latest = min(reported, current, *versions), max(reported, current, *versions)
        if earliest < cutoff:
            return 'source predates reply activation'
        if latest > now:
            return 'source timestamp is in the future'
        if earliest < now - (SCHEDULED_REPLY_AGE if scheduled else MAX_REPLY_AGE):
            return 'source is over one hour old' if scheduled else 'source is over five minutes old'
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return 'invalid source timestamp or identity'
    return None


def fresh_invitation(row, now, cutoff=INBOX_START_AT, *, state=None):
    return invitation_hold(row, now, cutoff, state=state) is None
