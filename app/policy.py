"""Fast excluded-topic checks; resource semantics are reviewed in writer.py."""
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
    return bool(EXCLUDED_TERMS.search(folded)) or any(
        0x1F1E6 <= ord(c) <= 0x1F1FF for c in folded)
