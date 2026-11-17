import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import reply_writer


NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)
SOURCE = {'id': '123', 'author': 'dev', 'author_id': '456', 'views': 12_000,
          'created_at': NOW.isoformat(), 'text': 'Retrying a build clears the original failure log.'}
DRAFT = {'text': 'Keep the failed log beside the retry.', 'event_key': 'Retry clears build logs',
         'premise': 'The original failure log is cleared on retry.'}
ASSESSMENT = {**DRAFT, 'reply_mode': 'observation', **dict.fromkeys(reply_writer.SOURCE_FLAGS, True)}
REVIEW = {**dict.fromkeys(reply_writer.FLAGS, True), 'source_evidence': SOURCE['text']}


class PersonalReplyTests(unittest.TestCase):
    def test_only_bounded_relevant_public_copy_reaches_both_model_calls(self):
        memory = {
            'recent_own': [{'kind': 'original', 'text': 'a' * 300} for _ in range(8)],
            'people': [{'author_id': '456', 'notes': 'private detail'}],
            'conversations': [
                {'author_id': '789', 'text': 'other author'},
                *[{'author_id': '456', 'text': 'b' * 300, 'source_text': 'unrelated detail'} for _ in range(6)],
            ],
        }
        with patch.object(reply_writer, 'complete', side_effect=[ASSESSMENT, REVIEW]) as model:
            result = reply_writer.generate(SOURCE, {}, NOW, memory=memory, angle='c' * 800)
        self.assertEqual(result, DRAFT)
        for call in model.call_args_list:
            data = call.args[1]
            self.assertEqual(data['public_memory'], {
                'previous_replies_to_this_author': ['b' * 120] * 3,
                'recent_originals': ['a' * 140] * 2,
            })
            self.assertEqual(data['source']['text'], SOURCE['text'])
            self.assertEqual(data['editorial_angle'], 'c' * 180)

    def test_memory_and_angle_cannot_supply_the_review_evidence(self):
        unrelated = 'The indexer only supports Markdown.'
        memory = {'conversations': [{'author_id': SOURCE['author_id'], 'text': unrelated}]}
        wrong = {**REVIEW, 'source_evidence': unrelated}
        with patch.object(reply_writer, 'complete', side_effect=[ASSESSMENT, wrong, wrong]), \
             self.assertRaises(reply_writer.ReviewFormatError):
            reply_writer.generate(SOURCE, {}, NOW, memory=memory, angle=unrelated)

    def test_source_without_author_id_cannot_match_an_unknown_person(self):
        memory = {'conversations': [{'author_id': None, 'text': 'old reply'}]}
        self.assertEqual(reply_writer.editorial_context({}, memory, ''), {})
