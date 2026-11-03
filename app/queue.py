"""Durable, exactly-once-attempt resource publishing with permanent URL claims."""
import json
import os
import re
from datetime import datetime,timezone,timedelta
from pathlib import Path
from twikit import Client
from .writer import generate,render,review,approval_digest,DraftError
from .schedule import next_due,daily_plan
from .resources import find_pair,inspect,source_for
from .http import canonical,LinkError

PATH=Path('state/resources_queue.json')
LEGACY_PATH=Path('state/community_queue.json')
RETIRED_PATH=Path('state/post_queue.json')
# twifork calls its card_uri argument poll_uri. This removal sentinel is not a
# poll: it tells X to omit the automatically generated website card.
NO_LINK_PREVIEW='tombstone://card'


def website_card_present(tweet):
    data=getattr(tweet,'_data',None)
    if isinstance(data,dict):
        card=data.get('card')
        if not card:return False
        name=card.get('legacy',{}).get('name','') if isinstance(card,dict) else ''
        return name not in {'tombstone','tombstone://card'}
    value=getattr(tweet,'has_card',None)
    if type(value) is bool:return value
    raise ValueError('Website card metadata unavailable')


def new_state(legacy=None):
    legacy=legacy or {}
    return {'version':4,'history':[],'legacy_history':legacy.get('history',[]),
            'last_posted_at':legacy.get('last_posted_at'),'pending':None,
            'used_urls':[],'used_fingerprints':[],'catalog':[],'discovery_cursors':{}}


def load():
    legacy=json.loads(LEGACY_PATH.read_text()) if LEGACY_PATH.exists() else {}
    if legacy.get('pending'):
        raise ValueError('Retired workflow has an unresolved reservation; reconcile X first')
    if PATH.exists():
        state=json.loads(PATH.read_text())
    else:
        state=new_state(legacy)
        # Carry forward known old outbound URLs. Legacy files remain untouched.
        old_files=[p.read_text() for p in (LEGACY_PATH,RETIRED_PATH) if p.exists()]
        for url in re.findall(r'https://[^\s"\\<>]+',' '.join(old_files)):
            try:state['used_urls'].append(canonical(url.rstrip('.,)')))
            except (ValueError,LinkError):pass
        state['used_urls']=sorted(set(state['used_urls']))
    if state.get('version')!=4 or not isinstance(state.get('history'),list) or not isinstance(state.get('used_urls'),list):
        raise ValueError('Invalid resource queue state')
    return state


def save(state):
    PATH.parent.mkdir(parents=True,exist_ok=True)
    temp=PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    temp.replace(PATH)


def output(key,value):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as stream:stream.write(f'{key}={value}\n')


def summary(text):
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as stream:stream.write(text+'\n')


def client():
    auth=os.environ.get('X_AUTH_TOKEN','').strip()
    csrf=os.environ.get('X_CT0','').strip()
    if not auth or not csrf:raise ValueError('X session cookies required')
    connection=Client('en-US',impersonate='chrome124')
    connection.set_cookies({'auth_token':auth,'ct0':csrf})
    return connection


def claim(state,resources):
    state['used_urls']=sorted(set(state['used_urls']).union(*(set(r['aliases']) for r in resources)))
    state['used_fingerprints']=sorted(set(state['used_fingerprints']).union(r['fingerprint'] for r in resources))


async def prepare(preview=False,start_now=False,all_categories=False):
    output('prepared','false');output('state_changed','false')
    state=load();now=datetime.now(timezone.utc)
    if state.get('pause_reason'):
        raise ValueError('Publishing paused after an unexpected website card; inspect before resuming')
    plan=daily_plan(now.date().isoformat())
    briefs=[plan[0]] if preview else [next_due(state,now,start_now)]
    if preview and all_categories:
        briefs=list({b['audience']:b for b in reversed(plan)}.values())
    for brief in briefs:
        if not brief:
            summary('No resource post due yet, slot used, or daily cap reached.');return
        excluded=set()
        for pair_attempt in range(3):
            resources=find_pair(state,brief['audience'],brief['slot'],exclude_urls=excluded)
            if not resources:
                if preview:raise LinkError('Not enough working unused links for the preview audience')
                save(state);output('state_changed','true');output('skip_reason','fresh_link_pool_low')
                summary('::warning::Not enough verified unused links for this audience; no post sent.');return
            try:
                labels=generate(brief['audience'],resources)
                text=render(brief['audience'],labels,resources)
                break
            except DraftError as exc:
                # Nothing has been reserved or sent. Try different pages, but
                # retain all review rules and the finite per-run attempt budget.
                excluded.update(set().union(*(set(r['aliases']) for r in resources)))
                state['catalog']=[c for c in state['catalog'] if canonical(c['url']) not in excluded]
                summary(f'Resource pair {pair_attempt+1}/3 rejected before reservation: {exc}')
        else:
            if not preview:
                save(state);output('state_changed','true');output('skip_reason','generation_exhausted')
            summary('::error::No approved post after three different resource pairs; nothing sent.')
            raise DraftError('Resource pair attempts exhausted')
        if preview:
            summary('PREVIEW ONLY ('+brief['audience']+'):\n\n'+text+'\n')
            # Preview claims are in-memory only, so previews do not consume real URLs.
            claim(state,resources)
            continue
        claim(state,resources)
        state['pending']={'slot':brief['slot'],'audience':brief['audience'],'labels':labels,
            'resources':resources,'text':text,'approval':approval_digest(brief['audience'],labels,resources),
            'prepared_at':now.isoformat(),'run_id':os.environ.get('GITHUB_RUN_ID','local'),
            'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT','1')}
        save(state);output('state_changed','true');output('prepared','true')
        summary('Two checked links reserved; awaiting durable state before publishing.')


