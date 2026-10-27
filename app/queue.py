import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from twikit import Client
from .sources import discover
from .writer import generate, validate

PATH=Path('state/post_queue.json')


def load():
    state=json.loads(PATH.read_text())
    if state.get('version') != 2 or not isinstance(state.get('history'),list) or not isinstance(state.get('used_source_ids'),list):
        raise ValueError('Invalid queue state; restore from Git history')
    return state


def save(state):
    temp=PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    temp.replace(PATH)


def output(key,value):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f: f.write(f'{key}={value}\n')


def slot(now):
    return f'{now.date().isoformat()}:{(now.hour*60+now.minute)//144}'


def eligible(state,now):
    if state.get('pending'):
        raise ValueError('Unresolved publication reservation. Inspect X and reconcile state before continuing.')
    today=[p for p in state['history'] if p['posted_at'][:10]==now.date().isoformat()]
    if len(today)>=10 or any(p['slot']==slot(now) for p in today): return False
    last=state.get('last_posted_at')
    if last and now-datetime.fromisoformat(last)<timedelta(minutes=90): return False
    return True


def client():
    auth=os.environ.get('X_AUTH_TOKEN','').strip()
    csrf=os.environ.get('X_CT0','').strip()
    if not auth or not csrf: raise ValueError('X_AUTH_TOKEN and X_CT0 are required')
    c=Client('en-US',impersonate='chrome124')
    c.set_cookies({'auth_token':auth,'ct0':csrf})
    return c


async def prepare():
    output('prepared','false')
    now=datetime.now(timezone.utc)
    state=load()
    if not eligible(state,now):
        print('Slot already used or minimum interval not reached.'); return
    for name in ('CLOUDFLARE_ACCOUNT_ID','CLOUDFLARE_API_TOKEN'):
        if not os.environ.get(name,'').strip(): raise ValueError(f'{name} is required')
    c=client()
    if not await c.is_logged_in(): raise ValueError('X session expired; update repository secrets')
    sources=await discover(c,set(state['used_source_ids']),state['history'],now)
    for source in sources[:2]:
        try: text=generate(source,state['history'])
        except (ValueError,json.JSONDecodeError):
            print('AI candidate rejected by content validation.'); continue
        if not text: continue
        state['pending']={'slot':slot(now),'source':source,'text':text,'prepared_at':now.isoformat(),
                          'run_id':os.environ.get('GITHUB_RUN_ID','local'),
                          'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT','1')}
        save(state)
        output('prepared','true')
        print('Quote prepared. Publication requires a durable Git reservation.')
        return
    print('No eligible source or valid draft; skipped rather than filling with stale content.')
    output('skip_reason','no_valid_draft')


async def publish():
    state=load(); pending=state.get('pending')
    now=datetime.now(timezone.utc)
    if not pending: raise ValueError('No publication reservation')
    if pending['run_id']!=os.environ.get('GITHUB_RUN_ID','local') or pending['run_attempt']!=os.environ.get('GITHUB_RUN_ATTEMPT','1'):
        raise ValueError('Reservation belongs to another run; reconcile manually')
    if now-datetime.fromisoformat(pending['prepared_at'])>timedelta(minutes=20):
        raise ValueError('Reservation expired; reconcile manually')
    if pending['slot']!=slot(now): raise ValueError('Slot changed; reconcile manually')
    text=validate(json.dumps({'should_post':True,'text':pending['text']}),pending['source'],state['history'])
    c=client()
    # Do not retry this call: a timeout may mean X published successfully.
    tweet=await c.create_tweet(text=text,attachment_url=pending['source']['url'])
    ident=str(getattr(tweet,'id','') or '')
    if not ident.isdigit(): raise ValueError('X returned no post ID; inspect X before clearing reservation')
    state['history'].append({'slot':pending['slot'],'text':text,'source_id':pending['source']['id'],
        'author':pending['source']['author'],'tweet_id':ident,'posted_at':now.isoformat()})
    state['history']=state['history'][-500:]
    state['used_source_ids']=(state['used_source_ids']+[pending['source']['id']])[-5000:]
    state['last_posted_at']=now.isoformat();state['pending']=None
    save(state)
    url=f'https://x.com/i/status/{ident}'
    output('published','true');output('post_url',url)
    print('Published '+url)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f: f.write(f'Published [{ident}]({url})\n')
