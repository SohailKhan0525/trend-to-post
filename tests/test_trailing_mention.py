import unittest

from app import mentions, sources
from test_mentions import NOW, mention


class TrailingMentionTests(unittest.TestCase):
    def test_punctuated_request_to_the_account_is_an_invitation(self):
        for text in ('why is my build failing, @qofenoai?', 'explain this error, @qofenoai',
                     'How is it going, @qofenoai?', 'Is this reproducible? @QofenoAI',
                     '怎么修好这个问题，@qofenoai？'):
            with self.subTest(text=text):
                self.assertEqual(mentions.snapshot(mention(text), NOW)['intent'], 'invite')

    def test_credits_quoted_speech_and_references_stay_out_of_the_inbox(self):
        for text in ('Who built @qofenoai?', 'Could the tip be from @qofenoai?',
                     'A great read, @qofenoai', 'Which fix worked? cc: @qofenoai',
                     'I saw “why is my build failing, @qofenoai?”',
                     'Are you shipping, @other, @qofenoai?'):
            with self.subTest(text=text), self.assertRaises(sources.SourceError):
                mentions.snapshot(mention(text), NOW)
