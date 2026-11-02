"""Fifty reproducible daily opportunities with no backlog bursts."""
import hashlib
import random
from datetime import datetime, timedelta, timezone
from .audiences import DAILY_MIX

POSTS_PER_DAY = 50
MIN_GAP = timedelta(minutes=15)
PLAN_VERSION = 'resources-v1'


def daily_plan(day):
    rng = random.Random(int(hashlib.sha256((PLAN_VERSION+day).encode()).hexdigest(),16))
    remaining = dict(DAILY_MIX)
    audiences = []
    while sum(remaining.values()):
        total=sum(remaining.values())
        options = [a for a,n in remaining.items() if n and (not audiences or a!=audiences[-1])
                   and all(count-(key==a)<=((total-1)//2 if key==a else total//2)
                           for key,count in remaining.items())]
        audience = rng.choices(options,weights=[remaining[a] for a in options])[0]
        audiences.append(audience)
        remaining[audience] -= 1
    midnight = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return [{'slot':f'{day}:{PLAN_VERSION}:{i}', 'audience':audience,
             'due_at':(midnight+timedelta(seconds=i*86400//POSTS_PER_DAY,
                                        minutes=rng.randint(11,17))).isoformat()}
            for i,audience in enumerate(audiences)]


def next_due(state,now,start_now=False):
    if state.get('pending'):
        raise ValueError('Unresolved publication reservation; reconcile before continuing')
    today = now.astimezone(timezone.utc).date().isoformat()
    history = state['history']+state.get('legacy_history',[])
    if sum(p['posted_at'][:10]==today for p in history)>=POSTS_PER_DAY:
        return None
    last = state.get('last_posted_at')
    if last and now-datetime.fromisoformat(last)<MIN_GAP:
        return None
    used = {p['slot'] for p in history}
    due = [s for s in daily_plan(today) if datetime.fromisoformat(s['due_at'])<=now]
    if due:
        return None if due[-1]['slot'] in used else due[-1]
    if start_now and not history:
        return daily_plan(today)[0]
    return None
