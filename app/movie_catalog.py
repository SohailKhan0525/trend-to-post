"""Find a short attributed spoken line in a specific revision of an English Wikiquote work."""
from datetime import datetime, timezone
from http.client import HTTPException
import json
import random
import re
import unicodedata
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


API = 'https://en.wikiquote.org/w/api.php'
USER_AGENT = 'QofenoQuoteReader/1.0 (https://github.com/SohailKhan0525/trend-to-post)'
# These are categories, not a finite inventory of quotes. Membership is read
# live, and a second category rotates across release years as new films appear.
CATEGORIES = (
    'Japanese films', 'Indian films', 'French films', 'Hong Kong films', 'Italian films',
    'South Korean films', 'German films', 'Canadian films', 'British films', 'Spanish films',
    'Mexican films', 'Foreign language films', 'Australian films', 'BBC shows', 'Netflix shows',
    'Japanese TV shows', 'Drama TV shows', 'Science fiction TV shows', 'HBO shows', 'Comedy TV shows',
)
HEADING = re.compile(r'^(={2,6})\s*(.+?)\s*\1\s*$')
DIALOGUE = re.compile(r"^:{1,2}\s*'''([^:'\n][^:\n]{0,45}?)(?::'''|'''\s*:)\s+(.+)$")
EPISODE = re.compile(r'\[\d{1,2}[.x]\d{1,3}\]|\bS\d{1,2}E\d{1,3}\b', re.I)
NOT_DIALOGUE = re.compile(r'\b(?:about|cast|interviews?|trailers?|taglines?|credits?|songs?|lyrics?|'
                          r'soundtrack|misattribut\w*|disputed|unsourced|deleted scenes?|commentary)\b', re.I)
NOT_SPEAKER = re.compile(r'\b(?:unknown|unidentified|narrator|narration|crowd|voice|singer|chorus|'
                         r'everyone|both|shouts?|singing|announcer|man|woman|they|them|someone)\b', re.I)
DANGLING = re.compile(r"\b(?:he|she|him|her|they|them|this|that|those|these|it)\b", re.I)
SMALL_TALK = re.compile(r"\b(?:how are you|who are you|what (?:do you want|is going on)|"
                        r"(?:i|we) (?:don't know|have to go)|(?:you|we) okay|nice to meet|"
                        r"thanks? (?:for|you)|wait a minute|oh my god|i'm sorry)\b", re.I)
PLAIN_LINE = re.compile(r"""[\w .,!?;:'"“”‘’—–-]+""")
MUSIC = re.compile(r'^\[\[Category:[^\]\n]*\bmusicals?\b', re.I | re.M)
WORK = re.compile(r'\b(?:film|movie|television|TV|sitcom|anime|animated series|web series)\b', re.I)
NONWORK = re.compile(r'\b(?:franchise|discography|disambiguation|list of films)\b', re.I)
NONPUBLIC = re.compile(r'<!--.*?(?:-->|$)|<(nowiki|pre|poem|ref|gallery|syntaxhighlight)\b[^>]*>'
                       r'.*?(?:</\1\s*>|$)', re.S | re.I)
SINGING = re.compile(r"\b(?:sings?|singing|chants?|chanting|lyrics?|song)\b", re.I)
PROFANITY = re.compile(r'\b(?:fuck\w*|shit\w*|bitch\w*|asshole\w*|cunt\w*|nigg\w*|fagg\w*|'
                       r'rape\w*|suicid\w*|murder\w*|kill\w*)\b', re.I)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _query(**params):
    url = API + '?' + urlencode({'action': 'query', 'format': 'json', 'formatversion': 2, **params})
    request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    with build_opener(_NoRedirects()).open(request, timeout=8) as response:
        if response.geturl() != url:
            raise ValueError('Unexpected Wikiquote response origin')
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError('Wikiquote read limit exceeded')
    result = json.loads(body)
    if not isinstance(result, dict) or not isinstance(result.get('query'), dict):
        raise ValueError('Wikiquote query unavailable')
    return result['query']


def _title_key(title):
    normalized = unicodedata.normalize('NFKC', title.replace('_', ' ')).strip().casefold()
    return re.sub(r'\s*\((?:\d{4}|(?:\d{4} )?(?:film|tv series|television series))\)$', '', normalized)


