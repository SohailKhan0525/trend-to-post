"""A reproducible random timetable and content mix, regenerated for each UTC day."""
import hashlib
import random
from datetime import datetime, timedelta, timezone
from .content import DAILY_MIX, TOPICS, FORMATS

POSTS_PER_DAY = 50
MIN_GAP = timedelta(minutes=15)
PLAN_VERSION = 'conversation-v2'

def daily_plan(day):
    rng = random.Random(int(hashlib.sha256((PLAN_VERSION+':'+day).encode()).hexdigest(),16))
    pools = {category:rng.sample(TOPICS[category], count) for category, count in DAILY_MIX.items()}
    briefs = []
    # Random interleaving, with a feasibility check so no repeated category is
    # forced at the end. For a suffix after c, c can occupy at most half its slots.
    while any(pools.values()):
        total = sum(map(len,pools.values()))
        options = [c for c,topics in pools.items() if topics and (not briefs or c!=briefs[-1][0])
                   and all(len(items)-(k==c) <= ((total-1)//2 if k==c else total//2)
                           for k,items in pools.items())]
        category = rng.choices(options, weights=[len(pools[c]) for c in options])[0]
        briefs.append((category,pools[category].pop()))
    midnight = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    # 28.8-minute bands, offset 11..17 minutes; minimum planned gap is 22.8 minutes,
    # including midnight. Versioned slots cannot collide with the old 20-slot plan.
    return [{'slot':f'{day}:{PLAN_VERSION}:{i}',
             'due_at':(midnight+timedelta(seconds=i*86400//POSTS_PER_DAY,
                                       minutes=rng.randint(11,17))).isoformat(),
             'category':category, 'topic':topic,
             'brief':FORMATS[category]+' Topic: '+topic}
            for i,(category,topic) in enumerate(briefs)]


def next_due(state, now, start_now=False):
    if state.get('pending'):
        raise ValueError('Unresolved publication reservation; inspect X before continuing')
    today = now.astimezone(timezone.utc).date().isoformat()
    published = [p for p in state['history'] if p['posted_at'][:10]==today]
    if len(published)>=POSTS_PER_DAY:
        return None
    last = state.get('last_posted_at')
    if last and now-datetime.fromisoformat(last)<MIN_GAP:
        return None
    used = {p['slot'] for p in state['history']}
    due = [s for s in daily_plan(today) if datetime.fromisoformat(s['due_at'])<=now]
    if due:
        return None if due[-1]['slot'] in used else due[-1]  # Missed earlier windows expire; never burst to fill them.
    if start_now and not state['history']:
        # First launch only. Consume a real slot, so launch cannot exceed the cap.
        return daily_plan(today)[0]
    return None
