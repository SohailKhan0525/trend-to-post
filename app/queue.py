"""Publish a verified quote and, when approved, one durable optional self-reply."""
import asyncio
import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from twikit import Client
from twikit.errors import InvalidSession, TweetNotAvailable
from . import sources, writer
from .cloudflare import DraftError, selected_model
from .ai_budget import AIPaused, active_pause, budget_scope
from .schedule import next_slot
from .policy import MIN_SOURCE_VIEWS

STATE = Path("state/qofeno.json")
LEGACY = Path("state/dash_rattle.json")
OLDER_QUOTES = Path("state/post_queue.json")


class SessionUnavailable(Exception):
    """The account session needs a fresh check before another send."""


def utcnow():
    return datetime.now(timezone.utc)


def legacy_ledgers(*states):
    """Keep known publication identities when replacing earlier publishers."""
    result={key:set() for key in ("used_source_ids","used_urls","used_events","used_texts")}

    def values(value):
        return value if isinstance(value,list) else []

    def source_id(value):
        if not isinstance(value,bool) and isinstance(value,(int,str)):
            text=str(value)
            if text.isascii() and text.isdigit() and int(text)>0:
                result["used_source_ids"].add(str(int(text)))

    def article_url(value):
        if isinstance(value,dict):
            value=value.get("unwound_url") or value.get("expanded_url") or value.get("url")
        canonical=sources.canonical_url(value)
        if canonical:
            result["used_urls"].add(canonical)

    def identities(record, *, nested=False):
        if not isinstance(record,dict):
            return
        source_id(record.get("source_id"))
        if nested:
            source_id(record.get("id"))
        for key in ("source_ids","edit_tweet_ids"):
            for ident in values(record.get(key)):
                source_id(ident)
        control=record.get("edit_control")
        if isinstance(control,dict):
            for ident in values(control.get("edit_tweet_ids")):
                source_id(ident)
        for key in ("urls","article_urls","source_urls"):
            for url in values(record.get(key)):
                article_url(url)
        for key in ("url","article_url","source_url"):
            article_url(record.get(key))

    for previous in states:
        for ident in values(previous.get("used_source_ids")):
            source_id(ident)
        for url in values(previous.get("used_urls")):
            article_url(url)
        for key in ("used_texts","used_events"):
            result[key].update(value for value in values(previous.get(key))
                               if isinstance(value,str) and value.strip())
        for row in values(previous.get("history")):
            if not isinstance(row,dict):
                continue
            identities(row)
            identities(row.get("source"),nested=True)
            for field,ledger in (("text","used_texts"),("event_key","used_events")):
                if isinstance(row.get(field),str) and row[field].strip():
                    result[ledger].add(writer.fingerprint(row[field]))
    return {key:sorted(items) for key,items in result.items()}


def load():
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if state.get("version")!=1 or any(not isinstance(state.get(k),list) for k in
                ("history","used_source_ids","used_urls","used_events","used_texts")):
            raise ValueError("Invalid quote ledger")
        return state
    old = json.loads(LEGACY.read_text()) if LEGACY.exists() else {}
    if old.get("pending") or old.get("pause_reason"):
        raise ValueError("Previous publisher requires reconciliation")
    older = json.loads(OLDER_QUOTES.read_text()) if OLDER_QUOTES.exists() else {}
    return {"version":1,"history":[],**legacy_ledgers(old,older),
            "last_posted_at":old.get("last_posted_at"),"pending":None,
            "check_session":old.get("check_session",False)}


def save_and_push(state):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n")
    temp.replace(STATE)
    subprocess.run(["bash","scripts/commit-state.sh"],check=True)


def summary(message):
    print(message,flush=True)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target,"a") as stream:
            stream.write(message+"\n\n")


def client():
    auth = os.environ.get("X_AUTH_TOKEN","").strip()
    csrf = os.environ.get("X_CT0","").strip()
    if not auth or not csrf:
        raise SessionUnavailable()
    connection = Client("en-US",impersonate="chrome124")
    connection.set_cookies({"auth_token":auth,"ct0":csrf})
    return connection


def run_key():
    return [os.environ["GITHUB_RUN_ID"],os.environ["GITHUB_RUN_ATTEMPT"]]


