import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from twikit.errors import InvalidSession
from app import queue
from app.schedule import daily_plan,next_slot,MIN_GAP

START=datetime(2026,11,6,tzinfo=timezone.utc)
DRAFT={"text":"BREAKING — Acme releases its code editor.\n\nWhich part of your workflow would you try first?",
       "event_key":"acme-editor-release","premise":"Acme releases its first code editor."}
SOURCE={"id":"111","author":"Acme","author_id":"222","text":"Our code editor launches today.",
        "created_at":START.isoformat(),"views":100000,"url":"https://x.com/Acme/status/111",
        "urls":["https://acme.example/blog/editor-release"],"source_ids":["111"]}


def empty():
    return {"version":1,"history":[],"used_source_ids":[],"used_urls":[],"used_events":[],
            "used_texts":[],"pending":None,"last_posted_at":None}


class ScheduleTests(unittest.TestCase):
    def test_forty_confirmed_quotes_each_day_under_normal_half_hour_wakes(self):
        state=empty()
        for day in range(7):
            for band in range(48):
                now=START+timedelta(days=day,minutes=30*band+1,seconds=15)
                end=now.replace(minute=now.minute//30*30,second=0)+timedelta(minutes=30)
                for _ in range(2):
                    found=next_slot(state,now,end)
                    if not found:break
                    slot,ready=found
                    now=ready+timedelta(seconds=50)
                    self.assertEqual(slot,next_slot(state,now)[0])
                    if state['last_posted_at']:
                        self.assertGreaterEqual(now-datetime.fromisoformat(state['last_posted_at']),MIN_GAP)
                    state['history'].append(dict(slot,posted_at=now.isoformat()))
                    state['last_posted_at']=now.isoformat()
            self.assertEqual(len(state['history']),40*(day+1))
        self.assertNotEqual(daily_plan('2026-11-06'),daily_plan('2026-11-07'))

    def test_cap_no_catch_up_and_failed_search_cooldown(self):
        state=empty()
        now=START+timedelta(hours=23,minutes=59)
        self.assertEqual(next_slot(state,now)[0],daily_plan('2026-11-06')[-1])
        state['last_attempt']={'slot':daily_plan('2026-11-06')[-1]['slot'],'at':now.isoformat()}
        self.assertIsNone(next_slot(state,now))
        state['history']=[dict(p,posted_at=now.isoformat()) for p in daily_plan('2026-11-06')]
        self.assertIsNone(next_slot(state,now))


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        self.slot=daily_plan('2026-11-06')[0]
        self.now=datetime.fromisoformat(self.slot['due_at'])+timedelta(seconds=1)
        for mock in (patch.object(queue,'STATE',root/'qofeno.json'),patch.object(queue,'LEGACY',root/'old.json'),
                     patch.object(queue,'OLDER_QUOTES',root/'older.json'),patch.object(queue,'utcnow',return_value=self.now),
                     patch.object(queue,'summary'),patch.dict(os.environ,{'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1'}),
                     patch.object(queue.sources,'validate',return_value=SOURCE),patch.object(queue.writer,'validate',return_value=DRAFT)):
            mock.start();self.addCleanup(mock.stop)

    def persist(self,state):
        queue.STATE.write_text(json.dumps(state))

    def connection(self):
        return SimpleNamespace(create_tweet=AsyncMock(return_value=SimpleNamespace(id='333')),
                               get_tweet_by_id=AsyncMock(return_value=SimpleNamespace(quoted_status_id='111')))

    async def test_one_quote_has_durable_reservation_then_verified_source(self):
        conn=self.connection()
        async def send(**kwargs):
            self.assertEqual(kwargs,{'text':DRAFT['text'],'attachment_url':SOURCE['url']})
            self.assertEqual(queue.load()['pending']['source'],SOURCE)
            return SimpleNamespace(id='333')
        conn.create_tweet.side_effect=send
        with patch.object(queue,'save_and_push',side_effect=self.persist):
            await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
        state=queue.load()
        self.assertEqual(state['history'][0]['source_views'],100000)
        self.assertIn('111',state['used_source_ids'])
        self.assertIsNone(state['pending'])
        self.assertNotIn('verification_pending',state)
        self.assertIsNone(next_slot(state,self.now))
        conn.create_tweet.assert_awaited_once()

    async def test_failed_reservation_commit_cannot_send(self):
        conn=self.connection()
        with patch.object(queue,'save_and_push',side_effect=OSError),self.assertRaises(OSError):
            await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
        conn.create_tweet.assert_not_awaited()

    async def test_uncertain_send_keeps_its_reservation(self):
        for result in (TimeoutError(),SimpleNamespace(id=None)):
            conn=self.connection();conn.create_tweet.side_effect=[result]
            with patch.object(queue,'save_and_push',side_effect=self.persist),self.assertRaises((ValueError,TimeoutError)):
                await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
            with self.assertRaises(ValueError):queue.require_open(queue.load())
            conn.create_tweet.assert_awaited_once()

    async def test_invalid_session_can_recover_without_ambiguous_retry(self):
        conn=self.connection();conn.create_tweet.side_effect=InvalidSession('fixture')
        with patch.object(queue,'save_and_push',side_effect=self.persist),self.assertRaises(queue.SessionUnavailable):
            await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
        self.assertIsNone(queue.load()['pending'])
        self.assertTrue(queue.load()['check_session'])

    async def test_wrong_quote_attachment_pauses_after_recording_id(self):
        conn=self.connection();conn.get_tweet_by_id.return_value=SimpleNamespace(quoted_status_id='444')
        with patch.object(queue,'save_and_push',side_effect=self.persist),self.assertRaises(ValueError):
            await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
        self.assertEqual(queue.load()['history'][0]['tweet_id'],'333')
        self.assertTrue(queue.load()['pause_reason'])
        conn.create_tweet.assert_awaited_once()

    async def test_readback_timeout_requires_verification_before_next_send(self):
        conn=self.connection();conn.get_tweet_by_id.side_effect=TimeoutError()
        with patch.object(queue,'save_and_push',side_effect=self.persist):
            await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
        state=queue.load()
        self.assertIsNone(state['pending'])
        self.assertEqual(state['verification_pending']['tweet_id'],'333')
        self.assertIsNone(next_slot(state,self.now+timedelta(hours=2)))
        conn.get_tweet_by_id.side_effect=None
        with patch.object(queue,'save_and_push',side_effect=self.persist):
            self.assertTrue(await queue.verify_quote(conn,state))
        conn.create_tweet.assert_awaited_once()

    def test_legacy_sources_and_pending_work_are_preserved_on_migration(self):
        queue.LEGACY.write_text(json.dumps({'last_posted_at':START.isoformat(),'used_texts':['oldtext']}))
        queue.OLDER_QUOTES.write_text(json.dumps({'used_source_ids':['987']}))
        migrated=queue.load()
        self.assertIn('987',migrated['used_source_ids'])
        self.assertIn('oldtext',migrated['used_texts'])
        self.assertEqual(migrated['last_posted_at'],START.isoformat())
        queue.LEGACY.write_text(json.dumps({'pending':{'text':'unconfirmed'}}))
        with self.assertRaises(ValueError):queue.load()
