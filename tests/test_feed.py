import json
import os
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.x_api import RejectedPost
from unittest.mock import Mock, patch

from app import queue, writer
from app.cloudflare import DraftError
from app.editorial import LANGUAGES, TOPICS
from app.schedule import daily_plan, next_slot, MIN_GAP

START = datetime(2026,11,3,tzinfo=timezone.utc)
BRIEF = {"topic":"cars","language":"English","detail":"hidden wiper switch","form":"one-liner","slot":"unit"}
DRAFT = {"text":"The wipers are under Settings. Rain apparently needs a user account.",
         "premise":"A car asks for a login before wiping away rain."}
YES = dict(dict.fromkeys(("language","fit","safe","fresh"),True),reading=DRAFT["premise"])


def empty():
    return {"version":1,"history":[],"used_texts":[],"used_premises":[],"prior_days":{},
            "last_posted_at":None,"pending":None}


class TimetableTests(unittest.TestCase):
    def test_half_hour_dispatch_reaches_fifty_with_realistic_latency(self):
        state = empty()
        for day in range(7):
            for band in range(48):
                start = START+timedelta(days=day,minutes=30*band)
                now, end = start+timedelta(minutes=1,seconds=14), start+timedelta(minutes=30)
                for _ in range(2):
                    slot = next_slot(state,now,end)
                    if not slot:break
                    brief,ready = slot
                    now = ready+timedelta(seconds=35)
                    self.assertEqual(brief,next_slot(state,now)[0])
                    if state["last_posted_at"]:
                        self.assertGreaterEqual(now-datetime.fromisoformat(state["last_posted_at"]),MIN_GAP)
                    state["history"].append(dict(brief,posted_at=now.isoformat()))
                    state["last_posted_at"] = now.isoformat()
            self.assertEqual(len(state["history"]),50*(day+1))
        self.assertEqual(len({p["slot"] for p in state["history"]}),350)

    def test_plan_covers_all_languages_and_topics_with_daily_variation(self):
        for day in ("2026-11-03","2026-11-04"):
            plan=daily_plan(day)
            self.assertEqual(Counter(p["language"] for p in plan),LANGUAGES)
            self.assertEqual(Counter(p["topic"] for p in plan),{k:n for k,(n,_) in TOPICS.items()})
            self.assertEqual(len({p["detail"] for p in plan}),50)
            self.assertEqual(plan,daily_plan(day))
        self.assertNotEqual([p["due_at"][11:] for p in daily_plan("2026-11-03")],
                            [p["due_at"][11:] for p in daily_plan("2026-11-04")])

    def test_migration_counts_cap_and_gap(self):
        state=empty()
        state["prior_days"]={"2026-11-03":50}
        self.assertIsNone(next_slot(state,START+timedelta(hours=12)))
        state["prior_days"]={}
        state["last_posted_at"]=(START+timedelta(hours=12)).isoformat()
        self.assertIsNone(next_slot(state,START+timedelta(hours=12,minutes=5)))

    def test_late_wake_never_backfills_a_used_or_expired_slot(self):
        state=empty()
        now=START+timedelta(hours=23,minutes=59)
        brief=next_slot(state,now)[0]
        self.assertEqual(brief["slot"],daily_plan("2026-11-03")[-1]["slot"])
        state["history"]=[dict(brief,posted_at=now.isoformat())]
        self.assertIsNone(next_slot(state,now))


