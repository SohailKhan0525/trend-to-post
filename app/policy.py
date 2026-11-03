"""Fast excluded-topic checks; draft semantics are reviewed in writer.py."""
import re
import unicodedata

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

def has_excluded_terms(text):
    folded = unicodedata.normalize('NFKC', text)
    return bool(EXCLUDED_TERMS.search(folded) or NON_ENGLISH.search(folded)) or any(
        0x1F1E6 <= ord(c) <= 0x1F1FF for c in folded)

# No word boundaries for scripts in which words are not separated by spaces.
NON_ENGLISH = re.compile(
    r'राजनीति|राजनीतिक|चुनाव|सांसद|प्रधानमंत्री|राष्ट्रपति|सरकार|राजनैतिक|भारत|पाकिस्तान|युद्ध|'
    r'政治|政党|政黨|选举|選舉|選挙|議員|议员|议会|議会|大統領|总统|總統|首相|政府|'
    r'中国|中國|美国|美國|日本(?!語)|戦争|战争|戰爭|議會|インド政府|'
    r'\b(?:pol[ií]tic[oa]s?|partidos? pol[ií]ticos?|elecciones|president[ea]s?|gobiernos?|país|países|japón)\b',
    re.IGNORECASE)
