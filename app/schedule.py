"""A reproducible UTC timetable with fifty spaced slots per full day."""
import random
from datetime import datetime, timedelta, timezone
from .editorial import FORMS, LANGUAGES, TOPICS

DAILY_CAP = 50
MIN_GAP = timedelta(minutes=15)


def daily_plan(day):
    rng = random.Random("dash-rattle-v1/" + day)
    midnight = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    # The regular dispatcher wakes near the start of each half hour. Two
    # separated half hours get an extra slot with room on either side.
    offsets = [rng.randint(2, 8) for _ in range(48)]
    first = rng.randrange(1, 22)
    second = rng.randrange(24, 46)
    for band in (first, second):
        offsets[band] = rng.randint(2, 4)
        offsets[band + 1] = rng.randint(7, 9)
    minutes = [30*i + offset for i, offset in enumerate(offsets)]
    minutes += [(minutes[i] + minutes[i+1]) // 2 for i in (first, second)]

    briefs = [(topic, detail) for topic, (count, pool) in TOPICS.items()
              for detail in rng.sample(pool, count)]
    languages = [language for language, count in LANGUAGES.items() for _ in range(count)]
    rng.shuffle(briefs)
    rng.shuffle(languages)
    forms = list(FORMS)*8
    rng.shuffle(forms)
    return [
        {"slot": f"{day}:dash-v1:{i}", "due_at": (midnight+timedelta(minutes=minute)).isoformat(),
         "topic": briefs[i][0], "detail": briefs[i][1], "language": languages[i], "form": forms[i]}
        for i, minute in enumerate(sorted(minutes))
    ]


def next_slot(state, now, window_end=None):
    """Return (brief, ready time). A waiting dispatch only covers its own band."""
    if state.get("pending"):
        raise ValueError("Unresolved reservation")
    now = now.astimezone(timezone.utc)
    day = now.date().isoformat()
    count = state.get("prior_days", {}).get(day, 0)
    sent = [row for row in state["history"] if row.get("posted_at")]
    count += sum(row["posted_at"][:10] == day for row in sent)
    if count >= DAILY_CAP:
        return None

    plan = daily_plan(day)
    used = {p["slot"] for p in sent}
    last = state.get("last_posted_at")
    earliest = datetime.fromisoformat(last) + MIN_GAP if last else now
    band_start = now.replace(minute=now.minute//30*30, second=0, microsecond=0)
    for i, brief in enumerate(plan):
        due = datetime.fromisoformat(brief["due_at"])
        expires = (datetime.fromisoformat(plan[i+1]["due_at"]) if i+1 < len(plan)
                   else band_start.replace(hour=0, minute=0)+timedelta(days=1))
        if window_end is not None and not band_start <= due < window_end:
            continue
        ready = max(due, earliest, now)
        if brief["slot"] in used or ready >= expires:
            continue
        if window_end is None and ready > now:
            continue
        if window_end is not None and ready >= window_end:
            continue
        return brief, ready
    return None
