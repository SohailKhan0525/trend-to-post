"""Cloudflare-only original founder/community posts with anti-repetition checks."""
import json
import os
import re
import secrets
import unicodedata
import urllib.request
from difflib import SequenceMatcher

SYSTEM = r'''Write short standalone X posts for an anonymous founder/builder community account.
Use the assigned BRIEF and CATEGORY, and vary phrasing. Voice: casual, playful, direct,
a little cheeky, approachable. Natural sentence case. No corporate motivational fluff.
Mix concise founder greetings, product invitations, useful builder questions, coding opinions,
and relatable observations. A greeting can have one blank line before the invitation.
One or two short paragraphs; 25-240 characters total; at most one emoji.
A product invitation can ask what someone is building and who it helps; trying a product must
be conditional on a real need. Never promise to buy, promote, follow, like, or use every product.
A coding hot take is an opinion or joke, never a made-up statistic or a personal attack.
Playful 'fake founders' humor is allowed without accusing real people of fraud or gatekeeping.
Do not repeat 'Hello founders' or any opening as a daily signature. Avoid generic 'let's connect'
as the whole post; say which builders or what conversation. No engagement-for-engagement deals,
follow-for-follow, like/repost bait, unsolicited mentions, hashtags, links, or automated replies.
No factual news, release claims, invented revenue, user counts, personal experience, employment,
or promises. No private information, insults at people, politics or medical/financial advice.
AVOID_OPENINGS is a blacklist of recent opening phrases, not examples to imitate.
Use a fresh concrete angle for this brief and the assigned APPROACH.
Return ONLY a single JSON object: {"text":"your post"}.
Escape any line breaks in the string as \n. No preamble, code fence, or extra fields.
'''



class DraftError(ValueError):
    """A fixed safe validation reason."""


def normalized(text):
    text=unicodedata.normalize('NFKC',text).casefold().replace('’',"'")
    return ' '.join(re.findall(r'[a-z0-9]+',re.sub(r'(\w)\1{2,}',r'\1',text)))


def validate(text,history):
    if not isinstance(text,str):
        raise DraftError('Post must be a string')
    text=unicodedata.normalize('NFC',text).strip().replace(r'\n','\n')
    if any(unicodedata.category(c).startswith('C') and c!='\n' for c in text):
        raise DraftError('Invisible control characters')
    parts=[' '.join(p.split()) for p in re.split(r'\n+',text) if p.strip()]
    if not 1<=len(parts)<=2:
        raise DraftError('Use one or two short paragraphs')
    text='\n\n'.join(parts)
    if not 25<=sum(1 if ord(c)<128 else 2 for c in text)<=260:
        raise DraftError('Post outside short-post budget')
    if re.search(r'https?://|www\.|@|#|```|\b(?:follow for follow|like for like|repost this|retweet this|guaranteed|million users)\b',text,re.I):
        raise DraftError('Links, mentions or engagement-trading language')
    if re.search(r'\b(?:i will|i.ll)\s+(?:buy|use|promote|follow)\s+(?:every|all|any)\b',text,re.I):
        raise DraftError('Unconditional product or engagement promise')
    if re.search(r'\d',text) and re.findall(r'\d+',text)!=['100']:
        raise DraftError('Invented numeric claim')
    norm=normalized(text)
    hook=normalized(parts[0])
    for old in history[-100:]:
        previous=normalized(old['text'])
        if norm==previous or SequenceMatcher(None,norm,previous).ratio()>=.78:
            raise DraftError('Repeated post or paraphrase')
        current_words=norm.split()
        if len(current_words)>=6 and any(' '.join(current_words[i:i+6]) in previous for i in range(len(current_words)-5)):
            raise DraftError('Recycled sentence fragment')
    for old in history[-8:]:
        old_hook=normalized(old['text'].split('\n\n')[0])
        if hook==old_hook or (len(hook.split())>=4 and SequenceMatcher(None,hook,old_hook).ratio()>=.85):
            raise DraftError('Repeated opening')
    return text


def complete(system,data,temperature=.8):
    account=os.environ.get('CLOUDFLARE_ACCOUNT_ID','').strip()
    token=os.environ.get('CLOUDFLARE_API_TOKEN','').strip()
    model=os.environ.get('CLOUDFLARE_AI_MODEL') or '@cf/meta/llama-3.1-8b-instruct-fp8'
    if not re.fullmatch(r'[a-fA-F0-9]{32}',account) or not token:
        raise ValueError('Cloudflare configuration required')
    if not re.fullmatch(r'@cf/[\w.-]+/[\w.-]+',model):
        raise ValueError('Invalid model identifier')
    payload={'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps(data)}],
             'seed':secrets.randbelow(999999999)+1,'temperature':temperature,'max_tokens':450,'response_format':{'type':'json_object'}}
    request=urllib.request.Request(f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}',
        data=json.dumps(payload).encode(),headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=60) as response:
        result=json.load(response)
    if result.get('success') is not True:
        raise ValueError('Cloudflare generation failed')
    raw=result.get('result',{}).get('response')
    if isinstance(raw,dict):
        return raw
    if isinstance(raw,str):
        raw=raw.strip()
        if raw.startswith('```'):
            raw=re.sub(r'^```(?:json)?\s*|\s*```$', '',raw)
    try:
        return json.loads(raw,strict=False)
    except (json.JSONDecodeError,TypeError) as exc:
        raise DraftError('Invalid JSON from Cloudflare') from exc


def generate(brief,history):
    data={'BRIEF':brief['brief'],'CATEGORY':brief['category'],
          'AVOID_OPENINGS':[' '.join(p['text'].split()[:6]) for p in history[-8:]]}
    for attempt in range(2):
        data['APPROACH']=secrets.choice(('A friendly direct invitation.', 'A specific question in plain language.', 'A concise playful observation.', 'An informal two-line conversation starter.'))
        try:
            result=complete(SYSTEM,data)
            if isinstance(result,dict) and set(result)=={'text'}:
                candidates=[result['text']]
            elif isinstance(result,dict) and set(result)=={'posts'} and isinstance(result['posts'],list) and 1<=len(result['posts'])<=3:
                candidates=result['posts']
            else:
                raise DraftError('Invalid candidate schema')
        except DraftError as exc:
            print(f'Generation rejected: {exc}')
            data['CORRECTION']='Return exactly one valid JSON object with one text string. No Markdown or preamble.'
            continue
        for candidate in candidates:
            try:
                text=validate(candidate,history)
            except DraftError as exc:
                print(f'Candidate rejected: {exc}')
                data['CORRECTION']=str(exc)
                continue
            return text
    raise DraftError('No fresh valid candidate after two attempts; skip this run')