def require_open(state, *, allow_reply=False):
    if state.get("pending"):
        raise ValueError("Unconfirmed send requires reconciliation")
    if state.get("pause_reason"):
        raise ValueError("Quote delivery is paused for inspection")
    if state.get("pending_reply") and not allow_reply:
        raise ValueError("A self-reply must finish or be reconciled before another quote")


def seal(slot, source, draft):
    body = json.dumps(["qofeno-v1",slot,source,draft],sort_keys=True,ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def unchanged(before, after):
    fields = ("id","author","author_id","text","created_at","urls","source_ids")
    return all(before.get(k)==after.get(k) for k in fields)


def reject(state, slot, reason):
    state["last_attempt"]={"slot":slot["slot"],"at":utcnow().isoformat(),"reason":reason}
    save_and_push(state)
    summary(reason)


async def verify_quote(connection,state):
    check=state.get("verification_pending")
    if not check:
        return True
    try:
        post=await asyncio.wait_for(connection.get_tweet_by_id(check["tweet_id"]),timeout=20)
    except Exception as exc:
        summary(f"Quote ID recorded. Source-attachment verification is pending ({type(exc).__name__}).")
        return False
    quoted=str(getattr(post,"quoted_status_id","") or "")
    if not quoted:
        try:
            quoted=str(getattr(getattr(post,"quote",None),"id","") or "")
        except (AttributeError,KeyError,TypeError,ValueError):
            # The optional nested source can be absent or trimmed in readback.
            quoted=""
    if not quoted:
        summary("Quote ID recorded. Source-attachment metadata is not available yet; verification remains pending.")
        return False
    if quoted!=check["source_id"]:
        state["pause_reason"]="Confirmed post does not embed the expected source"
        save_and_push(state)
        raise ValueError("Unexpected quote attachment")
    state.pop("verification_pending",None)
    save_and_push(state)
    summary("X readback confirmed the intended quoted source.")
    try:
        handle = getattr(getattr(post,"user",None),"screen_name",None)
        if isinstance(handle,str) and handle.isascii() and 1<=len(handle)<=15 and handle.replace("_","").isalnum():
            summary(f"Published account verified by X readback: @{handle}")
    except (AttributeError,KeyError,TypeError,ValueError):
        pass
    return True


def reply_seal(quote_id, text, planned_at):
    return hashlib.sha256(json.dumps(["qofeno-reply-v1",quote_id,text,planned_at]).encode()).hexdigest()


def reply_history(state, pending):
    rows=[row for row in state['history'] if row.get('tweet_id')==pending['quote_id']]
    if len(rows)!=1 or rows[0].get('reply')!=pending['text']:
        raise ValueError('Self-reply does not match a confirmed own quote')
    if pending['approval']!=reply_seal(pending['quote_id'],pending['text'],pending['planned_at']):
        raise ValueError('Self-reply reservation changed')
    return rows[0]


def archive_reply(state, pending, status, error_type=None):
    """Keep uncertainty durable without ever scheduling the optional reply again."""
    if state.get('pending_reply')!=pending:
        raise ValueError('Self-reply reservation changed')
    row=reply_history(state,pending)
    evidence={'reservation':pending,'status':status,'archived_at':utcnow().isoformat()}
    if error_type:
        evidence['error_type']=error_type
    state.setdefault('uncertain_replies',[]).append(evidence)
    row['reply_status']=status
    state.pop('pending_reply',None)
    # Archive + removal of the active item are one durable state commit. Failure
    # aborts this wake; the next wake can archive the old reservation, never send it.
    save_and_push(state)
    summary('Optional self-reply archived for inspection; it will not be resent. Future quotes may continue.')


def reply_verification_expired(state, pending):
    row=reply_history(state,pending)
    return utcnow()-datetime.fromisoformat(row['reply_posted_at'])>timedelta(hours=1)


async def finish_reply(connection,state):
    """Send only a proven-unsent plan; an ambiguous reserved send is never repeated."""
    pending=state.get('pending_reply')
    if not pending:
        return True
    require_open(state,allow_reply=True)
    if state.get('verification_pending'):
        return False
    row=reply_history(state,pending)
    status=pending.get('status')
    if status=='reserved':
        archive_reply(state,pending,'unconfirmed','InterruptedSend')
        return True
    if status=='confirmed':
        try:
            post=await asyncio.wait_for(connection.get_tweet_by_id(pending['reply_id']),timeout=20)
        except Exception as exc:
            if reply_verification_expired(state,pending):
                archive_reply(state,pending,'verification_pending',type(exc).__name__)
                return True
            summary(f"Self-reply ID recorded. Parent verification is pending ({type(exc).__name__}).")
            return False
        parent=str(getattr(post,'in_reply_to','') or '')
        if not parent:
            if reply_verification_expired(state,pending):
                archive_reply(state,pending,'verification_pending','MissingParentMetadata')
                return True
            summary('Self-reply ID recorded. Parent metadata is not available yet; no resend attempted.')
            return False
        if str(getattr(post,'id',''))!=pending['reply_id'] or parent!=pending['quote_id']:
            state['pause_reason']='Confirmed self-reply does not belong to the expected own quote'
            save_and_push(state)
            raise ValueError('Unexpected self-reply parent')
        row['reply_status']='verified'
        state.pop('pending_reply',None)
        save_and_push(state)
        summary('X readback confirmed the self-reply belongs to the intended Qofeno quote.')
        return True
    if status!='planned':
        raise ValueError('Unknown self-reply state')
    age=utcnow()-datetime.fromisoformat(pending['planned_at'])
    if not timedelta(0)<=age<=timedelta(hours=1):
        row['reply_status']='expired_unsent'
        state.pop('pending_reply',None)
        save_and_push(state)
        summary('Optional self-reply expired before sending. The confirmed quote remains published.')
        return True
    if state.get('check_session'):
        if not await connection.is_logged_in():
            raise SessionUnavailable()
        state['check_session']=False
    validated=writer.validate(pending['draft'],pending['source'])
    if validated.get('reply')!=pending['text'] or row.get('reply_id'):
        raise ValueError('Self-reply approval changed or already has a confirmed ID')
    reserved={**pending,'status':'reserved','reserved_at':utcnow().isoformat(),'run':run_key()}
    state['pending_reply']=reserved
    row['reply_status']='reserved'
    save_and_push(state)
    durable=load()
    if durable.get('pending_reply')!=reserved or reserved['run']!=run_key():
        raise ValueError('Self-reply reservation changed')
    # Like quote sends, stale reservations are known-unsent before create_tweet.
    send_at=utcnow()
    wait=send_at-datetime.fromisoformat(reserved['reserved_at'])
    age=send_at-datetime.fromisoformat(reserved['planned_at'])
    if not timedelta(0)<=wait<=timedelta(seconds=90) or not timedelta(0)<=age<=timedelta(hours=1):
        reply_history(durable,reserved)['reply_status']='expired_unsent'
        durable.pop('pending_reply',None)
        save_and_push(durable)
        return True
    try:
        post=await connection.create_tweet(text=reserved['text'],reply_to=reserved['quote_id'])
    except InvalidSession:
        durable['pending_reply']=pending
        reply_history(durable,pending)['reply_status']='planned'
        durable['check_session']=True
        save_and_push(durable)
        raise SessionUnavailable() from None
    except Exception as exc:
        archive_reply(durable,reserved,'unconfirmed',type(exc).__name__)
        return True
    ident=str(getattr(post,'id','') or '')
    if not ident.isdigit():
        archive_reply(durable,reserved,'unconfirmed','MissingPostID')
        return True
    durable['pending_reply']={**reserved,'status':'confirmed','reply_id':ident}
    reply_history(durable,reserved).update(reply_status='confirmed',reply_id=ident,reply_posted_at=utcnow().isoformat())
    save_and_push(durable)
    summary(f"Published Qofeno self-reply · https://x.com/i/status/{ident}")
    return await finish_reply(connection,durable)


def candidate_batch(state,candidates):
    """Rotate a bounded review window so rejected leaders cannot starve later news."""
    if not candidates:
        return []
    last_author=state["history"][-1].get("author") if state["history"] else None
    ranked=sorted(candidates,key=lambda row:row["author"]==last_author)
    cursor=state.get("candidate_cursor",0)
    if type(cursor) is not int or cursor<0:
        cursor=0
    start=cursor%len(ranked)
    return [((start+offset+1)%len(ranked),ranked[(start+offset)%len(ranked)])
            for offset in range(min(4,len(ranked)))]


def review_key(source):
    evidence = [writer.EDITORIAL_POLICY_VERSION, source['id'], source['text'],
                source.get('author'), source.get('urls'), source.get('source_ids')]
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def unreviewed_candidates(state, candidates, now):
    cached = state.get('editorial_rejections', {})
    state['editorial_rejections'] = cached = {
        key: row for key, row in cached.items()
        if row.get('policy') == writer.EDITORIAL_POLICY_VERSION
        and datetime.fromisoformat(row['until']) > now}
    available = [row for row in candidates if review_key(row) not in cached]
    if len(available) < len(candidates):
        summary(f"Saved AI calls: {len(candidates)-len(available)} unchanged candidates already failed editorial review.")
    return available


def remember_rejection(state, candidate, error, now):
    expires = datetime.fromisoformat(candidate['created_at'].replace('Z', '+00:00')) + timedelta(hours=24)
    if not isinstance(error, writer.SourceRejected):
        expires = min(expires, now + timedelta(hours=6))
    state.setdefault('editorial_rejections', {})[review_key(candidate)] = {
        'policy': writer.EDITORIAL_POLICY_VERSION, 'until': expires.isoformat(),
        'reason': str(error)[:200]}


def report_pause(state):
    pause = active_pause(state, utcnow())
    if pause:
        summary(f"AI paused until {pause.until.isoformat()}: {pause.reason} No post sent.")
        return True
    return False


async def publish_slot(state,slot,connection):
    require_open(state)
    if report_pause(state):
        return
    if state.get("check_session"):
        if not await connection.is_logged_in():
            raise SessionUnavailable()
        state["check_session"]=False
    candidates=unreviewed_candidates(state,await sources.discover(connection,state,utcnow()),utcnow())
    summary('Workers AI model: '+selected_model())
    for cursor,candidate in candidate_batch(state,candidates):
        # Editorial cooldowns are separate from publication/deduplication claims.
        state["candidate_cursor"]=cursor
        try:
            with budget_scope(state,save_and_push,utcnow):
                draft=writer.generate(candidate,state,utcnow())
        except AIPaused as exc:
            reject(state,slot,f"AI paused until {exc.until.isoformat()}: {exc.reason} No post sent.")
            return
        except DraftError as exc:
            remember_rejection(state,candidate,exc,utcnow())
            summary(f"Candidate skipped by topic, wording or duplicate checks: {str(exc)[:600]}")
            continue
        try:
            fresh=sources.snapshot(await connection.get_tweet_by_id(candidate["id"]),utcnow())
        except (sources.SourceError,TweetNotAvailable):
            summary("Candidate no longer meets the source rules.")
            continue
        if not unchanged(candidate,fresh):
            summary("Source changed during writing. It needs a new review.")
            continue
        if set(fresh["source_ids"]) & set(state["used_source_ids"]) or set(fresh["urls"]) & set(state["used_urls"]):
            continue
        return await send_once(state,slot,fresh,draft,connection)
    reject(state,slot,f"No fresh, distinct source with at least {MIN_SOURCE_VIEWS:,} post views passed this check.")


def release_unsent(state,pending):
    """Release only this reservation's additions after a proven pre-send exit."""
    if state.get("pending")!=pending:
        raise ValueError("Quote reservation changed")
    for ledger,added in pending["added"].items():
        additions=set(added)
        state[ledger]=[value for value in state[ledger] if value not in additions]
    state["pending"]=None


async def send_once(state,slot,source,draft,connection):
    require_open(state)
    now=utcnow()
    eligible=next_slot(state,now)
    if not eligible or eligible[0]["slot"]!=slot["slot"]:
        reject(state,slot,"Opportunity expired during research. Skipped.")
        return
    sources.validate(source,now)
    draft=writer.validate(draft,source)
    text_claims=[] if draft.get('format') in ('emoji','emoji_question') else [writer.fingerprint(draft['text'])]
    if draft.get('reply'):
        text_claims.append(writer.fingerprint(draft['reply']))
    requested={"used_source_ids":source["source_ids"],"used_urls":source["urls"],
               "used_texts":text_claims,
               "used_events":[writer.fingerprint(draft["event_key"])]}
    added={key:sorted(set(values)-set(state[key])) for key,values in requested.items()}
    pending={"slot":slot,"source":source,"draft":draft,"reserved_at":now.isoformat(),
             "approval":seal(slot,source,draft),"run":run_key(),"added":added}
    state["pending"]=pending
    for ledger,values in added.items():
        state[ledger].extend(values)
    save_and_push(state)
    durable=load()
    if durable.get("pending")!=pending or pending["run"]!=run_key() or pending["approval"]!=seal(slot,source,draft):
        raise ValueError("Quote reservation changed")
    send_at=utcnow()
    if send_at.date()!=now.date() or not timedelta(0)<=send_at-now<=timedelta(seconds=90):
        release_unsent(durable,pending)
        save_and_push(durable)
        summary("Source check expired before sending. Skipped.")
        return
    try:
        sources.validate(source,send_at)
    except sources.SourceError:
        release_unsent(durable,pending)
        save_and_push(durable)
        summary("Source expired before sending. Skipped.")
        return
    try:
        post=await connection.create_tweet(text=draft["text"],attachment_url=source["url"])
    except InvalidSession:
        # In the pinned client this happens before transmitting create_tweet.
        release_unsent(durable,pending)
        durable["check_session"]=True
        save_and_push(durable)
        raise SessionUnavailable() from None
    ident=str(getattr(post,"id","") or "")
    if not ident.isdigit():
        raise ValueError("Unconfirmed send; quote reservation retained")
    posted_at=utcnow().isoformat()
    durable["history"].append({**slot,**draft,"source_id":source["id"],"author":source["author"],
                               "editorial_version":writer.EDITORIAL_POLICY_VERSION,
                               "body_style":('self_reply_question' if draft.get('reply') else
                                             'emoji' if draft.get('format')=='emoji' else
                                             'question' if '?' in draft['text'].split('\n\n')[-1] else 'detail'),
                               "source_url":source["url"],"source_views":source["views"],
                               "source_checked_at":now.isoformat(),"source_created_at":source["created_at"],
                               "tweet_id":ident,"posted_at":posted_at})
    durable["history"]=durable["history"][-1000:]
    durable["last_posted_at"]=posted_at
    durable["pending"]=None
    durable["verification_pending"]={"tweet_id":ident,"source_id":source["id"]}
    if draft.get('reply'):
        durable['pending_reply']={'quote_id':ident,'text':draft['reply'],'status':'planned',
                                  'planned_at':posted_at,'draft':draft,'source':source,
                                  'approval':reply_seal(ident,draft['reply'],posted_at)}
        durable['history'][-1]['reply_status']='planned'
    save_and_push(durable)
    summary(f"Published Qofeno quote · {source['views']:,} source views · https://x.com/i/status/{ident}")
    if await verify_quote(connection,durable):
        await finish_reply(connection,durable)


async def inspect():
    state=load()
    require_open(state)
    if report_pause(state):
        return
    conn=client()
    candidates=unreviewed_candidates(state,await sources.discover(conn,state,utcnow()),utcnow())
    summary(f"Eligible source candidates found: {len(candidates)}")
    for _,candidate in candidate_batch(state,candidates):
        summary(f"Source · {candidate['author']} · {candidate['views']:,} views · {candidate['url']}")
        try:
            with budget_scope(state,save_and_push,utcnow):
                draft=writer.generate(candidate,state,utcnow())
        except DraftError as exc:
            remember_rejection(state,candidate,exc,utcnow())
            save_and_push(state)
            summary(f"This candidate did not pass editorial review: {str(exc)[:600]}")
            continue
        save_and_push(state)
        summary("Inspection draft\n\n"+draft["text"])
        return


async def run(event):
    start=utcnow()
    state=load()
    require_open(state,allow_reply=True)
    connection=client()
    if not await verify_quote(connection,state):
        return
    if not await finish_reply(connection,state):
        return
    state=load()
    if report_pause(state):
        return
    window=None
    if event=="repository_dispatch":
        window=start.replace(minute=start.minute//30*30,second=0,microsecond=0)+timedelta(minutes=30)
    for _ in range(2 if window else 1):
        state=load()
        require_open(state)
        if report_pause(state):
            return
        found=next_slot(state,utcnow(),window)
        if not found:
            summary("No eligible quote opportunity in this wake.")
            return
        slot,ready=found
        wait=(ready-utcnow()).total_seconds()
        if wait>0:
            await asyncio.sleep(wait)
        await publish_slot(state,slot,connection)