def _page_lines(page):
    if not isinstance(page, dict) or page.get('ns') != 0 or 'redirect' in page:
        return
    title = page.get('title')
    revisions = page.get('revisions') or []
    if not isinstance(title, str) or not title.strip() or '/' in title or not revisions:
        return
    revision = revisions[0]
    revision_id = revision.get('revid')
    source = revision.get('slots', {}).get('main', {}).get('content')
    if type(revision_id) is not int or revision_id <= 0 or not isinstance(source, str):
        return
    source = NONPUBLIC.sub('\n', source)
    intro = re.split(r'^==', source, maxsplit=1, flags=re.M)[0]
    if not WORK.search(intro) or NONWORK.search(title) or MUSIC.search(source):
        return
    lines = source.splitlines()
    speakers = {match[1] for raw in lines if (match := DIALOGUE.fullmatch(raw))}
    # Track title/episode subsections; 'About', cast interviews, songs and
    # tags often contain genuine quotations that are not spoken screen lines.
    headings = {}
    song_block = False
    url = 'https://en.wikiquote.org/w/index.php?' + urlencode({'title': title.replace(' ', '_'), 'oldid': revision_id})
    for raw in lines:
        heading = HEADING.fullmatch(raw)
        if heading:
            song_block = False
            level = len(heading[1])
            headings = {n: name for n, name in headings.items() if n < level}
            headings[level] = heading[2].strip()
            continue
        if raw.startswith('----'):
            song_block = False
        if (raw.startswith((':', '*', "''", '[')) and SINGING.search(raw)
                and ('[' in raw or "''[" in raw or '♪' in raw)):
            song_block = True
        if song_block or not headings or any(NOT_DIALOGUE.search(name) for name in headings.values()):
            continue
        section = headings[max(headings)]
        match = DIALOGUE.fullmatch(raw)
        if raw.startswith('* ') and section in speakers:
            # A named character section explicitly singles out individual lines.
            speaker, line = section, raw[2:]
        elif (match and (section.casefold() in ('dialogue', 'dialogues') or EPISODE.search(section))
              and match[2].startswith("'''") and match[2].endswith("'''")):
            # Ordinary turns within a conversation are not notable one-liners.
            speaker, line = match.groups()
        else:
            continue
        if line.startswith("'''") and line.endswith("'''"):
            line = line[3:-3]
        if not line:
            continue
        # Require a directly labelled individual and an entire, unedited line.
        # Reject stage directions, lyrics, nested quotations and wiki markup.
        if (not speaker[0].isupper() or not all(c.isalpha() or c in " .-'’" for c in speaker)
                or NOT_SPEAKER.search(speaker) or not 1 <= len(speaker.split()) <= 5):
            continue
        if (not line[0].isupper() or line[-1] not in '.?!' or not PLAIN_LINE.fullmatch(line)
                or "''" in line or '...' in line or '"' in line or '“' in line or '”' in line
                or not 3 <= len(line.split()) <= 10 or len(line) > 85
                or DANGLING.search(line) or PROFANITY.search(line) or SMALL_TALK.search(line)):
            continue
        yield {'quote': line, 'title': title, 'speaker': speaker, 'source': url}


def find_quote(history, *, exclude_titles=(), now=None, accept=None):
    """Read at most two category lists and two batches of revisions; never send or persist."""
    now = now or datetime.now(timezone.utc)
    rng = random.Random(int(now.timestamp()) // 600)
    used = {_title_key(title) for title in exclude_titles if isinstance(title, str)}
    for row in history:
        credit = row.get('movie_credit') if isinstance(row, dict) else None
        if isinstance(credit, dict) and isinstance(credit.get('title'), str):
            used.add(_title_key(credit['title']))
            try:
                url = urlsplit(credit.get('source') or '')
                if url.hostname == 'en.wikiquote.org':
                    if url.path.startswith('/wiki/'):
                        used.add(_title_key(unquote(url.path[6:])))
                    elif url.path == '/w/index.php':
                        used.update(_title_key(title) for title in parse_qs(url.query).get('title', ()))
            except (TypeError, ValueError):
                pass
    for category in (rng.choice(CATEGORIES), f'{rng.randint(1950, now.year)} films'):
        try:
            members = _query(list='categorymembers', cmtitle='Category:' + category,
                             cmnamespace=0, cmtype='page', cmlimit=500).get('categorymembers', [])
            titles = [row['title'] for row in members if isinstance(row, dict) and row.get('ns') == 0
                      and isinstance(row.get('title'), str) and 0 < len(row['title']) <= 150
                      and not any(c in row['title'] for c in '/|') and _title_key(row['title']) not in used]
            if not titles:
                continue
            chosen = rng.sample(titles, min(len(titles), 6))
            pages = _query(prop='revisions', titles='|'.join(chosen), rvslots='main',
                           rvprop='ids|content').get('pages', [])
            rng.shuffle(pages)
            for page in pages:
                if page.get('title') not in chosen:
                    continue
                options = list(_page_lines(page))
                rng.shuffle(options)
                for entry in options:
                    if accept is None or accept(entry):
                        return entry
        except HTTPError as exc:
            if exc.code in (403, 429):
                break
        except (OSError, HTTPException):
            break
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return None
