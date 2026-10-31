"""A reproducible random timetable and content mix, regenerated for each UTC day."""
import hashlib
import random
from datetime import datetime, timedelta, timezone

POSTS_PER_DAY = 20
MIN_GAP = timedelta(minutes=30)
BRIEFS = (
    ('connect', 'Greet early-stage founders and invite a conversation about building before everything looks polished.'),
    ('showcase', 'Invite a one-sentence product description and who it helps. Make trying it conditional on a real need.'),
    ('coding_take', 'A playful, debatable opinion about coding from a laptop versus a phone, remote terminal, or server. Make it personal opinion, not a claim that beginners are inferior.'),
    ('question', 'Ask builders what they cut from an MVP to actually ship it.'),
    ('observation', 'A short witty observation about spending longer choosing a stack than shipping a feature.'),
    ('connect', 'Invite solo founders working after their day job to compare notes.'),
    ('showcase', 'Ask for a tool that removes one annoying repetitive task, plus the task it removes.'),
    ('coding_take', 'An opinion about reading the diff before trusting an AI coding assistant.'),
    ('question', 'Ask what feedback made someone change their product rather than add another feature.'),
    ('observation', 'A playful contrast between a polished landing page and an unfinished core workflow.'),
    ('connect', 'Invite technical and nontechnical cofounders to discuss the hardest handoff in building together.'),
    ('showcase', 'Invite a small open-source side project and the problem it solves. Do not promise to star or promote it.'),
    ('coding_take', 'A debatable opinion about boring dependable software versus rewriting everything with a new framework.'),
    ('question', 'Ask what founders would validate before spending a weekend building again.'),
    ('observation', 'A brief relatable joke about debugging a tiny bug that turned out to be a wrong assumption.'),
    ('connect', 'Greet builders still at their first user and invite an honest conversation without revenue flexes.'),
    ('showcase', 'Invite a scrappy useful prototype that works even though its design is unfinished.'),
    ('coding_take', 'A playful opinion about terminal tools, editor extensions and knowing when to stop configuring.'),
    ('question', 'Ask which small user experience improvement mattered more than a big new feature.'),
    ('observation', 'A witty observation about collecting ideas versus finishing one tiny working product.'),
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
