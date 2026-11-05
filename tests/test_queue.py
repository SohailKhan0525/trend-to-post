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

    async def test_rejected_first_four_do_not_starve_fifth_candidate_on_next_check(self):
        candidates=[{**SOURCE,'id':str(111+i),'source_ids':[str(111+i)],
                     'url':f'https://x.com/Acme/status/{111+i}'} for i in range(5)]
        by_id={item['id']:item for item in candidates}
        conn=self.connection()
        conn.get_tweet_by_id.side_effect=lambda ident: (SimpleNamespace(quoted_status_id='115')
                                                       if ident=='333' else by_id[ident])
        def review(candidate,*_):
            if candidate['id']!='115':
                raise queue.DraftError('Source gate rejected: new_announcement')
            return DRAFT
        with patch.object(queue,'save_and_push',side_effect=self.persist),\
             patch.object(queue.sources,'discover',AsyncMock(return_value=candidates)),\
             patch.object(queue.sources,'snapshot',side_effect=lambda value,now:value),\
             patch.object(queue.writer,'generate',side_effect=review) as generate:
            await queue.publish_slot(empty(),self.slot,conn)
            state=queue.load()
            self.assertEqual(state['candidate_cursor'],4)
            self.assertEqual(generate.call_count,4)
            self.assertEqual(state['used_source_ids'],[])
            conn.create_tweet.assert_not_awaited()
            with patch.object(queue,'utcnow',return_value=self.now+timedelta(minutes=10)):
                await queue.publish_slot(state,self.slot,conn)
        state=queue.load()
        self.assertEqual(generate.call_count,5)
        self.assertEqual(state['history'][0]['source_id'],'115')
        self.assertEqual(state['candidate_cursor'],0)
        conn.create_tweet.assert_awaited_once()

    async def test_expired_opportunity_preserves_attempted_candidate_cursor(self):
        state=empty();state['candidate_cursor']=3
        conn=self.connection()
        with patch.object(queue,'save_and_push',side_effect=self.persist),\
             patch.object(queue,'next_slot',return_value=None):
            await queue.send_once(state,self.slot,SOURCE,DRAFT,conn)
        self.assertEqual(queue.load()['candidate_cursor'],3)
        conn.create_tweet.assert_not_awaited()

    async def test_inspection_honors_persisted_cursor_without_changing_it(self):
        state=empty();state['candidate_cursor']=4
        self.persist(state)
        candidates=[{**SOURCE,'id':str(111+i)} for i in range(5)]
        with patch.object(queue,'client',return_value=self.connection()),\
             patch.object(queue.sources,'discover',AsyncMock(return_value=candidates)),\
             patch.object(queue.writer,'generate',return_value=DRAFT) as generate,\
             patch.object(queue,'save_and_push') as save:
            await queue.inspect()
        self.assertEqual(generate.call_args.args[0]['id'],'115')
        self.assertEqual(generate.call_count,1)
        self.assertEqual(queue.load()['candidate_cursor'],4)
        save.assert_not_called()

    async def test_editorial_reasons_are_bounded_and_visible_in_inspection_and_publishing(self):
        candidate={**SOURCE}
        reason='Gate rejected: new_announcement. '+'x'*800
        for inspect in (True,False):
            with self.subTest(inspect=inspect),\
                 patch.object(queue,'load',return_value=empty()),\
                 patch.object(queue,'client',return_value=self.connection()),\
                 patch.object(queue.sources,'discover',AsyncMock(return_value=[candidate])),\
                 patch.object(queue.writer,'generate',side_effect=queue.DraftError(reason)),\
                 patch.object(queue,'reject'),patch.object(queue,'summary') as output:
                if inspect:
                    await queue.inspect()
                else:
                    await queue.publish_slot(empty(),self.slot,self.connection())
            messages='\n'.join(call.args[0] for call in output.call_args_list)
            self.assertIn(reason[:600],messages)
            self.assertNotIn(reason[:601],messages)

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
            retained=queue.load()
            self.assertIn('111',retained['used_source_ids'])
            self.assertIn(SOURCE['urls'][0],retained['used_urls'])
            self.assertIn(queue.writer.fingerprint(DRAFT['text']),retained['used_texts'])
            self.assertIn(queue.writer.fingerprint(DRAFT['event_key']),retained['used_events'])
            conn.create_tweet.assert_awaited_once()

    async def test_invalid_session_can_recover_without_ambiguous_retry(self):
        conn=self.connection();conn.create_tweet.side_effect=InvalidSession('fixture')
        with patch.object(queue,'save_and_push',side_effect=self.persist),self.assertRaises(queue.SessionUnavailable):
            await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
        self.assertIsNone(queue.load()['pending'])
        self.assertTrue(queue.load()['check_session'])
        for ledger in ('used_source_ids','used_urls','used_texts','used_events'):
            self.assertEqual(queue.load()[ledger],[])

    async def test_known_unsent_cleanup_preserves_preexisting_ledger_values(self):
        state=empty()
        state.update(used_source_ids=['111','99'],used_urls=[SOURCE['urls'][0],'https://example.com/earlier'],
                     used_texts=[queue.writer.fingerprint(DRAFT['text']),'prior-text'],used_events=['prior-event'])
        original={key:list(state[key]) for key in ('used_source_ids','used_urls','used_texts','used_events')}
        source={**SOURCE,'source_ids':['111','112'],'urls':[*SOURCE['urls'],'https://example.com/new']}
        conn=self.connection();conn.create_tweet.side_effect=InvalidSession('fixture')
        with patch.object(queue,'save_and_push',side_effect=self.persist),self.assertRaises(queue.SessionUnavailable):
            await queue.send_once(state,self.slot,source,DRAFT,conn)
        released=queue.load()
        for ledger,values in original.items():
            self.assertEqual(released[ledger],values)

    async def test_expired_reservation_before_create_releases_its_ledger_additions(self):
        conn=self.connection()
        with patch.object(queue,'save_and_push',side_effect=self.persist),\
             patch.object(queue,'utcnow',side_effect=[self.now,self.now+timedelta(seconds=91)]):
            await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
        conn.create_tweet.assert_not_awaited()
        state=queue.load()
        self.assertIsNone(state['pending'])
        for ledger in ('used_source_ids','used_urls','used_texts','used_events'):
            self.assertEqual(state[ledger],[])

    async def test_source_expiry_before_create_releases_its_ledger_additions(self):
        conn=self.connection()
        with patch.object(queue,'save_and_push',side_effect=self.persist),\
             patch.object(queue.sources,'validate',side_effect=[SOURCE,queue.sources.SourceError('expired')]):
            await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
        conn.create_tweet.assert_not_awaited()
        state=queue.load()
        self.assertIsNone(state['pending'])
        for ledger in ('used_source_ids','used_urls','used_texts','used_events'):
            self.assertEqual(state[ledger],[])

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

    async def test_readback_accepts_nested_quote_id_without_legacy_quote_id(self):
        conn=self.connection()
        conn.get_tweet_by_id.return_value=SimpleNamespace(quoted_status_id=None,quote=SimpleNamespace(id='111'))
        with patch.object(queue,'save_and_push',side_effect=self.persist):
            await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
        state=queue.load()
        self.assertNotIn('verification_pending',state)
        self.assertNotIn('pause_reason',state)
        self.assertEqual(state['history'][0]['tweet_id'],'333')
        conn.create_tweet.assert_awaited_once()

    async def test_missing_quote_metadata_stays_pending_and_does_not_pause(self):
        conn=self.connection()
        conn.get_tweet_by_id.return_value=SimpleNamespace(quoted_status_id=None,quote=None)
        with patch.object(queue,'save_and_push',side_effect=self.persist):
            await queue.send_once(empty(),self.slot,SOURCE,DRAFT,conn)
        state=queue.load()
        self.assertEqual(state['verification_pending']['tweet_id'],'333')
        self.assertNotIn('pause_reason',state)
        self.assertIsNone(next_slot(state,self.now+timedelta(hours=2)))
        conn.get_tweet_by_id.return_value=SimpleNamespace(quote=SimpleNamespace(id='111'))
        with patch.object(queue,'save_and_push',side_effect=self.persist):
            self.assertTrue(await queue.verify_quote(conn,state))
        self.assertNotIn('verification_pending',queue.load())
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

    def test_legacy_history_source_aliases_article_links_and_fingerprints_migrate(self):
        queue.LEGACY.write_text(json.dumps({
            'used_source_ids':['900'],'used_events':['prior-event-hash'],
            'history':[{'source_id':'901','text':'An older original post.'}],
        }))
        queue.OLDER_QUOTES.write_text(json.dumps({
            'used_source_ids':['0010',True,None,'bad'],'used_texts':['prior-text-hash',None],
            'used_urls':['https://www.example.com/news/a/?utm_source=x','https://example.com/'],
            'history':[
                {'source_id':'11','tweet_id':'99999','source_ids':[12,'13',False],
                 'edit_tweet_ids':['14'],'edit_control':{'edit_tweet_ids':['15']},
                 'urls':['https://example.com/news/b?ref=share'],
                 'source':{'id':'16','source_ids':['17'],'edit_control':{'edit_tweet_ids':['18']},
                           'urls':[{'expanded_url':'https://example.com/news/c#top'}]},
                 'text':'Our prior quote text.','event_key':'editor version 2 release'},
                {'source_id':None,'source_ids':'not-a-list','urls':[None,{}],
                 'text':None,'event_key':False,'article_url':'https://example.com/news/d'},
                None,
            ],
        }))
        state=queue.load()
        self.assertEqual(set(state['used_source_ids']),{'10','11','12','13','14','15','16','17','18','900','901'})
        self.assertNotIn('99999',state['used_source_ids'])
        self.assertEqual(set(state['used_urls']),{f'https://example.com/news/{letter}' for letter in 'abcd'})
        self.assertIn('prior-text-hash',state['used_texts'])
        self.assertIn(queue.writer.fingerprint('Our prior quote text.'),state['used_texts'])
        self.assertIn(queue.writer.fingerprint('An older original post.'),state['used_texts'])
        self.assertIn('prior-event-hash',state['used_events'])
        self.assertIn(queue.writer.fingerprint('editor version 2 release'),state['used_events'])
