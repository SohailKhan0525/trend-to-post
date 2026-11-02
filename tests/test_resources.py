import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock,MagicMock
from app import queue
from app.http import canonical,assert_public,fetch,Page,LinkError
from app.resources import inspect,discover,_is_fresh
from app.schedule import daily_plan,next_due,MIN_GAP
from app.audiences import DAILY_MIX
from app.writer import generate,render,review,complete,approval_digest,DraftError

NOW=datetime(2026,11,2,12,0,tzinfo=timezone.utc)
SOURCE={'id':'one','root':'https://docs.example.com/guide/','hosts':['docs.example.com'],'paths':['/guide/'],'audiences':['vibecoders']}
LABELS=['Build a web app','Learn accessible layouts']
GOOD={'allowed':True,'relevant':True,'grounded':True}


def pair():
    return [{'url':url,'source_id':str(i),'title':label,'excerpt':'Practical instructions for building useful accessible web apps. '*8,
             'aliases':[canonical(url)],'fingerprint':str(i),'verified_at':NOW.isoformat()}
            for i,(url,label) in enumerate(zip(('https://docs.example.com/guide','https://tools.example.org/learn'),LABELS))]


def state():return queue.new_state()


class HTTPTests(unittest.TestCase):
    def test_tracking_and_anchor_variants_are_one_identity(self):
        self.assertEqual(canonical('https://DOCS.example.com/guide/?utm_source=x#part'),canonical('https://docs.example.com/guide'))
        self.assertEqual(canonical('https://docs.example.com/%67uide'),canonical('https://docs.example.com/guide'))
    def test_unsafe_urls_are_rejected(self):
        for url in ('http://docs.example.com/guide','https://user:password@docs.example.com/','https://docs.example.com:9000/','file:///etc/passwd','https://docs.example.com/a\nb'):
            with self.subTest(url=url),self.assertRaises((LinkError,ValueError)):canonical(url)
    def test_host_allowlist_is_exact(self):
        for url in ('https://evil.example.com/','https://docs.example.com.evil.com/'):
            with self.assertRaises(LinkError):assert_public(url,SOURCE['hosts'])
    def test_private_dns_destination_is_rejected(self):
        with patch('socket.getaddrinfo',return_value=[(None,None,None,None,('127.0.0.1',443))]),self.assertRaises(LinkError):assert_public(SOURCE['root'],SOURCE['hosts'])
    def test_redirect_preserves_slash_and_checks_destination(self):
        response=MagicMock();response.__enter__.return_value=response
        response.status=200;response.read.return_value=b'<html></html>';response.headers={'Content-Type':'text/html'}
        redirect=urllib.error.HTTPError('https://docs.example.com/guide',301,'move',{'Location':'/guide/'},None)
        opener=MagicMock();opener.open.side_effect=[redirect,response]
        with patch('socket.getaddrinfo',return_value=[(None,None,None,None,('8.8.8.8',443))]),patch('urllib.request.build_opener',return_value=opener):page=fetch('https://docs.example.com/guide',SOURCE['hosts'])
        self.assertEqual(page.url,'https://docs.example.com/guide/')
        self.assertEqual(opener.open.call_args.args[0].full_url,'https://docs.example.com/guide/')
    def test_unapproved_redirect_is_blocked(self):
        redirect=urllib.error.HTTPError(SOURCE['root'],302,'move',{'Location':'https://localhost/private'},None)
        opener=MagicMock();opener.open.side_effect=redirect
        with patch('socket.getaddrinfo',return_value=[(None,None,None,None,('8.8.8.8',443))]),patch('urllib.request.build_opener',return_value=opener),self.assertRaises(LinkError):fetch(SOURCE['root'],SOURCE['hosts'])
        self.assertEqual(opener.open.call_count,1)


