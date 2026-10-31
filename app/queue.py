import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from twikit import Client
from .writer import generate, validate, DraftError
from .schedule import next_due, daily_plan

PATH=Path('state/community_queue.json')


def load():
    state=json.loads(PATH.read_text())
    if state.get('version')!=3 or not isinstance(state.get('history'),list):
        raise ValueError('Invalid standalone queue state')
    return state


def save(state):
    temp=PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    temp.replace(PATH)


def output(key,value):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:
            f.write(f'{key}={value}\n')


def summary(text):
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write(text+'\n')


def client():
    auth=os.environ.get('X_AUTH_TOKEN','').strip()
    csrf=os.environ.get('X_CT0','').strip()
    if not auth or not csrf:
        raise ValueError('X session cookies required')
    c=Client('en-US',impersonate='chrome124')
    c.set_cookies({'auth_token':auth,'ct0':csrf})
    return c


async def prepare(preview=False,start_now=False):
    output('prepared','false')
    state=load()
    now=datetime.now(timezone.utc)
    plan=daily_plan(now.date().isoformat())
    brief=plan[0] if preview else next_due(state,now,start_now)
    if not brief:
        summary('No post due yet, slot already used, or daily cap reached.')
        return
    try:
        text=generate(brief,state['history'])
    except DraftError as exc:
        output('skip_reason','generation_failed')
        summary('::error::Due post failed generation: '+str(exc))
        raise
    if preview:
        summary('PREVIEW ONLY:\n\n'+text)
        return
    state['pending']={'slot':brief['slot'],'category':brief['category'],'text':text,
        'prepared_at':now.isoformat(),'run_id':os.environ.get('GITHUB_RUN_ID','local'),
        'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT','1')}
    state['plan']={'day':now.date().isoformat(),'slots':plan}
    save(state)
    output('prepared','true')
    summary('Standalone post prepared; awaiting durable reservation.')


async def publish():
    state=load()
    pending=state.get('pending')
    now=datetime.now(timezone.utc)
    if not pending:
        raise ValueError('No reserved post')
    if pending['run_id']!=os.environ.get('GITHUB_RUN_ID','local') or pending['run_attempt']!=os.environ.get('GITHUB_RUN_ATTEMPT','1'):
        raise ValueError('Reservation belongs to another run; reconcile manually')
    if not timedelta(0)<=now-datetime.fromisoformat(pending['prepared_at'])<=timedelta(minutes=20):
        raise ValueError('Reservation expired; inspect before clearing')
    if pending['slot'].split(':')[0]!=now.date().isoformat():
        raise ValueError('Day changed; reconcile reservation')
    text=validate(pending['text'],state['history'])
    # Exactly one attempt. A timeout can mean publication succeeded.
    tweet=await client().create_tweet(text=text)
    ident=str(getattr(tweet,'id','') or '')
    if not ident.isdigit():
        raise ValueError('Missing post ID; inspect X before clearing reservation')
    state['history'].append({'slot':pending['slot'],'category':pending['category'],
        'text':text,'tweet_id':ident,'posted_at':now.isoformat()})
    state['history']=state['history'][-1000:]
    state['last_posted_at']=now.isoformat()
    state['pending']=None
    save(state)
    url=f'https://x.com/i/status/{ident}'
    output('published','true')
    output('post_url',url)
    summary('Published '+url)
