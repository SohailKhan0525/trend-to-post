"""Forty daily opportunities with spacing and no catch-up bursts."""
import random
from datetime import datetime, timedelta, timezone

DAILY_CAP = 40
MIN_GAP = timedelta(minutes=25)
RECHECK_GAP = timedelta(minutes=10)


def daily_plan(day):
    rng = random.Random("qofeno-quotes-v1/" + day)
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return [{"slot":f"{day}:quote-v1:{i}",
             "due_at":(start+timedelta(minutes=36*i+rng.randint(2,7))).isoformat()}
            for i in range(DAILY_CAP)]


def next_slot(state, now, window_end=None):
    if state.get("pending") or state.get("pause_reason") or state.get("verification_pending"):
        return None
    now = now.astimezone(timezone.utc)
    day = now.date().isoformat()
    sent = [row for row in state["history"] if row.get("posted_at")]
    if sum(row["posted_at"][:10]==day for row in sent) >= DAILY_CAP:
        return None
    used = {row["slot"] for row in sent}
    last = state.get("last_posted_at")
    earliest = datetime.fromisoformat(last)+MIN_GAP if last else now
    plan = daily_plan(day)
    for i, item in enumerate(plan):
        due = datetime.fromisoformat(item["due_at"])
        expires = (datetime.fromisoformat(plan[i+1]["due_at"]) if i+1<len(plan)
                   else due.replace(hour=0,minute=0)+timedelta(days=1))
        ready = max(due,earliest,now)
        tried = state.get("last_attempt")
        if tried and tried["slot"] == item["slot"]:
            ready = max(ready,datetime.fromisoformat(tried["at"])+RECHECK_GAP)
        if item["slot"] in used or ready>=expires:
            continue
        if window_end is None and ready>now:
            continue
        if window_end is not None and ready>=window_end:
            continue
        return item,ready
    return None
