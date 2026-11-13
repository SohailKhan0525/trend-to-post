"""Fast excluded-topic checks; draft semantics are reviewed in writer.py."""
import re
import unicodedata
from pathlib import Path

# Prefer high reach while allowing relevant smaller sources into the queue.
MIN_SOURCE_VIEWS = 10_000

EXCLUDED_TERMS = re.compile(
    r'\b(?:politic\w*|geopolitic\w*|elections?|vot(?:e|es|ing)|ballots?|'
    r'presidents?|prime minister|parliament\w*|governments?|senat\w*|'
    r'political part(?:y|ies)|democrats?|republicans?|nationalis\w*|'
    r'countries|country|nations?|nationalit\w*|patrioti\w*|'
    r'military|wars?|invasion|immigration|foreign policy|'
    r'united states|united kingdom|usa|uk|india|pakistan|china|russia|'
    r'ukraine|israel|palestin\w*|iran|iraq|afghanistan|japan|korea|'
    r'germany|france|canada|australia|brazil|mexico|turkey|taiwan|'
    r'trump|biden|modi|putin|zelensky\w*|netanyahu|xi jinping)\b', re.I)

# Read the bundled names once. CJK text does not need spaces between words.
def country_pattern():
    names = Path(__file__).with_name("region_names.txt").read_text(encoding="utf-8").splitlines()
    words, unspaced = [], []
    for name in sorted(names,key=len,reverse=True):
        if not name or name.startswith("#") or name == "Reunión":
            continue
        group = unspaced if re.search(r"[\u3040-\u30ff\u3400-\u9fff]",name) else words
        group.append(re.escape(name))
    return re.compile(r"(?<!\w)(?:"+"|".join(words)+r")(?!\w)|"+"|".join(unspaced),re.I)


COUNTRIES = country_pattern()
# Lowercase Spanish "reunión" is the ordinary word for a meeting.
CASE_SENSITIVE_NAMES = re.compile(r"(?<!\w)(?:US|U\.S\.|U\.K\.|UAE|PRC|Reunión)(?!\w)")


def has_excluded_terms(text):
    folded = unicodedata.normalize('NFKC', text)
    return bool(EXCLUDED_TERMS.search(folded) or NON_ENGLISH.search(folded) or COUNTRIES.search(folded) or CASE_SENSITIVE_NAMES.search(folded)) or any(
        0x1F1E6 <= ord(c) <= 0x1F1FF for c in folded)

# No word boundaries for scripts in which words are not separated by spaces.
NON_ENGLISH = re.compile(
    r'राजनीति|राजनीतिक|चुनाव|सांसद|प्रधानमंत्री|राष्ट्रपति|सरकार|राजनैतिक|भारत|पाकिस्तान|युद्ध|'
    r'政治|政党|政黨|选举|選舉|選挙|議員|议员|议会|議会|大統領|总统|總統|首相|政府|'
    r'中国|中國|美国|美國|日本(?!語)|戦争|战争|戰爭|議會|インド政府|'
    r'\b(?:pol[ií]tic[oa]s?|partidos? pol[ií]ticos?|elecciones|president[ea]s?|gobiernos?|país|países|japón)\b',
    re.IGNORECASE)
