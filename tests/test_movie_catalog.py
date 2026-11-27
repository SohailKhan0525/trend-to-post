from datetime import datetime, timezone
import io
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from app import movie_catalog as catalog


NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
LINE = 'A spare key opens both doors.'


def page(title='Example', source=None, rev=22):
    if source is None:
        source = "This is a feature film.\n==Dialogue==\n:'''Lin''': '''" + LINE + "'''"
    return {'ns': 0, 'title': title,
            'revisions': [{'revid': rev, 'slots': {'main': {'content': source}}}]}


class CatalogTests(unittest.TestCase):
    def test_requires_named_screen_dialogue_and_exact_revision(self):
        actual = list(catalog._page_lines(page('Example & One')))
        self.assertEqual(actual, [{'quote': LINE, 'title': 'Example & One', 'speaker': 'Lin',
                                   'source': 'https://en.wikiquote.org/w/index.php?title=Example_%26_One&oldid=22'}])
        episode = "A Japanese animated series.\n==Episodes==\n===An Engine [1.03]===\n:'''Lin:''' '''" + LINE + "'''"
        self.assertEqual([r['quote'] for r in catalog._page_lines(page(source=episode))], [LINE])

    def test_rejects_unattributed_ambiguous_and_non_dialogue_sections(self):
        for heading, raw in (
                ('Dialogue', '* ' + LINE), ('Dialogue', ":'''Unknown''': " + LINE),
                ('Dialogue', ":'''Lin''': ''[sings]'' " + LINE),
                ('Dialogue', ":'''Lin''': They know what happened."),
                ('Dialogue', ":'''Lin''': The wheels [[turn]] again."),
                ('Adjacent text', ":'''Lin''': " + LINE), ('Lyrics', ":'''Lin''': " + LINE),
                ('About the film==\n===Dialogue=', ":'''Lin''': " + LINE)):
            with self.subTest(heading=heading, raw=raw):
                source = 'This is a feature film.\n==' + heading + '==\n' + raw
                self.assertFalse(list(catalog._page_lines(page(source=source))))
        for extra in ('\n[[Category:American musicals]]',):
            self.assertFalse(list(catalog._page_lines(page(source="A film.\n==Dialogue==\n:'''Lin''': " + LINE + extra))))

    def test_bounds_reads_skips_prior_titles_and_uses_callers_line_checks(self):
        history = [{'movie_credit': {'title': 'Past Work (2012)',
                                    'source': 'https://en.wikiquote.org/wiki/Local_Name'}}]
        members = [{'ns': 0, 'title': x} for x in ('Past Work (2012 film)', 'Local Name', 'Seed', 'Fresh', 'Also Fresh')]
        bad = page('Fresh', "A film.\n==Dialogue==\n:'''Lin''': '''A gentle rain is falling.'''")
        with patch.object(catalog, '_query', side_effect=[{'categorymembers': members},
                          {'pages': [bad, page('Also Fresh')]}]) as query:
            result = catalog.find_quote(history, exclude_titles=['Seed'], now=NOW,
                                        accept=lambda entry: entry['quote'] == LINE)
        self.assertEqual(result['title'], 'Also Fresh')
        self.assertEqual(query.call_count, 2)
        self.assertEqual(set(query.call_args_list[1].kwargs['titles'].split('|')), {'Fresh', 'Also Fresh'})

    def test_ignores_hidden_lines_and_song_blocks_before_a_new_scene(self):
        raw = ("A film.\n==Dialogue==\n<!--\n:'''Lin''': '''The first line is hidden.'''\n-->\n"
               "<nowiki>\n:'''Lin''': '''The second line is hidden.'''\n</nowiki>\n"
               ":''[The band begins singing.]''\n:'''Lin''': '''The third line is sung.'''\n"
               "----\n:'''Lin''': '''" + LINE + "'''")
        self.assertEqual([r['quote'] for r in catalog._page_lines(page(source=raw))], [LINE])

    def test_excludes_ordinary_turns_generic_highlights_and_unverified_speaker_headings(self):
        raw = ("A film.\n==Dialogue==\n:'''Lin''': Ordinary dialogue is excluded.\n"
               ":'''Lin''': '''Nice to meet you.'''\n:'''Lin''': Only a '''small''' part is bold.\n"
               "==Unrelated Name==\n* No evidence identifies the speaker.\n"
               "==Lin==\n* " + LINE)
        self.assertEqual([r['quote'] for r in catalog._page_lines(page(source=raw))], [LINE])

    def test_empty_categories_end_and_next_wakes_rotate(self):
        with patch.object(catalog, '_query', return_value={'categorymembers': []}) as query:
            self.assertIsNone(catalog.find_quote([], now=NOW))
            self.assertEqual(query.call_count, 2)
            first = query.call_args_list.copy()
            self.assertIsNone(catalog.find_quote([], now=NOW.replace(hour=14)))
        self.assertNotEqual(first, query.call_args_list[2:])

    def test_rate_limit_stops_next_category_reads(self):
        failure = HTTPError(catalog.API, 429, 'Rate limited', {}, io.BytesIO(b''))
        with patch.object(catalog, '_query', side_effect=failure) as query:
            self.assertIsNone(catalog.find_quote([], now=NOW))
        query.assert_called_once()

    def test_missing_metadata_unexpected_page_and_nonscreen_work_do_not_supply_quotes(self):
        for obj in ({}, page(rev=None), {**page(), 'ns': 1}, {**page(), 'redirect': True},
                    page('Example/Season 1'), page(source="This is a novel.\n==Dialogue==\n:'''Lin''': " + LINE)):
            self.assertFalse(list(catalog._page_lines(obj)), obj)
        with patch.object(catalog, '_query', side_effect=[{'categorymembers': [{'ns': 0, 'title': 'Fresh'}]},
                          {'pages': [page('Unrequested page')]}, {'categorymembers': []}]):
            self.assertIsNone(catalog.find_quote([], now=NOW))


if __name__ == '__main__':
    unittest.main()
