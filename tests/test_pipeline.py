import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from app import queue
from app.sources import candidate,discover
from app.writer import validate,generate

NOW=datetime(2026,10,27,12,10,tzinfo=timezone.utc)
TEXT='What the fuck?\n\nThe interesting part is the workflow change. Who gets to review what the agent changed?'
SOURCE={'id':'123','text':'Claude adds a coding workflow for reviewing agent changes.','author':'builder','url':'https://x.com/builder/status/123'}

def state(): return {'version':2,'history':[],'used_source_ids':[],'last_posted_at':None,'pending':None}
def raw(text=TEXT): return json.dumps({'should_post':True,'text':text})
def tweet(**kw):
    d=dict(id='123',text=SOURCE['text'],user=SimpleNamespace(screen_name='builder'),created_at_datetime=NOW-timedelta(hours=1),lang='en',favorite_count=15,reply_count=3,retweet_count=2)
    d.update(kw);return SimpleNamespace(**d)

class ValidationTests(unittest.TestCase):
    def test_good_post(self): self.assertEqual(validate(raw(),SOURCE,[]),TEXT)
    def test_decline(self): self.assertIsNone(validate(json.dumps({'should_post':False,'text':''}),SOURCE,[]))
    def test_invalid_outputs(self):
        for text in (TEXT.replace('\n\n', ' '), 'short','a'*281,'😀'*140,TEXT+' @OpenAI',TEXT+' https://evil.test',TEXT+' 999x faster',TEXT+'\u200b'):
            with self.subTest(text=text),self.assertRaises(ValueError): validate(raw(text),SOURCE,[])
    def test_schema(self):
        for obj in ({'text':TEXT},{'should_post':'false','text':TEXT},{'should_post':True,'text':TEXT,'reply':'x'}):
            with self.assertRaises(ValueError):validate(json.dumps(obj),SOURCE,[])
    def test_duplicate(self):
        with self.assertRaises(ValueError):validate(raw(),SOURCE,[{'text':TEXT}])
    def test_copy(self):
        with self.assertRaises(ValueError):validate(raw(SOURCE['text']),SOURCE,[])
    def test_sources(self):
        self.assertIsNotNone(candidate(tweet(),NOW,set()))
        for changes in ({'created_at_datetime':NOW-timedelta(days=2)},{'text':'Football finals tonight'}, {'text':'AI giveaway follow back'}, {'lang':'fr'},{'in_reply_to':'1'},{'is_quote_status':True},{'possibly_sensitive':True},{'favorite_count':0,'reply_count':0,'retweet_count':0}):
            self.assertIsNone(candidate(tweet(**changes),NOW,set()))
        self.assertIsNone(candidate(tweet(),NOW,{'123'}))
    def test_slots(self):
        self.assertEqual(len({queue.slot(NOW.replace(hour=h,minute=m)) for h in range(24) for m in range(60)}),10)
    def test_daily_and_interval(self):
        s=state();self.assertTrue(queue.eligible(s,NOW))
        s['last_posted_at']=(NOW-timedelta(minutes=30)).isoformat();self.assertFalse(queue.eligible(s,NOW))
        s['last_posted_at']=None;s['history']=[{'posted_at':NOW.isoformat(),'slot':queue.slot(NOW)}];self.assertFalse(queue.eligible(s,NOW))
    def test_unresolved_blocks_next_day(self):
        s=state();s['pending']={'slot':'yesterday'}
        with self.assertRaises(ValueError):queue.eligible(s,NOW)
    def test_cloudflare_contract(self):
        import io
        response=io.StringIO(json.dumps({'success':True,'result':{'response':raw()}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',return_value=response) as call:
            self.assertEqual(generate(SOURCE,[]),TEXT)
            payload=json.loads(call.call_args.args[0].data)
            self.assertEqual(payload['messages'][0]['role'],'system')
            self.assertIn('untrusted',payload['messages'][0]['content'])

class PublicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'state.json'
        self.p=patch.object(queue,'PATH',self.path);self.p.start()
        self.env=patch.dict(os.environ,{'GITHUB_RUN_ID':'test','GITHUB_RUN_ATTEMPT':'1','GITHUB_OUTPUT':'','GITHUB_STEP_SUMMARY':''});self.env.start()
    def tearDown(self):self.env.stop();self.p.stop();self.temp.cleanup()
    def reservation(self):
        now=datetime.now(timezone.utc);s=state();s['pending']={'slot':queue.slot(now),'source':SOURCE,'text':TEXT,'prepared_at':now.isoformat(),'run_id':'test','run_attempt':'1'};queue.save(s)
    async def test_success_attaches_quote_and_clears_reservation(self):
        self.reservation();c=SimpleNamespace(create_tweet=AsyncMock(return_value=SimpleNamespace(id='456')))
        with patch.object(queue,'client',return_value=c):await queue.publish()
        c.create_tweet.assert_awaited_once_with(text=TEXT,attachment_url=SOURCE['url'])
        s=queue.load();self.assertIsNone(s['pending']);self.assertEqual(s['used_source_ids'],['123'])
        self.assertFalse(queue.eligible(s,datetime.now(timezone.utc)))
    async def test_uncertain_failure_never_retries(self):
        self.reservation();c=SimpleNamespace(create_tweet=AsyncMock(side_effect=TimeoutError))
        with patch.object(queue,'client',return_value=c),self.assertRaises(TimeoutError):await queue.publish()
        c.create_tweet.assert_awaited_once();self.assertIsNotNone(queue.load()['pending'])
        with self.assertRaises(ValueError):queue.eligible(queue.load(),datetime.now(timezone.utc))
    async def test_missing_id_remains_reserved(self):
        self.reservation();c=SimpleNamespace(create_tweet=AsyncMock(return_value=SimpleNamespace(id='')))
        with patch.object(queue,'client',return_value=c),self.assertRaises(ValueError):await queue.publish()
        self.assertIsNotNone(queue.load()['pending'])
    async def test_rerun_cannot_publish_old_reservation(self):
        self.reservation()
        with patch.dict(os.environ,{'GITHUB_RUN_ATTEMPT':'2'}),self.assertRaises(ValueError):await queue.publish()
    async def test_search_rate_limit_stops_requests(self):
        c=SimpleNamespace(search_tweet=AsyncMock(side_effect=RuntimeError('429')))
        with self.assertRaises(RuntimeError):await discover(c,set(),[],NOW)
        c.search_tweet.assert_awaited_once()
    async def test_prepare_does_not_publish(self):
        queue.save(state());c=SimpleNamespace(is_logged_in=AsyncMock(return_value=True),create_tweet=AsyncMock())
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch.object(queue,'client',return_value=c),patch.object(queue,'discover',AsyncMock(return_value=[SOURCE])),patch.object(queue,'generate',return_value=TEXT):await queue.prepare()
        c.create_tweet.assert_not_awaited();self.assertIsNotNone(queue.load()['pending'])
