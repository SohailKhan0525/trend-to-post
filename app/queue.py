"""Durably reserve, send once, and confirm each scheduled original."""
import asyncio
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from .x_api import prepare_sender, RejectedPost
from . import writer
from .schedule import daily_plan, next_slot

STATE = Path("state/dash_rattle.json")
RESOURCES = Path("state/resources_queue.json")
COMMUNITY = Path("state/community_queue.json")


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
    if any(row.get("queued_at") for row in state["history"]):
        write_drafts(state)
    subprocess.run(["bash","scripts/commit-state.sh"],check=True)


def write_drafts(state):
    rows = [row for row in state["history"] if row.get("queued_at")][-50:]
    lines = ["# Dash Rattle drafts", "", "The latest 50 reviewed drafts, newest first. Open a draft in X to review and post it.", ""]
    for row in reversed(rows):
        link = "https://x.com/intent/post?" + urlencode({"text":row["text"]})
        lines.extend([f"### {row['queued_at'][:16].replace('T',' ')} UTC · {row['language']} · {row['topic']}", ""])
        lines.extend("    "+line for line in row["text"].splitlines())
        lines.extend(["",f"[Open draft in X]({link})",""])
    STATE.with_name("DRAFTS.md").write_text("\n".join(lines)+"\n")


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
    publish = os.environ.get("X_API_PUBLISH","").lower() == "true"
    sender = prepare_sender() if publish else None
    draft = writer.generate(brief,state)
    now = utcnow()
    current = next_slot(state,now)
    if not current or current[0]["slot"] != brief["slot"]:
        summary("Slot expired while writing; skipped.")
        return
    state["used_texts"].append(writer.fingerprint(draft["text"]))
    state["used_premises"].append(writer.fingerprint(draft["premise"]))
    if not publish:
        state["history"].append(dict(brief,**draft,queued_at=now.isoformat()))
        state["history"] = state["history"][-1000:]
        state["last_queued_at"] = now.isoformat()
        save_and_push(state)
        summary(f"Saved reviewed draft · {brief['language']} · {brief['topic']} (state/DRAFTS.md)")
        return
    pending = {"brief":brief,"draft":draft,"approval":writer.approval(brief,draft),
               "run":run_key(),"reserved_at":now.isoformat()}
    state["pending"] = pending
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
        ident = await asyncio.to_thread(sender,draft["text"])
    except RejectedPost as exc:
        durable["pending"] = None
        save_and_push(durable)
        summary(f"X declined the post (HTTP {exc.status}).")
        raise
    if not isinstance(ident,str) or not ident.isdigit():
        raise ValueError("Unconfirmed X result; reservation kept for reconciliation")
    posted_at = utcnow().isoformat()
    durable["history"].append(dict(brief,**draft,tweet_id=ident,posted_at=posted_at))
    durable["history"] = durable["history"][-1000:]
    durable["last_posted_at"] = posted_at
    durable["pending"] = None
    save_and_push(durable)
    summary(f"Published {brief['language']} · {brief['topic']} · https://x.com/i/status/{ident}")


async def run(event):
    configured = all(os.environ.get(key) for key in ("X_API_KEY","X_API_SECRET","X_ACCESS_TOKEN","X_ACCESS_TOKEN_SECRET"))
    summary(f"Official X credentials complete: {configured}. Delivery: " +
            ("official API" if os.environ.get("X_API_PUBLISH","").lower()=="true" else "reviewed drafts"))
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
