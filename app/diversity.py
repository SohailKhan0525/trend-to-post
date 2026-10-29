"""Deterministic repetition guards; no model judgment is trusted for these checks."""
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

BRANDS = re.compile(r'\b(?:anthropic|openai|chatgpt|claude|opencode|nvidia|gemini|ai)(?:\s+ai)?\b', re.I)
STOP = set('a an the and or but it its is are was were be been being to of for on in at by with from that this these those now just own your you we our they their has have had can could will would might may gets getting into about as than then so some any all who what which how not more next starts starting'.split())
STORY_FAMILIES = {
    'api_credits': r'\bapi credits?\b|\bcredits?\b.{0,24}\bapi\b',
    'ai_drug_discovery': r'\b(?:biotech|drug discovery|experimental drugs|medical decision)\b',
    'speech_to_speech': r'\bspeech[ -]to[ -]speech\b',
}


def normalized(text, mask_brands=False):
    text = unicodedata.normalize('NFKC', text).casefold().replace('’', "'")
    if mask_brands:
        text = BRANDS.sub('product', text)
    # Elongations and punctuation changes must not evade hook cooldowns.
    text = re.sub(r'(\w)\1{2,}', r'\1', text)
    return ' '.join(re.findall(r"[a-z0-9]+", text))


def hooks(text):
    return re.split(r'\n\s*\n', text.strip())[0]


def body(text):
    parts = re.split(r'\n\s*\n', text.strip(), maxsplit=1)
    return parts[-1]


def words(text):
    return {w for w in normalized(BRANDS.sub('', text)).split() if w not in STOP and len(w)>2}


def overlap(a, b):
    return len(a & b) / max(1, len(a | b))


def recent(history, now=None):
    now = now or datetime.now(timezone.utc)
    result = []
    for item in history[-100:]:
        try:
            stamp = datetime.fromisoformat(item['posted_at'])
            if stamp.tzinfo is None or not timedelta(0) <= now-stamp <= timedelta(hours=48):
                continue
        except (KeyError, ValueError, TypeError):
            # Legacy fixtures/history without timestamps still get conservative guards.
            pass
        result.append(item)
    return result


def repetition_reason(text, history):
    hook = normalized(hooks(text), mask_brands=True)
    draft_body = normalized(body(text))
    draft_words = words(body(text))
    for old in history[-20:]:
        old_hook = normalized(hooks(old['text']), mask_brands=True)
        if hook == old_hook or SequenceMatcher(None, hook, old_hook).ratio() >= .80:
            return 'Repeated hook within the last 20 posts'
        hwords, owords = hook.split(), old_hook.split()
        if len(hwords)>=4 and any(' '.join(hwords[i:i+4]) in ' '.join(owords) for i in range(len(hwords)-3)):
            return 'Recycled hook template'
    for old in history[-100:]:
        old_body = normalized(body(old['text']))
        if SequenceMatcher(None, draft_body, old_body).ratio() >= .74:
            return 'Repeated body with a different hook'
        old_words = words(body(old['text']))
        if min(len(draft_words), len(old_words)) >= 5 and overlap(draft_words, old_words) >= .55:
            return 'Recycled commentary wording'
    return None


def story_reason(source_text, history, now=None):
    for old in recent(history, now):
        previous = old.get('source_text') or old['text']
        for pattern in STORY_FAMILIES.values():
            if re.search(pattern, source_text, re.I) and re.search(pattern, previous, re.I):
                return 'Story-family cooldown (48 hours)'
        a, b = words(source_text), words(previous)
        if min(len(a),len(b)) >= 6 and overlap(a,b) >= .55:
            return 'Same story from another source (48 hours)'
    return None