async def publish():
    output('published','false');output('state_changed','false')
    state=load();pending=state.get('pending');now=datetime.now(timezone.utc)
    if not pending:raise ValueError('No reserved resource post')
    if pending['run_id']!=os.environ.get('GITHUB_RUN_ID','local') or pending['run_attempt']!=os.environ.get('GITHUB_RUN_ATTEMPT','1'):
        raise ValueError('Reservation belongs to another run; reconcile before retrying')
    if not timedelta(0)<=now-datetime.fromisoformat(pending['prepared_at'])<=timedelta(minutes=20):
        raise ValueError('Reservation expired; reconcile before retrying')
    if pending['slot'].split(':')[0]!=now.date().isoformat():raise ValueError('Reservation crossed the UTC day')
    audience,labels,resources=pending['audience'],pending['labels'],pending['resources']
    text=render(audience,labels,resources)
    if text!=pending['text'] or pending['approval']!=approval_digest(audience,labels,resources):
        raise ValueError('Reserved text or reviewed resources were modified')
    try:
        fresh=[inspect(r['url'],source_for(r['source_id'])) for r in resources]
        for old,new in zip(resources,fresh):
            if canonical(old['url'])!=canonical(new['url']) or not set(new['aliases']).issubset(old['aliases']):
                raise LinkError('Resource destination changed after reservation')
        review(audience,labels,fresh)
    except (LinkError,DraftError,OSError,ValueError):
        # This path is known NOT to have contacted X. Burn the URL claims and
        # release the reservation safely; unknown outcomes after create_tweet do not.
        state['pending']=None;save(state);output('state_changed','true')
        summary('::warning::Link/content recheck failed before sending; reservation released, URLs remain retired.');return
    # Exactly one outbound attempt. Timeouts and all uncertain outcomes keep pending.
    connection=client()
    tweet=await connection.create_tweet(text=text,poll_uri=NO_LINK_PREVIEW)
    ident=str(getattr(tweet,'id','') or '')
    if not ident.isdigit():raise ValueError('No real post ID; inspect X before clearing the reservation')
    state['history'].append({'slot':pending['slot'],'audience':audience,'text':text,
        'resources':resources,'tweet_id':ident,'posted_at':now.isoformat(),
        'website_card_requested':False,'website_card_status':'unverified'})
    state['history']=state['history'][-1000:]
    # Permanent URL and content ledgers are NEVER trimmed with the post history.
    state['last_posted_at']=now.isoformat();state['pending']=None;save(state)
    output('state_changed','true');output('published','true');output('post_url','https://x.com/i/status/'+ident)
    summary('Published https://x.com/i/status/'+ident)
    # A read failure must never turn a successful publication into another send.
    # The real post ID is saved and exposed to the persistence step before reading.
    try:
        confirmed=await connection.get_tweet_by_id(ident)
        has_card=website_card_present(confirmed)
        state['history'][-1]['website_card_status']='present' if has_card else 'absent'
        if has_card:
            state['pause_reason']='X returned a website card despite explicit suppression'
            summary('::warning::Unexpected website card detected. Future publishing paused; the confirmed post will not be resent.')
        else:
            summary('X readback verified: no website preview card.')
        save(state)
    except Exception as exc:
        summary('::warning::Post confirmed; preview readback unavailable ('+type(exc).__name__+'). No resend attempted.')