class ResourceTests(unittest.TestCase):
    def html(self,title='Accessible layouts',body=None):
        body=body or 'Practical instructions for accessible headings, readable labels and useful examples. '*4
        return Page(SOURCE['root'],f'<html><title>{title}</title><main>{body}</main></html>'.encode(),'text/html',[])
    def test_public_page_produces_verified_evidence(self):
        with patch('app.resources.fetch',return_value=self.html()):resource=inspect(SOURCE['root'],SOURCE)
        self.assertEqual(resource['url'],SOURCE['root']);self.assertIn('verified_at',resource)
        self.assertEqual(resource['aliases'],['https://docs.example.com/guide'])
    def test_soft_errors_and_excluded_topics_are_rejected(self):
        for page in (self.html('404 - Not found'),self.html('Just a moment'),self.html(body='Useful guide. '*80+'Political parties and country comparisons.')):
            with patch('app.resources.fetch',return_value=page),self.assertRaises(LinkError):inspect(SOURCE['root'],SOURCE)
    def test_canonical_and_redirect_aliases_are_recorded(self):
        page=self.html();page.redirects=['https://docs.example.com/guide/old']
        page.body=page.body.replace(b'<main>',b'<link rel="canonical" href="/guide/main"><main>')
        with patch('app.resources.fetch',return_value=page):resource=inspect(SOURCE['root'],SOURCE)
        self.assertIn('https://docs.example.com/guide/main',resource['aliases']);self.assertIn('https://docs.example.com/guide/old',resource['aliases'])
    def test_permanent_ledger_survives_empty_history(self):
        s=state();resource=pair()[0];queue.claim(s,[resource]);s['history']=[]
        self.assertFalse(_is_fresh(resource,s))
        self.assertFalse(_is_fresh(dict(resource,aliases=['https://docs.example.com/other']),s))
    def test_discovery_uses_real_links_not_model_urls(self):
        root=Page(SOURCE['root'],b'<a href="/guide/layout">Guide</a><a href="https://evil.test/a">bad</a><a href="/pricing">buy</a>','text/html',[])
        robots=Page('https://docs.example.com/robots.txt',b'User-agent: *\nDisallow:','text/plain',[])
        with patch('app.resources.fetch',side_effect=[root,robots]):items=discover(SOURCE,'seed')
        self.assertEqual({i['url'] for i in items},{'https://docs.example.com/guide','https://docs.example.com/guide/layout'})


class ScheduleTests(unittest.TestCase):
    def test_fifty_varied_slots_with_all_audiences(self):
        a=daily_plan('2026-11-02');b=daily_plan('2026-11-03')
        self.assertEqual(len(a),50);self.assertEqual(len({p['slot'] for p in a}),50)
        self.assertEqual({k:sum(p['audience']==k for p in a) for k in DAILY_MIX},DAILY_MIX)
        self.assertTrue(all(x['audience']!=y['audience'] for x,y in zip(a,a[1:])))
        self.assertNotEqual([p['due_at'][11:] for p in a],[p['due_at'][11:] for p in b]);self.assertEqual(a,daily_plan('2026-11-02'))
    def test_five_minute_runner_can_deliver_fifty_per_day(self):
        s=state();midnight=NOW.replace(hour=0,minute=0)
        for day in range(3):
            for minute in range(3,1440,5):
                now=midnight+timedelta(days=day,minutes=minute);brief=next_due(s,now)
                if brief:
                    if s['last_posted_at']:self.assertGreaterEqual(now-datetime.fromisoformat(s['last_posted_at']),MIN_GAP)
                    s['history'].append({'slot':brief['slot'],'posted_at':now.isoformat()});s['last_posted_at']=now.isoformat()
            self.assertEqual(len(s['history']),(day+1)*50)
    def test_no_catchup_or_duplicate_slot(self):
        s=state();brief=next_due(s,NOW);s['history']=[{'slot':brief['slot'],'posted_at':NOW.isoformat()}]
        self.assertIsNone(next_due(s,NOW))
    def test_legacy_posts_count_towards_cap(self):
        s=state();s['legacy_history']=[{'slot':f'old:{i}','posted_at':NOW.isoformat()} for i in range(50)]
        self.assertIsNone(next_due(s,NOW))
    def test_interval_and_unresolved_outcome_block(self):
        s=state();s['last_posted_at']=(NOW-timedelta(minutes=10)).isoformat();self.assertIsNone(next_due(s,NOW))
        s['pending']={'text':'unknown'}
        with self.assertRaises(ValueError):next_due(s,NOW)


