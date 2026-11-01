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
from app.schedule import daily_plan,next_due,POSTS_PER_DAY,MIN_GAP
from app.content import DAILY_MIX
from app.policy import approval_digest,REVIEW_SYSTEM
from app.writer import generate,validate,complete,review_topics,DraftError

NOW=datetime(2026,10,31,12,0,tzinfo=timezone.utc)
TEXT="Hello solo founders\n\nLet's connect"

def state():return {'version':3,'history':[],'last_posted_at':None,'pending':None}

class ScheduleTests(unittest.TestCase):
    def test_fifty_unique_spread_slots_and_balanced_categories(self):
        for day in ('2026-10-31','2026-11-01','2026-12-31'):
            plan=daily_plan(day)
            self.assertEqual(len(plan),50)
            self.assertEqual(len({s['slot'] for s in plan}),50)
            self.assertEqual(len({s['topic'] for s in plan}),50)
            times=[datetime.fromisoformat(p['due_at']) for p in plan]
            self.assertTrue(all(t.date().isoformat()==day for t in times))
            self.assertTrue(all(b-a>=timedelta(minutes=22.8) for a,b in zip(times,times[1:])))
            self.assertTrue(all(a['category']!=b['category'] for a,b in zip(plan,plan[1:])))
            self.assertEqual({c:sum(s['category']==c for s in plan) for c in {s['category'] for s in plan}},DAILY_MIX)
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
    def test_five_minute_dispatch_can_deliver_all_fifty_without_bursts(self):
        s=state()
        midnight=NOW.replace(hour=0,minute=0)
        for day in range(3):
            for minute in range(3,1440,5):
                now=midnight+timedelta(days=day,minutes=minute)
                brief=next_due(s,now)
                if brief:
                    if s['last_posted_at']:
                        self.assertGreaterEqual(now-datetime.fromisoformat(s['last_posted_at']),MIN_GAP)
                    s['history'].append({'slot':brief['slot'],'posted_at':now.isoformat()})
                    s['last_posted_at']=now.isoformat()
            self.assertEqual(len(s['history']),(day+1)*POSTS_PER_DAY)
    def test_rollout_does_not_collide_with_old_slots_or_reset_daily_cap(self):
        s=state();today=NOW.date().isoformat()
        s['history']=[{'slot':f'{today}:{i}','posted_at':NOW.isoformat()} for i in range(20)]
        brief=next_due(s,NOW)
        self.assertIn(':conversation-v2:',brief['slot'])
        s['history'] += [{'slot':f'{today}:conversation-v2:{i}','posted_at':NOW.isoformat()} for i in range(30)]
        self.assertIsNone(next_due(s,NOW))

