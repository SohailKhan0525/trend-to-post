"""Cloudflare-only original founder/community posts with anti-repetition checks."""
import json
import os
import re
import secrets
import unicodedata
import urllib.request
from difflib import SequenceMatcher

SYSTEM = r'''Write ONLY the short X post requested in BRIEF. Follow CATEGORY exactly.
The owner's preferred examples are:
CONNECT: "Hello fake founders\n\nLet's connect"
SHOWCASE: "Hello founders\n\nComment your startup product. I'll try it if I need it."
CODING_TAKE: "If you're coding 100% on a laptop, you're probably new to coding."
These are the desired formats and blunt tone. Do not turn them into advice discussions.
CONNECT: exactly two paragraphs. First is "Hello <audience>" (at most five words).
Second is a short "Let's connect" invitation (at most twelve words). No questions.
SHOWCASE: exactly two paragraphs. "Hello <audience>", then a short direct invitation
starting Comment, Drop or Share and naming a startup, product, project, tool or app.
You MAY say "I'll try it if I need it". Never promise to use every product.
CODING_TAKE: exactly one short sentence, at most thirty words. A blunt casual coding opinion
or playful jab about habits. Begin "If", "Coding", "Unpopular opinion:" or "Hot take:".
No explanatory follow-up, lessons, numbered lists, engagement question or corporate voice.
Do not invent a personal product, struggle, experience, revenue, job or life story.
Never write "I'm still trying", "I wish", "my product", "what do you wish you'd known",
"what's the most important", "what's your biggest challenge", or coaching questions.
No generic motivation, advice threads, news, quote-posts, URLs, hashtags, mentions or emojis.
Use plain casual English with clean blank lines. No preamble, filler or added commentary.
Repeated greeting FORMATS are intentional; vary the audience or invitation as BRIEF permits.
Return ONLY JSON {"text":"the post"}; escape line breaks as \n.
'''



class DraftError(ValueError):
    """A fixed safe validation reason."""


def normalized(text):
    text=unicodedata.normalize('NFKC',text).casefold().replace('’',"'")
    return ' '.join(re.findall(r'[a-z0-9]+',re.sub(r'(\w)\1{2,}',r'\1',text)))


def validate(text,history,category=None):
    if not isinstance(text,str):
        raise DraftError('Post must be a string')
    text=unicodedata.normalize('NFC',text).strip().replace(r'\n','\n')
    if any(unicodedata.category(c).startswith('C') and c!='\n' for c in text):
        raise DraftError('Invisible control characters')
    parts=[' '.join(p.split()) for p in re.split(r'\n+',text) if p.strip()]
    if not 1<=len(parts)<=2:
        raise DraftError('Use one or two short paragraphs')
    text='\n\n'.join(parts)
    if not 20<=sum(1 if ord(c)<128 else 2 for c in text)<=180:
        raise DraftError('Post outside short-post budget')
    if re.search(r'https?://|www\.|@|#|```|\b(?:follow for follow|like for like|repost this|retweet this|guaranteed|million users)\b',text,re.I):
        raise DraftError('Links, mentions or engagement-trading language')
    if re.search(r'\b(?:i will|i.ll)\s+(?:buy|use|promote|follow)\s+(?:every|all|any)\b',text,re.I):
        raise DraftError('Unconditional product or engagement promise')
    if re.search(r'\d',text) and re.findall(r'\d+',text)!=['100']:
        raise DraftError('Invented numeric claim')
    if re.search(r"\b(?:i am|i'm|i’m|my product|my startup|i wish|biggest challenge|most important|wish you)\b",text,re.I):
        raise DraftError('Invented personal story or generic coaching language')
    if category in {'connect','showcase'}:
        if len(parts)!=2 or not re.fullmatch(r'Hello [A-Za-z -]+[.!]?',parts[0]) or len(parts[0].split())>5:
            raise DraftError('Required format: Hello audience, blank line, short invitation')
        if '?' in text:
            raise DraftError('Use a direct invitation, not an advice question')
        if category=='connect' and (not re.match(r"Let['’]s connect\b",parts[1],re.I) or len(parts[1].split())>12):
            raise DraftError('Connection post must simply invite people to connect')
        if category=='showcase' and (not re.match(r'(?:Comment|Drop|Share)\b',parts[1],re.I) or not re.search(r'\b(?:startup|product|project|tool|app)\b',parts[1],re.I) or len(parts[1].split())>25):
            raise DraftError('Product post must directly invite a product or project')
    elif category=='coding_take':
        if len(parts)!=1 or len(text.split())>30 or '?' in text or not re.match(r'(?:If\b|Coding\b|Unpopular opinion:|Hot take:)',text,re.I):
            raise DraftError('Coding take must be one blunt sentence, no follow-up question')
    elif category is not None:
        raise DraftError('Unsupported content category')
    norm=normalized(text)
    hook=normalized(parts[0])
    for old in history[-100:]:
        previous=normalized(old['text'])
        if norm==previous or (category not in {'connect','showcase'} and SequenceMatcher(None,norm,previous).ratio()>=.78):
            raise DraftError('Repeated post or paraphrase')
        current_words=norm.split()
        if category not in {'connect','showcase'} and len(current_words)>=6 and any(' '.join(current_words[i:i+6]) in previous for i in range(len(current_words)-5)):
            raise DraftError('Recycled sentence fragment')
    for old in ([] if category in {'connect','showcase'} else history[-8:]):
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
          'RECENT_EXACT_POSTS':[p['text'] for p in history[-8:]]}
    for attempt in range(2):
        data['RULE']='Stay exactly in the assigned format. No advice question or personal story.'
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
                text=validate(candidate,history,brief['category'])
            except DraftError as exc:
                print(f'Candidate rejected: {exc}')
                data['CORRECTION']=str(exc)
                continue
            return text
    raise DraftError('No fresh valid candidate after two attempts; skip this run')