class EditorialTests(unittest.TestCase):
    def test_natural_scripts_fit_post_budget(self):
        samples={"Hindi":"इस बटन की सेटिंग खोजते खोजते बारिश रुक गई।",
                 "Japanese":"会議を減らす会議が定例になった。","Simplified Chinese":"雨停了，雨刷的设置页面终于打开了。",
                 "Spanish":"El botón de cancelar necesita su propia reunión.","English":DRAFT["text"]}
        for language,text in samples.items():
            with self.subTest(language=language):
                self.assertEqual(writer.validate(dict(DRAFT,text=text),dict(BRIEF,language=language))["text"],text)

    def test_exclusions_and_wrong_script(self):
        for language,text in [("English","Look at https://example.org now."),("Hindi","An English-only punchline."),
                              ("Japanese","政治の話をしよう。"),("Simplified Chinese","这些选举新闻真有趣。"),
                              ("Spanish","Hablemos de partidos políticos."),("Hindi","चुनाव की बात करते हैं।"),
                              ("English","An update from the government."),("English","Thank me later. Follow for more."),
                              ("English","a"*261),("English","Users clicked 37% more often."),("Japanese","〇〇〇〇〇〇")]:
            with self.subTest(text=text),self.assertRaises(DraftError):
                writer.validate(dict(DRAFT,text=text),dict(BRIEF,language=language))

    def test_english_premise_blocks_reuse_across_scripts(self):
        state=empty()
        state["used_premises"]=[writer.fingerprint(DRAFT["premise"])]
        with self.assertRaises(DraftError):
            writer.check_repetition(dict(DRAFT,text="雨刷的设置页面终于打开了。"),state)

    def test_review_never_approves_missing_or_nonboolean_fields(self):
        for verdict in [dict(YES,safe=False),dict(YES,language="true"),{},dict(YES,extra=True),None]:
            with self.subTest(verdict=verdict),patch.object(writer,"complete",side_effect=[DRAFT,verdict]*3),self.assertRaises(DraftError):
                writer.generate(BRIEF,empty())

    def test_corrected_model_draft_still_requires_independent_review(self):
        with patch.object(writer,"complete",side_effect=[DraftError("bad JSON"),DRAFT,YES]) as call:
            self.assertEqual(writer.generate(BRIEF,empty()),DRAFT)
        self.assertEqual(call.call_args.args[0],writer.CRITIC)
        self.assertNotIn(DRAFT["premise"],str(call.call_args.args[1]))

    def test_service_outage_never_generates_fallback(self):
        with patch.object(writer,"complete",side_effect=TimeoutError),self.assertRaises(TimeoutError):
            writer.generate(BRIEF,empty())


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        base=Path(temp.name)
        self.brief=next(p for p in daily_plan("2026-11-03") if p["language"]=="English")
        self.now=datetime.fromisoformat(self.brief["due_at"])+timedelta(seconds=5)
        self.durable=[]
        for p in [patch.object(queue,"STATE",base/"state.json"),patch.object(queue,"RESOURCES",base/"retired.json"),
                  patch.object(queue,"COMMUNITY",base/"older.json"),patch.object(queue,"utcnow",return_value=self.now),
                  patch.object(queue,"summary"),patch.dict(os.environ,{"GITHUB_RUN_ID":"test","GITHUB_RUN_ATTEMPT":"1","X_API_PUBLISH":"true"})]:
            p.start();self.addCleanup(p.stop)

    def persist(self,state):
        self.durable.append(json.loads(json.dumps(state)))
        queue.STATE.write_text(json.dumps(state))

    async def test_reservation_is_durable_before_one_text_only_send(self):
        def send(text):
            self.assertIsNotNone(queue.load()["pending"])
            self.assertEqual(text,DRAFT["text"])
            return "123"
        sender=Mock(side_effect=send)
        with patch.object(writer,"generate",return_value=DRAFT),patch.object(queue,"prepare_sender",return_value=sender),patch.object(queue,"save_and_push",side_effect=self.persist):
            await queue.publish_slot(empty(),self.brief)
        sender.assert_called_once()
        self.assertIsNone(queue.load()["pending"])
        self.assertEqual(queue.load()["history"][-1]["tweet_id"],"123")

    async def test_failed_reservation_push_cannot_send(self):
        with patch.object(writer,"generate",return_value=DRAFT),patch.object(queue,"save_and_push",side_effect=OSError),patch.object(queue,"prepare_sender") as prepare,self.assertRaises(OSError):
            await queue.publish_slot(empty(),self.brief)
        prepare.return_value.assert_not_called()

    async def test_timeout_or_missing_id_blocks_future_automatic_send(self):
        for outcome in (TimeoutError(),None):
            sender=Mock(side_effect=[outcome])
            with patch.object(writer,"generate",return_value=DRAFT),patch.object(queue,"prepare_sender",return_value=sender),patch.object(queue,"save_and_push",side_effect=self.persist),self.assertRaises((TimeoutError,ValueError)):
                await queue.publish_slot(empty(),self.brief)
            with self.assertRaises(ValueError):queue.ensure_open(queue.load())
            sender.assert_called_once()

    async def test_default_mode_saves_copyable_draft_without_contacting_x(self):
        with patch.dict(os.environ,{"X_API_PUBLISH":""}),patch.object(writer,"generate",return_value=DRAFT),patch.object(queue,"prepare_sender") as api,patch.object(queue,"save_and_push",side_effect=self.persist):
            await queue.publish_slot(empty(),self.brief)
        api.assert_not_called()
        state=queue.load()
        queue.write_drafts(state)
        self.assertIn("Open draft in X",queue.STATE.with_name("DRAFTS.md").read_text())
        self.assertNotIn("tweet_id",state["history"][-1])
        self.assertIsNone(next_slot(state,self.now))

    async def test_definitive_rejection_releases_reservation(self):
        with patch.object(writer,"generate",return_value=DRAFT),patch.object(queue,"prepare_sender",return_value=Mock(side_effect=RejectedPost(402))),patch.object(queue,"save_and_push",side_effect=self.persist),self.assertRaises(RejectedPost):
            await queue.publish_slot(empty(),self.brief)
        self.assertIsNone(queue.load()["pending"])

    async def test_preview_covers_all_languages_without_persisting_or_sending(self):
        with patch.object(writer,"generate",return_value=DRAFT) as make,patch.object(queue,"prepare_sender") as x,patch.object(queue,"save_and_push") as push:
            await queue.preview()
        self.assertEqual({c.args[0]["language"] for c in make.call_args_list},set(LANGUAGES))
        x.assert_not_called();push.assert_not_called()

    def test_retired_history_counts_and_uncertain_send_blocks_migration(self):
        data={"history":[dict(DRAFT,posted_at=START.isoformat())],"legacy_history":[],"last_posted_at":START.isoformat(),"pending":None}
        queue.RESOURCES.write_text(json.dumps(data))
        self.assertEqual(queue.load()["prior_days"],{"2026-11-03":1})
        self.assertEqual(json.loads(queue.RESOURCES.read_text()),data)
        data["pending"]={"text":"unknown"};queue.RESOURCES.write_text(json.dumps(data))
        with self.assertRaises(ValueError):queue.load()