class WriterTests(unittest.TestCase):
    def test_short_original_and_optional_blank_line(self):
        self.assertEqual(validate(TEXT,[]),TEXT)
        self.assertEqual(validate('Your terminal setup has more plugins than your product has features.',[]),'Your terminal setup has more plugins than your product has features.')
    def test_invalid_content(self):
        for text in ('short','a'*281,TEXT+' @somebody',TEXT+' https://example.com',TEXT+'\u200b',TEXT+' I have 999 customers',"Hello founders\n\nI'll use every product you share today."):
            with self.subTest(text=text),self.assertRaises(DraftError):validate(text,[])
    def test_exact_and_near_duplicates(self):
        with self.assertRaises(DraftError):validate(TEXT,[{'text':TEXT.lower()}])
        with self.assertRaises(DraftError):validate(TEXT.replace("Let's connect","Let's connect."),[{'text':TEXT}])
    def test_repeated_opening(self):
        with self.assertRaises(DraftError):validate('Hello solo founders\n\nWhich customer conversation changed how you thought about your pricing?',[{'text':TEXT}])
    def test_fresh_take_allowed(self):
        self.assertIsNotNone(validate('Hot take: a tiny working feature beats another weekend choosing a framework.',[{'text':TEXT}],'coding_take'))
    def test_generator_selects_fresh_candidate(self):
        fresh='Hot take: a dependable tool beats a beautiful rewrite that never ships.'
        with patch('app.writer.complete',side_effect=[{'posts':[TEXT,fresh]},{'allowed':True}]):
            self.assertEqual(generate({'brief':'A coding opinion','category':'coding_take'},[{'text':TEXT}]),fresh)
    def test_requested_formats_pass(self):
        for category,text in (
            ('connect',"Hello fake founders\n\nLet's connect"),
            ('showcase',"Hello founders\n\nComment your startup product. I'll try it if I need it."),
            ('coding_take',"If you're coding 100% on a laptop, you're probably new to coding."),
            ('curiosity','is there a word between yes and no?'),
            ('choice','would you rather lose your bookmarks or your screenshots?'),
            ('relatable','a temporary fix has suspiciously permanent energy.'),
            ('ai_question','what do you check first in AI-generated code?'),
            ('coding_take','Your editor theme is not a product feature.')):
            self.assertEqual(validate(text,[],category),text)
    def test_generic_questions_and_personal_stories_fail(self):
        unwanted=("I'm still trying to figure out how to make my product discoverable. What's the one thing you wish you'd known about getting noticed early on?",
                  "Hello founders\n\nWhat's your biggest challenge?",
                  "Hello founders\n\nWhat did you cut from your MVP to ship it?")
        for text in unwanted:
            with self.subTest(text=text),self.assertRaises(DraftError):validate(text,[],'connect')
    def test_greeting_can_repeat_with_different_invitation(self):
        self.assertIsNotNone(validate("Hello founders\n\nDrop your side project and what it does.",
            [{'text':"Hello founders\n\nShare your latest tool."}],'showcase'))
    def test_all_repeated_candidates_are_still_blocked(self):
        with patch('app.writer.complete',return_value={'text':TEXT}) as call:
            with self.assertRaises(DraftError):generate({'brief':'Founder question','category':'connect'},[{'text':TEXT}])
        self.assertEqual(call.call_count,2)
    def test_invalid_json_gets_one_bounded_retry(self):
        with patch('app.writer.complete',side_effect=[DraftError('Invalid JSON'),{'text':TEXT},{'allowed':True}]) as call:
            self.assertEqual(generate({'brief':'Founder question','category':'connect'},[]),TEXT)
        self.assertEqual(call.call_count,3)
    def test_two_malformed_responses_stop(self):
        with patch('app.writer.complete',side_effect=DraftError('Invalid JSON')) as call:
            with self.assertRaises(DraftError):generate({'brief':'Founder question','category':'connect'},[])
        self.assertEqual(call.call_count,2)
    def test_model_line_breaks_are_normalized_safely(self):
        response=io.StringIO(json.dumps({'success':True,'result':{'response':'{"text":"Hello solo founders\n\nWhat are you building for your first customer?"}'}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',return_value=response):
            draft=complete('instructions',{})
            self.assertIn('\n\n',validate(draft['text'],[]))
    def test_cloudflare_request(self):
        response=io.StringIO(json.dumps({'success':True,'result':{'response':json.dumps({'posts':[TEXT]})}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',return_value=response) as call:
            self.assertEqual(complete('instructions',{'brief':'hello'}),{'posts':[TEXT]})
            request=call.call_args.args[0]
            self.assertIn('/ai/run/@cf/',request.full_url)
            self.assertEqual(json.loads(request.data)['messages'][0]['content'],'instructions')
    def test_questions_need_one_question_without_an_answer(self):
        for text in ('is there a word between yes and no? Maybe.',
                     'Tabs or spaces? And why?', 'Is there a word for this feeling'):
            with self.subTest(text=text),self.assertRaises(DraftError):validate(text,[],'curiosity')
    def test_padded_observation_is_rejected(self):
        with self.assertRaises(DraftError):
            validate('A password manager asking for its own password is like your aunt asking you to explain how the internet works.',[],'relatable')
    def test_hundred_percent_does_not_allow_other_fake_percentages(self):
        with self.assertRaises(DraftError):validate('100% effort produces 90% better code every single time.',[],'coding_take')
    def test_politics_countries_and_national_flags_are_rejected(self):
        for text in ('Which political party makes the best software?',
                     'Which country has the best developers?', 'Coding in India is pretty interesting.',
                     'What do you think about the president?', 'Hello developers 🇯🇵 let us connect today.'):
            with self.subTest(text=text),self.assertRaises(DraftError):validate(text,[])
    def test_semantic_review_is_strict_and_fail_closed(self):
        for verdict in ({'allowed':False},{'allowed':'true'},{'allowed':1},{},{'allowed':True,'reason':'ok'},None):
            with self.subTest(verdict=verdict),patch('app.writer.complete',return_value=verdict),self.assertRaises(DraftError):
                review_topics('What language do you use to chat with your AI assistant?')
        with patch('app.writer.complete',return_value={'allowed':True}) as call:
            review_topics('is there a word between yes and no?')
        self.assertEqual(call.call_args.args[0],REVIEW_SYSTEM)
        self.assertEqual(call.call_args.kwargs['temperature'],0)
    def test_topic_rejection_regenerates_and_reviews_new_text(self):
        rejected='Which place has the smartest founders?'
        approved='is there a word between yes and no?'
        with patch('app.writer.complete',side_effect=[{'text':rejected},{'allowed':False},{'text':approved},{'allowed':True}]) as call:
            self.assertEqual(generate({'category':'curiosity','brief':'a curious word question'},[]),approved)
        self.assertEqual(call.call_count,4)
    def test_topic_service_outage_never_returns_unreviewed_text(self):
        with patch('app.writer.complete',side_effect=[{'text':'is there a word between yes and no?'},TimeoutError]):
            with self.assertRaises(TimeoutError):generate({'category':'curiosity','brief':'a word question'},[])
    def test_exact_duplicate_is_remembered_beyond_two_days(self):
        history=[{'text':TEXT}]+[{'text':f'distinct older sample {i}'} for i in range(500)]
        with self.assertRaises(DraftError):validate(TEXT,history,'connect')
    def test_unicode_normalization_keeps_words_for_duplicates(self):
        text='¿Existe una palabra entre sí y no?'
        with self.assertRaises(DraftError):validate(text,[{'text':text}], 'curiosity')

class QueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'state.json'
        self.p=patch.object(queue,'PATH',self.path);self.p.start()
        self.messages=patch.object(queue,'summary');self.summary=self.messages.start()
        self.env=patch.dict(os.environ,{'GITHUB_RUN_ID':'test','GITHUB_RUN_ATTEMPT':'1','GITHUB_OUTPUT':'','GITHUB_STEP_SUMMARY':''});self.env.start()
    def tearDown(self):self.messages.stop();self.env.stop();self.p.stop();self.temp.cleanup()
    def reserved(self):
        s=state();s['pending']={'slot':datetime.now(timezone.utc).date().isoformat()+':3','category':'connect','text':TEXT,'topic_approval':approval_digest(TEXT),'prepared_at':datetime.now(timezone.utc).isoformat(),'run_id':'test','run_attempt':'1'};queue.save(s)
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
    async def test_all_category_preview_cannot_change_state_or_publish(self):
        queue.save(state());before=self.path.read_text()
        with patch.object(queue,'generate',return_value=TEXT) as g,patch.object(queue,'client') as c:
            await queue.prepare(preview=True,all_categories=True)
        c.assert_not_called();self.assertEqual(before,self.path.read_text())
        self.assertEqual({call.args[0]['category'] for call in g.call_args_list},set(DAILY_MIX))
    async def test_modified_or_unreviewed_reservation_cannot_publish(self):
        for approval in (None,'old-policy-digest',approval_digest('different text')):
            self.reserved();s=queue.load();s['pending']['topic_approval']=approval;queue.save(s)
            with patch.object(queue,'client') as c,self.assertRaises(ValueError):await queue.publish()
            c.assert_not_called();self.assertIsNotNone(queue.load()['pending'])

    async def test_due_generation_failure_is_visible_and_does_not_reserve(self):
        queue.save(state());before=self.path.read_text()
        with patch.object(queue,'generate',side_effect=DraftError('No fresh candidate')):
            with self.assertRaises(DraftError):await queue.prepare(start_now=True)
        self.assertEqual(before,self.path.read_text())
        self.assertIn('::error::Due post failed generation',self.summary.call_args.args[0])
