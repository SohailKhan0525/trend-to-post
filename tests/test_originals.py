import io
import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from app import queue
from app.schedule import daily_plan,next_due
from app.writer import generate,validate,complete,DraftError

NOW=datetime(2026,10,31,12,0,tzinfo=timezone.utc)
TEXT="Hello solo founders\n\nWhat part of your MVP did you cut so you could finally ship it?"

def state():return {'version':3,'history':[],'last_posted_at':None,'pending':None}

class ScheduleTests(unittest.TestCase):
    def test_twenty_unique_spread_slots_and_balanced_categories(self):
        for day in ('2026-10-31','2026-11-01','2026-12-31'):
            plan=daily_plan(day)
            self.assertEqual(len(plan),20)
            self.assertEqual(len({s['slot'] for s in plan}),20)
            times=[datetime.fromisoformat(p['due_at']) for p in plan]
            self.assertTrue(all(t.date().isoformat()==day for t in times))
            self.assertTrue(all(b-a>=timedelta(minutes=42) for a,b in zip(times,times[1:])))
            self.assertEqual({c:sum(s['category']==c for s in plan) for c in {s['category'] for s in plan}},{'connect':4,'showcase':4,'coding_take':4,'question':4,'observation':4})
    def test_times_change_daily_but_retries_keep_same_plan(self):
        a=daily_plan('2026-10-31');b=daily_plan('2026-11-01')
        self.assertEqual(a,daily_plan('2026-10-31'))
        self.assertNotEqual([s['due_at'][11:] for s in a],[s['due_at'][11:] for s in b])
    def test_one_per_due_slot(self):
        s=state();brief=next_due(s,NOW)
        s['history']=[{'slot':brief['slot'],'posted_at':(NOW-timedelta(hours=1)).isoformat()}]
        self.assertIsNone(next_due(s,NOW))
    def test_no_catchup_to_expired_earlier_slots(self):
        s=state();brief=next_due(s,NOW)
        s['history']=[{'slot':brief['slot'],'posted_at':(NOW-timedelta(hours=1)).isoformat()}]
        # Previously missed due slots cannot create a backlog burst.
        self.assertIsNone(next_due(s,NOW))
    def test_daily_cap(self):
        s=state();s['history']=[{'slot':x['slot'],'posted_at':NOW.isoformat()} for x in daily_plan(NOW.date().isoformat())]
        self.assertIsNone(next_due(s,NOW,start_now=True))
    def test_interval_and_unresolved_reservation(self):
        s=state();s['last_posted_at']=(NOW-timedelta(minutes=10)).isoformat()
        self.assertIsNone(next_due(s,NOW))
        s['pending']={'text':'uncertain'}
        with self.assertRaises(ValueError):next_due(s,NOW)
    def test_launch_consumes_slot_before_first_due(self):
        now=NOW.replace(hour=0,minute=0)
        self.assertIsNone(next_due(state(),now))
        self.assertEqual(next_due(state(),now,True)['slot'],daily_plan(now.date().isoformat())[0]['slot'])

