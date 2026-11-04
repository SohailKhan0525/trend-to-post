"""Durably reserve, send once, and confirm each scheduled original."""
import asyncio
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from twikit import Client
from twikit.errors import InvalidSession
from . import writer
from .schedule import daily_plan, next_slot

STATE = Path("state/dash_rattle.json")
RESOURCES = Path("state/resources_queue.json")
COMMUNITY = Path("state/community_queue.json")


class SessionUnavailable(Exception):
    """X has not accepted the stored browser session."""


def utcnow():
    return datetime.now(timezone.utc)


def load():
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if state.get("version") != 1 or not all(isinstance(state.get(k),list) for k in ("history","used_texts","used_premises")):
            raise ValueError("Invalid humor queue")
        return state
    legacy = {}
    for path in (COMMUNITY, RESOURCES):
        if path.exists():
            previous = json.loads(path.read_text())
            if previous.get("pending"):
                raise ValueError("Reconcile the previous publisher reservation first")
            legacy = previous
    rows = legacy.get("history",[]) + legacy.get("legacy_history",[])
    return {"version":1,"history":[],"used_texts":sorted({writer.fingerprint(p["text"]) for p in rows if p.get("text")}),
            "used_premises":[],"prior_days":dict(Counter(p["posted_at"][:10] for p in rows)),
            "last_posted_at":legacy.get("last_posted_at"),"pending":None}


def save_and_push(state):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n")
    temp.replace(STATE)
    subprocess.run(["bash","scripts/commit-state.sh"],check=True)


def client():
    auth = os.environ.get("X_AUTH_TOKEN","").strip()
    csrf = os.environ.get("X_CT0","").strip()
    if not auth or not csrf:
        raise ValueError("Both X session secrets are required")
    conn = Client("en-US",impersonate="chrome124")
    conn.set_cookies({"auth_token":auth,"ct0":csrf})
    return conn


def summary(message):
    print(message,flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path,"a") as stream:
            stream.write(message+"\n\n")


def run_key():
    return [os.environ["GITHUB_RUN_ID"],os.environ["GITHUB_RUN_ATTEMPT"]]


def ensure_open(state):
    if state.get("pending") or state.get("pause_reason"):
        raise ValueError("Queue paused or previous publication unresolved")


async def preview():
    state = load()
    plan = daily_plan(utcnow().date().isoformat())
    seen = set()
    for brief in plan:
        if brief["language"] not in seen:
            seen.add(brief["language"])
            draft = writer.generate(brief,state)
            summary(f"Preview only · {brief['language']} · {brief['topic']}\n\n{draft['text']}")
            state["history"].append(dict(brief,**draft))


async def publish_slot(state,brief):
    # A previous run can already have put this slot on X. Nothing is sent until
    # today's slot, limits and reservation are verified against durable state.
    ensure_open(state)
    connection = client()
    if state.get("check_session"):
        if not await connection.is_logged_in():
            raise SessionUnavailable()
        # Persist the successful check with the next reservation.
        state.pop("check_session")
    try:
        draft = writer.generate(brief,state)
    except writer.DraftError:
        # An exhausted reserve cannot be reused. Keep this slot and niche but
        # let the editor try one other language under the same publication rules.
        alternate = "Spanish" if brief["language"] == "English" else "English"
        brief = dict(brief,language=alternate)
        draft = writer.generate(brief,state)
    now = utcnow()
    current = next_slot(state,now)
    if not current or current[0]["slot"] != brief["slot"]:
        summary("Slot expired while writing; skipped.")
        return
    pending = {"brief":brief,"draft":draft,"approval":writer.approval(brief,draft),
               "run":run_key(),"reserved_at":now.isoformat()}
    state["pending"] = pending
    state["used_texts"].append(writer.fingerprint(draft["text"]))
    state["used_premises"].append(writer.fingerprint(draft["premise"]))
    save_and_push(state)
    durable = load()
    reservation = durable["pending"]
    if reservation != pending or reservation["run"] != run_key() or writer.validate(draft,brief) != draft or writer.approval(brief,draft) != reservation["approval"]:
        raise ValueError("Reservation did not match the approved draft")
    send_at = utcnow()
    if send_at.date()!=now.date() or not timedelta(0)<=send_at-now<=timedelta(minutes=5):
        # This attempt has not contacted X yet; release only this known reservation.
        durable["pending"] = None
        save_and_push(durable)
        summary("Reservation expired before sending; skipped.")
        return
    try:
        tweet = await connection.create_tweet(text=draft["text"],poll_uri="tombstone://card")
    except InvalidSession:
        # In pinned twifork 2.4.0 this exception is raised during the initial
        # transaction handshake, before the create request is transmitted.
        durable["pending"] = None
        durable["check_session"] = True
        save_and_push(durable)
        raise SessionUnavailable() from None
    ident = str(getattr(tweet,"id","") or "")
    if not ident.isdigit():
        raise ValueError("Unconfirmed X result; reservation kept for reconciliation")
    posted_at = utcnow().isoformat()
    durable["history"].append(dict(brief,**draft,tweet_id=ident,posted_at=posted_at))
    durable["history"] = durable["history"][-1000:]
    durable["last_posted_at"] = posted_at
    durable["pending"] = None
    save_and_push(durable)
    summary(f"Published {brief['language']} · {brief['topic']} · https://x.com/i/status/{ident}")
    # Confirmation is already durable. A failed read cannot cause a resend.
    try:
        posted = await asyncio.wait_for(connection.get_tweet_by_id(ident),timeout=10)
        card = posted._data.get("card")
        has_card = bool(card) and card.get("legacy",{}).get("name") not in {"tombstone","tombstone://card"}
    except Exception as exc:
        summary(f"Post confirmed. Card readback unavailable ({type(exc).__name__}).")
        return
    if has_card:
        durable["pause_reason"] = "X returned an unexpected card"
        save_and_push(durable)
        raise ValueError("Unexpected card on confirmed post")
    summary("X readback confirmed no website card.")


async def run(event):
    start = utcnow()
    # The 30-minute dispatcher can cover two spaced opportunities in its band.
    # Cron/manual/push invocations check once and never force an extra post.
    window = None
    if event == "repository_dispatch":
        window = start.replace(minute=start.minute//30*30,second=0,microsecond=0)+timedelta(minutes=30)
    for _ in range(2 if window else 1):
        state = load()
        ensure_open(state)
        slot = next_slot(state,utcnow(),window)
        if not slot:
            summary("No eligible slot in this wake.")
            return
        brief, ready = slot
        wait = (ready-utcnow()).total_seconds()
        if wait>0:
            await asyncio.sleep(wait)
        await publish_slot(state,brief)