class WriterTests(unittest.TestCase):
    def test_layout_two_urls_and_follow_line(self):
        text=render('vibecoders',LABELS,pair())
        self.assertTrue(text.startswith('For vibecoders\n\n'));self.assertEqual(text.count('https://'),2)
        self.assertTrue(text.endswith('Thank me later.\nFollow for more useful links.'))
    def test_model_cannot_change_urls(self):
        with patch('app.writer.complete',side_effect=[{'choices':[0,0]},GOOD]):self.assertEqual(generate('vibecoders',pair()),LABELS)
        with patch('app.writer.complete',return_value={'urls':['https://invented.invalid/']}) as call,self.assertRaises(DraftError):generate('vibecoders',pair())
        self.assertEqual(call.call_count,2)
    def test_model_cannot_invent_a_label_or_select_outside_the_options(self):
        for result in ({'choices':[999,0]},{'choices':[True,0]},{'labels':LABELS}):
            with patch('app.writer.complete',return_value=result),self.assertRaises(DraftError):generate('vibecoders',pair())
    def test_duplicate_dirty_urls_and_hype_are_rejected(self):
        for labels,resources in ((['Best free resources','CSS guide'],pair()),(LABELS,[pair()[0],pair()[0]]),(LABELS,[dict(pair()[0],url='https://docs.example.com/guide?utm_source=repeat'),pair()[1]])):
            with self.assertRaises(DraftError):render('vibecoders',labels,resources)
    def test_topic_grounding_and_relevance_fail_closed(self):
        for verdict in ({'allowed':False,'grounded':True,'relevant':True},{'allowed':True,'grounded':False,'relevant':True},{'allowed':True,'grounded':True,'relevant':False},{'allowed':'true','grounded':True,'relevant':True},{},None):
            with patch('app.writer.complete',return_value=verdict),self.assertRaises(DraftError):review('vibecoders',LABELS,pair())
    def test_crossed_or_invented_labels_are_rejected_before_ai_review(self):
        resources=pair()
        for labels in (list(reversed(LABELS)),['Managerial research guide',LABELS[1]],['Free unlimited deployment',LABELS[1]]):
            with patch('app.writer.complete',return_value=GOOD) as call,self.assertRaises(DraftError):review('vibecoders',labels,resources)
            call.assert_not_called()
    def test_review_outage_never_approves(self):
        with patch('app.writer.complete',side_effect=TimeoutError),self.assertRaises(TimeoutError):review('vibecoders',LABELS,pair())
    def test_cloudflare_contract(self):
        response=io.StringIO(json.dumps({'success':True,'result':{'response':json.dumps({'labels':LABELS})}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',return_value=response) as call:self.assertEqual(complete('rules',{}),{'labels':LABELS})
        self.assertIn('/ai/run/@cf/',call.call_args.args[0].full_url)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();base=Path(self.temp.name)
        self.patches=[patch.object(queue,'PATH',base/'resources.json'),patch.object(queue,'LEGACY_PATH',base/'legacy.json'),patch.object(queue,'RETIRED_PATH',base/'retired.json'),patch.object(queue,'summary'),patch.dict(os.environ,{'GITHUB_RUN_ID':'test','GITHUB_RUN_ATTEMPT':'1','GITHUB_OUTPUT':'','GITHUB_STEP_SUMMARY':''})]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def reserved(self):
        resources=pair();s=state();queue.claim(s,resources)
        s['pending']={'slot':datetime.now(timezone.utc).date().isoformat()+':resources-v1:2','audience':'vibecoders','labels':LABELS,'resources':resources,'text':render('vibecoders',LABELS,resources),'approval':approval_digest('vibecoders',LABELS,resources),'prepared_at':datetime.now(timezone.utc).isoformat(),'run_id':'test','run_attempt':'1'};queue.save(s)
    def network(self):return patch.object(queue,'inspect',side_effect=pair()),patch.object(queue,'source_for',return_value=SOURCE),patch.object(queue,'review')
    async def test_publish_once_and_keep_permanent_urls(self):
        self.reserved();c=SimpleNamespace(create_tweet=AsyncMock(return_value=SimpleNamespace(id='456')));a,b,d=self.network()
        with a,b,d,patch.object(queue,'client',return_value=c):await queue.publish()
        c.create_tweet.assert_awaited_once_with(text=render('vibecoders',LABELS,pair()))
        self.assertIsNone(queue.load()['pending']);self.assertEqual(len(queue.load()['used_urls']),2)
    async def test_timeout_keeps_reservation(self):
        self.reserved();c=SimpleNamespace(create_tweet=AsyncMock(side_effect=TimeoutError));a,b,d=self.network()
        with a,b,d,patch.object(queue,'client',return_value=c),self.assertRaises(TimeoutError):await queue.publish()
        self.assertIsNotNone(queue.load()['pending']);c.create_tweet.assert_awaited_once()
    async def test_broken_url_never_contacts_x_and_releases_safely(self):
        self.reserved()
        with patch.object(queue,'source_for',return_value=SOURCE),patch.object(queue,'inspect',side_effect=LinkError('broken')),patch.object(queue,'client') as c:await queue.publish()
        c.assert_not_called();self.assertIsNone(queue.load()['pending']);self.assertEqual(len(queue.load()['used_urls']),2)
    async def test_review_outage_before_send_releases_but_never_reuses_urls(self):
        self.reserved();a,b,d=self.network()
        with a,b,patch.object(queue,'review',side_effect=TimeoutError),patch.object(queue,'client') as c:await queue.publish()
        c.assert_not_called();self.assertIsNone(queue.load()['pending']);self.assertEqual(len(queue.load()['used_urls']),2)
    async def test_failed_label_pair_does_not_trap_future_discovery(self):
        s=state();s['catalog']=[{'url':r['url'],'source_id':r['source_id']} for r in pair()];queue.save(s)
        with patch.object(queue,'find_pair',return_value=pair()),patch.object(queue,'generate',side_effect=DraftError('no valid labels')),patch.object(queue,'client') as c,self.assertRaises(DraftError):await queue.prepare(start_now=True)
        c.assert_not_called();self.assertFalse(queue.load()['catalog']);self.assertIsNone(queue.load()['pending']);self.assertFalse(queue.load()['used_urls'])
    async def test_modified_text_and_old_run_are_rejected(self):
        self.reserved();s=queue.load();s['pending']['text']+='extra';queue.save(s)
        with patch.object(queue,'client') as c,self.assertRaises(ValueError):await queue.publish()
        c.assert_not_called();self.reserved()
        with patch.dict(os.environ,{'GITHUB_RUN_ATTEMPT':'2'}),self.assertRaises(ValueError):await queue.publish()
    async def test_prepare_claims_first_and_preview_never_saves(self):
        queue.save(state())
        with patch.object(queue,'find_pair',return_value=pair()),patch.object(queue,'generate',return_value=LABELS),patch.object(queue,'client') as c:await queue.prepare(start_now=True)
        c.assert_not_called();self.assertIsNotNone(queue.load()['pending']);self.assertEqual(len(queue.load()['used_urls']),2)
        queue.save(state());before=queue.PATH.read_text()
        with patch.object(queue,'find_pair',return_value=pair()),patch.object(queue,'generate',return_value=LABELS),patch.object(queue,'client') as c:await queue.prepare(preview=True)
        c.assert_not_called();self.assertEqual(before,queue.PATH.read_text())
    async def test_empty_pool_skips_without_recycling(self):
        queue.save(state())
        with patch.object(queue,'find_pair',return_value=None),patch.object(queue,'generate') as g:await queue.prepare(start_now=True)
        g.assert_not_called();self.assertIsNone(queue.load()['pending'])
    async def test_legacy_history_preserved_and_pending_blocks_migration(self):
        legacy={'history':[{'slot':'old','posted_at':NOW.isoformat(),'text':'old post'}],'pending':None,'last_posted_at':NOW.isoformat()}
        queue.LEGACY_PATH.write_text(json.dumps(legacy));before=queue.LEGACY_PATH.read_text()
        self.assertEqual(queue.load()['legacy_history'],legacy['history']);self.assertEqual(before,queue.LEGACY_PATH.read_text())
        legacy['pending']={'text':'uncertain'};queue.LEGACY_PATH.write_text(json.dumps(legacy))
        with self.assertRaises(ValueError):queue.load()