class WriterTests(unittest.TestCase):
    def test_short_original_and_optional_blank_line(self):
        self.assertEqual(validate(TEXT,[]),TEXT)
        self.assertEqual(validate('Your terminal setup has more plugins than your product has features.',[]),'Your terminal setup has more plugins than your product has features.')
    def test_invalid_content(self):
        for text in ('short','a'*281,TEXT+' @somebody',TEXT+' https://example.com',TEXT+'\u200b',TEXT+' I have 999 customers',"Hello founders\n\nI'll use every product you share today."):
            with self.subTest(text=text),self.assertRaises(DraftError):validate(text,[])
    def test_exact_and_near_duplicates(self):
        with self.assertRaises(DraftError):validate(TEXT,[{'text':TEXT.lower()}])
        with self.assertRaises(DraftError):validate(TEXT.replace('solo founders','startup builders'),[{'text':TEXT}])
    def test_repeated_opening(self):
        with self.assertRaises(DraftError):validate('Hello solo founders\n\nWhich customer conversation changed how you thought about your pricing?',[{'text':TEXT}])
    def test_fresh_take_allowed(self):
        self.assertIsNotNone(validate('The stack can wait.\n\nA tiny working feature beats another weekend choosing a framework.',[{'text':TEXT}]))
    def test_generator_selects_fresh_candidate(self):
        fresh='Boring software is underrated.\n\nA dependable tool beats a beautiful rewrite that never ships.'
        with patch('app.writer.complete',side_effect=[{'posts':[TEXT,fresh]},{'novel':True}]):
            self.assertEqual(generate({'brief':'A coding opinion','category':'coding_take'},[{'text':TEXT}]),fresh)
    def test_semantic_repetition_fails_closed(self):
        for verdict in ({'novel':False},{'novel':'true'},{'yes':True}):
            with self.subTest(verdict=verdict),patch('app.writer.complete',side_effect=[{'posts':[TEXT]},verdict]):
                with self.assertRaises(DraftError):generate({'brief':'Founder question','category':'question'},[{'text':'Earlier unrelated observation.'}])
    def test_cloudflare_request(self):
        response=io.StringIO(json.dumps({'success':True,'result':{'response':json.dumps({'posts':[TEXT]})}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',return_value=response) as call:
            self.assertEqual(complete('instructions',{'brief':'hello'}),{'posts':[TEXT]})
            request=call.call_args.args[0]
            self.assertIn('/ai/run/@cf/',request.full_url)
            self.assertEqual(json.loads(request.data)['messages'][0]['content'],'instructions')

class QueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'state.json'
        self.p=patch.object(queue,'PATH',self.path);self.p.start()
        self.env=patch.dict(os.environ,{'GITHUB_RUN_ID':'test','GITHUB_RUN_ATTEMPT':'1','GITHUB_OUTPUT':'','GITHUB_STEP_SUMMARY':''});self.env.start()
    def tearDown(self):self.env.stop();self.p.stop();self.temp.cleanup()
    def reserved(self):
        s=state();s['pending']={'slot':datetime.now(timezone.utc).date().isoformat()+':3','category':'question','text':TEXT,'prepared_at':datetime.now(timezone.utc).isoformat(),'run_id':'test','run_attempt':'1'};queue.save(s)
    async def test_standalone_publish_no_quote_or_reply_arguments(self):
        self.reserved();c=SimpleNamespace(create_tweet=AsyncMock(return_value=SimpleNamespace(id='456')))
        with patch.object(queue,'client',return_value=c):await queue.publish()
        c.create_tweet.assert_awaited_once_with(text=TEXT)
        self.assertIsNone(queue.load()['pending'])
        self.assertEqual(queue.load()['history'][0]['tweet_id'],'456')
    async def test_uncertain_timeout_keeps_reservation(self):
        self.reserved();c=SimpleNamespace(create_tweet=AsyncMock(side_effect=TimeoutError))
        with patch.object(queue,'client',return_value=c),self.assertRaises(TimeoutError):await queue.publish()
        c.create_tweet.assert_awaited_once();self.assertIsNotNone(queue.load()['pending'])
    async def test_missing_id_blocks_retry(self):
        self.reserved();c=SimpleNamespace(create_tweet=AsyncMock(return_value=SimpleNamespace(id='')))
        with patch.object(queue,'client',return_value=c),self.assertRaises(ValueError):await queue.publish()
        self.assertIsNotNone(queue.load()['pending'])
    async def test_old_run_cannot_publish(self):
        self.reserved()
        with patch.dict(os.environ,{'GITHUB_RUN_ATTEMPT':'2'}),self.assertRaises(ValueError):await queue.publish()
    async def test_prepare_only_reserves(self):
        queue.save(state())
        with patch.object(queue,'generate',return_value=TEXT),patch.object(queue,'client') as c:await queue.prepare(start_now=True)
        c.assert_not_called();self.assertIsNotNone(queue.load()['pending'])
    async def test_preview_cannot_change_state_or_publish(self):
        queue.save(state());before=self.path.read_text()
        with patch.object(queue,'generate',return_value=TEXT),patch.object(queue,'client') as c:await queue.prepare(preview=True)
        c.assert_not_called();self.assertEqual(before,self.path.read_text())
