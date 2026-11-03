from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from app import linkedin


class LinkedInTests(TestCase):
    def test_archive_latest_and_no_second_generation(self):
        sample=linkedin.sections((linkedin.ROOT/"2026-11-03.txt").read_text())
        drafts=dict(zip(linkedin.FIELDS,sample))
        with TemporaryDirectory() as temp, patch.object(linkedin,"complete",side_effect=[drafts,dict.fromkeys(drafts,True)]) as ai:
            directory=Path(temp)
            self.assertTrue(linkedin.make_day("2026-11-04",directory))
            self.assertEqual(linkedin.sections((directory/"latest.txt").read_text()),sample)
            self.assertEqual((directory/"2026-11-04.txt").read_bytes(),(directory/"latest.txt").read_bytes())
            self.assertFalse(linkedin.make_day("2026-11-04",directory))
            self.assertEqual(ai.call_count,2)
