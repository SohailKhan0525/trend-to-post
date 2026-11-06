"""Discover, verify, reserve and publish one original quote."""
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
from .cloudflare import DraftError
from .schedule import next_slot

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


def require_open(state):
    if state.get("pending"):
        raise ValueError("Unconfirmed send requires reconciliation")
    if state.get("pause_reason"):
        raise ValueError("Quote delivery is paused for inspection")


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


async def publish_slot(state,slot,connection):
    require_open(state)
    if state.get("check_session"):
        if not await connection.is_logged_in():
            raise SessionUnavailable()
        state["check_session"]=False
    candidates=await sources.discover(connection,state,utcnow())
    for cursor,candidate in candidate_batch(state,candidates):
        # Persisted by the existing rejection or reservation write. Editorial
        # failures stay eligible for a future review instead of being consumed.
        state["candidate_cursor"]=cursor
        try:
            draft=writer.generate(candidate,state,utcnow())
        except DraftError as exc:
            summary(f"Candidate skipped by news, wording or duplicate checks: {str(exc)[:600]}")
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
    reject(state,slot,"No new verified announcement with at least 100,000 source-post views passed this check.")


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
    writer.validate(draft,source)
    requested={"used_source_ids":source["source_ids"],"used_urls":source["urls"],
               "used_texts":[writer.fingerprint(draft["text"])],
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
                               "source_url":source["url"],"source_views":source["views"],
                               "source_checked_at":now.isoformat(),"source_created_at":source["created_at"],
                               "tweet_id":ident,"posted_at":posted_at})
    durable["history"]=durable["history"][-1000:]
    durable["last_posted_at"]=posted_at
    durable["pending"]=None
    durable["verification_pending"]={"tweet_id":ident,"source_id":source["id"]}
    save_and_push(durable)
    summary(f"Published Qofeno quote · {source['views']:,} source views · https://x.com/i/status/{ident}")
    await verify_quote(connection,durable)


async def inspect():
    state=load()
    require_open(state)
    conn=client()
    candidates=await sources.discover(conn,state,utcnow())
    summary(f"Eligible source candidates found: {len(candidates)}")
    for _,candidate in candidate_batch(state,candidates):
        summary(f"Source · {candidate['author']} · {candidate['views']:,} views · {candidate['url']}")
        try:
            draft=writer.generate(candidate,state,utcnow())
        except DraftError as exc:
            summary(f"This candidate did not pass editorial review: {str(exc)[:600]}")
            continue
        summary("Inspection draft\n\n"+draft["text"])
        return


async def run(event):
    start=utcnow()
    state=load()
    require_open(state)
    connection=client()
    if not await verify_quote(connection,state):
        return
    window=None
    if event=="repository_dispatch":
        window=start.replace(minute=start.minute//30*30,second=0,microsecond=0)+timedelta(minutes=30)
    for _ in range(2 if window else 1):
        state=load()
        require_open(state)
        found=next_slot(state,utcnow(),window)
        if not found:
            summary("No eligible quote opportunity in this wake.")
            return
        slot,ready=found
        wait=(ready-utcnow()).total_seconds()
        if wait>0:
            await asyncio.sleep(wait)
        await publish_slot(state,slot,connection)
