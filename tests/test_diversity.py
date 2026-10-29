import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from app.diversity import repetition_reason, story_reason
from app.writer import generate, DraftError, SYSTEM, GROUNDING, NOVELTY

HISTORY = json.loads((Path(__file__).parent/'fixtures/repetition_history.json').read_text())
NOW = datetime(2026,10,29,12,20,tzinfo=timezone.utc)
SOURCE = {'id':'123','text':'OpenCode adds a local diff viewer for reviewing file changes before applying them.'}
DRAFT = {'should_post':True,'hook':'The diff is the useful part.',
         'body':'Reviewing edits before applying them puts the decision back with the developer.'}

class RepetitionRegressionTests(unittest.TestCase):
    def test_observed_repeated_hooks_are_blocked(self):
        for index in (3,4,5,6,8,10,11,12,16,19,20):
            with self.subTest(index=index):
                self.assertIsNotNone(repetition_reason(HISTORY[index]['text'],HISTORY[:index]))
    def test_elongation_and_brand_swap_do_not_evade_hook_guard(self):
        old=[{'text':"AI's getting a little too big for its britches\n\nOld body."}]
        for hook in ("ChatGPT's getting a little too big for its britches...", "AI's getting too big for its britches"):
            self.assertIsNotNone(repetition_reason(hook+'\n\nCompletely different content here.',old))
        self.assertIsNotNone(repetition_reason('ohhh myyy what the hell?\n\nDifferent content.',[{'text':'ohhhhh myyy, what the hell??\n\nOriginal content.'}]))
    def test_changed_hook_does_not_hide_same_body(self):
        old=[{'text':'An earlier hook\n\nReviewing edits before applying them puts the decision back with the developer.'}]
        self.assertIsNotNone(repetition_reason('Different opener entirely\n\nReviewing edits before applying them puts the decision back with the developer.',old))
    def test_same_story_different_author(self):
        old=[{'text':'Old hook\n\nAnthropic is working on drug discovery.', 'posted_at':NOW.isoformat()}]
        self.assertIsNotNone(story_reason('Claude enters biotech with a new announcement',old,NOW))
    def test_live_preview_api_credit_repeat_is_blocked(self):
        self.assertIsNotNone(story_reason('Limited API credits for ChatGPT Pro and Business subscribers only.',HISTORY,NOW))
    def test_story_cooldown_expires(self):
        old=[{'text':'Old hook\n\nAnthropic is working on drug discovery.', 'posted_at':(NOW-timedelta(hours=49)).isoformat()}]
        self.assertIsNone(story_reason('Claude enters biotech with a new announcement',old,NOW))
    def test_unrelated_story_same_company_allowed(self):
        self.assertIsNone(story_reason('Anthropic adds a diff viewer to Claude Code.',HISTORY,NOW))
        self.assertIsNone(repetition_reason(DRAFT['hook']+'\n\n'+DRAFT['body'],HISTORY))
    def test_fresh_source_memory_used(self):
        old=[{'text':'A joke\n\nA short observation.', 'source_text':SOURCE['text'], 'posted_at':NOW.isoformat()}]
        self.assertIsNotNone(story_reason(SOURCE['text'],old,NOW))

class ModelGateTests(unittest.TestCase):
    def history(self):
        return [{'text':'An old hook\n\nA contaminated biotech takeover story.', 'posted_at':datetime.now(timezone.utc).isoformat()}]
    def test_old_posts_never_enter_writer_or_grounding_context(self):
        with patch('app.writer.complete',side_effect=[DRAFT,{'grounded':True,'specific':True},{'novel':True}]) as call:
            self.assertIsNotNone(generate(SOURCE,self.history()))
        self.assertEqual(call.call_args_list[0].args[0],SYSTEM)
        data=call.call_args_list[0].args[1]
        self.assertEqual(set(data),{'CURRENT_SOURCE','ANGLE','ALLOW_SWEARING'})
        self.assertNotIn('biotech',json.dumps(data))
        self.assertEqual(call.call_args_list[1].args[0],GROUNDING)
        self.assertNotIn('biotech',json.dumps(call.call_args_list[1].args[1]))
        self.assertEqual(call.call_args_list[2].args[0],NOVELTY)
    def test_unsupported_claim_rejected_before_novelty(self):
        with patch('app.writer.complete',side_effect=[DRAFT,{'grounded':False,'specific':True}]) as call:
            with self.assertRaisesRegex(DraftError,'grounding'):
                generate(SOURCE,self.history())
        self.assertEqual(call.call_count,2)
    def test_same_event_semantic_gate(self):
        with patch('app.writer.complete',side_effect=[DRAFT,{'grounded':True,'specific':True},{'novel':False}]):
            with self.assertRaisesRegex(DraftError,'Same story'):
                generate(SOURCE,self.history())
    def test_malformed_or_string_verdict_fails_closed(self):
        for verdict in ({'grounded':'true','specific':True},{'grounded':True},{'grounded':True,'specific':False},None):
            with self.subTest(verdict=verdict),patch('app.writer.complete',side_effect=[DRAFT,verdict]):
                with self.assertRaises(DraftError):generate(SOURCE,[])
    def test_precheck_avoids_spending_on_known_repeated_story(self):
        with patch('app.writer.complete') as call:
            with self.assertRaises(DraftError):
                generate({'text':'Claude enters biotech.'},self.history())
        call.assert_not_called()
