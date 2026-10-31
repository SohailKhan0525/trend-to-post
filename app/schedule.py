"""A reproducible random timetable and content mix, regenerated for each UTC day."""
import hashlib
import random
from datetime import datetime, timedelta, timezone

POSTS_PER_DAY = 20
MIN_GAP = timedelta(minutes=30)
BRIEFS = (
    ('connect', "Hello fake founders. Second line: Let's connect. Keep it this blunt."),
    ('connect', 'Hello founders. A short invitation to connect.'),
    ('connect', 'Hello solo founders. A short invitation to connect with other solo builders.'),
    ('connect', 'Hello indie hackers. A short invitation to connect.'),
    ('connect', 'Hello side-project builders. A short invitation to connect.'),
    ('connect', 'Hello bootstrapped founders. A short invitation to connect.'),
    ('connect', 'Hello developers. A short invitation to connect with people shipping products.'),
    ('connect', 'Hello first-time founders. A short invitation to connect.'),
    ('showcase', "Hello founders. Ask them to comment their startup product; say you'll try it if you need it."),
    ('showcase', 'Hello builders. Ask them to drop the product they are building.'),
    ('showcase', 'Hello indie hackers. Ask them to share their latest tool.'),
    ('showcase', 'Hello solo founders. Ask them to comment their startup product and what it does.'),
    ('showcase', 'Hello developers. Ask them to drop their side-project product.'),
    ('showcase', "Hello makers. Ask them to share a useful app; you'll check it out if you need it."),
    ('coding_take', "A blunt playful take in this shape: if you're coding 100% on a laptop you're probably new to coding. One sentence; no explanation."),
    ('coding_take', 'A blunt one-sentence opinion about configuring an editor instead of writing code.'),
    ('coding_take', 'A blunt one-sentence opinion about copying AI code without reading it.'),
    ('coding_take', 'A blunt one-sentence opinion about changing frameworks before finishing a project.'),
    ('coding_take', 'A blunt one-sentence opinion about collecting tutorials instead of shipping code.'),
    ('coding_take', 'A blunt one-sentence opinion about overengineering a tiny side project.'),
)



def daily_plan(day):
    rng = random.Random(int(hashlib.sha256(('founder-originals-v1:'+day).encode()).hexdigest(),16))
    briefs = list(BRIEFS)
    rng.shuffle(briefs)
    midnight = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    # A different minute in every 72-minute band. At least 42 minutes between slots,
    # including the midnight boundary; no clusters or catch-up bursts.
    return [{'slot':f'{day}:{i}', 'due_at':(midnight+timedelta(minutes=i*72+rng.randint(10,40))).isoformat(),
             'category':category,'brief':brief}
            for i,(category,brief) in enumerate(briefs)]


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
        # First launch only. Consume a real slot, so launch cannot create post #21.
        return daily_plan(today)[0]
    return None
