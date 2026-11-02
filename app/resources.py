"""Discover real links on approved first-party sites, then verify their content."""
import hashlib
import json
import random
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin,urlsplit
from .http import fetch,canonical,domain,LinkError
from .policy import has_excluded_terms

SOURCES_PATH=Path('data/sources.json')
MAX_CATALOG=12000
MAX_PROBES=20
MAX_PER_DOMAIN_PER_DAY=6


def sources():
    return json.loads(SOURCES_PATH.read_text())


def source_for(ident):
    return next(s for s in sources() if s['id']==ident)


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title=[];self.body=[];self.main=[];self.links=[];self.canonical=None
        self.skip=0;self.title_depth=0;self.main_depth=0;self.password=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in {'script','style','nav','footer','header','aside','noscript'}:
            self.skip+=1
        if tag=='title':self.title_depth+=1
        if tag in {'main','article'}:self.main_depth+=1
        if tag=='a' and attrs.get('href'):self.links.append(attrs['href'])
        if tag=='link' and 'canonical' in attrs.get('rel','').split():self.canonical=attrs.get('href')
        if tag=='input' and attrs.get('type')=='password':self.password=True
    def handle_endtag(self,tag):
        if tag in {'script','style','nav','footer','header','aside','noscript'}:self.skip=max(0,self.skip-1)
        if tag=='title':self.title_depth=max(0,self.title_depth-1)
        if tag in {'main','article'}:self.main_depth=max(0,self.main_depth-1)
    def handle_data(self,data):
        if self.title_depth:self.title.append(data)
        elif not self.skip:
            self.body.append(data)
            if self.main_depth:self.main.append(data)
    def text(self):
        return ' '.join(' '.join(self.main or self.body).split())


def eligible(url,source):
    try:
        if urlsplit(url).query or urlsplit(url).fragment:
            return False
        key=canonical(url)
        p=urlsplit(key)
        if p.hostname not in source['hosts']:
            return False
        if key==canonical(source['root']):return True
        if not any(p.path.startswith(prefix.rstrip('/')) for prefix in source['paths']):
            return False
        if re.search(r'/(?:login|sign-?in|sign-?up|pricing|privacy|terms|legal|search|tags?|authors?|category|categories|events?)(?:/|$)|\.(?:png|jpg|webp|svg|zip|pdf|mp4|css|js|xml|gz)$',p.path,re.I):
            return False
        return True
    except (ValueError,LinkError):
        return False


def inspect(url,source):
    if not eligible(url,source):raise LinkError('URL is not an eligible resource')
    page=fetch(url,source['hosts'])
    if not eligible(page.url,source) or 'html' not in page.content_type:
        raise LinkError('Expected a public resource HTML page')
    doc=Document();doc.feed(page.body.decode('utf-8',errors='replace'))
    title=' '.join(' '.join(doc.title).split())[:180]
    body=doc.text()
    if len(title)<4 or len(body)<100 or re.search(r'page not found|404(?:\s|\s*[-|:])|access denied|just a moment|verify you are human|sign in to continue|checking your browser',title,re.I):
        raise LinkError('Missing content, soft error or challenge page')
    if doc.password and len(body)<1000:
        raise LinkError('Resource requires authentication')
    excerpt=body[:1100]
    if has_excluded_terms(title+' '+body):
        raise LinkError('Excluded political or country-related resource')
    aliases={canonical(url),canonical(page.url),*[canonical(u) for u in page.redirects]}
    if doc.canonical:
        declared=urljoin(page.url,doc.canonical)
        if eligible(declared,source):aliases.add(canonical(declared))
    fingerprint=hashlib.sha256(re.sub(r'\s+',' ',(title+' '+body[:1500]).casefold()).encode()).hexdigest()
    return {'url':page.url,'title':title,'excerpt':excerpt,'aliases':sorted(aliases),
            'fingerprint':fingerprint,'source_id':source['id'],
            'verified_at':datetime.now(timezone.utc).isoformat()}


def _sitemap_links(url,source,seed,depth=0):
    if depth>1:return []
    page=fetch(url,source['hosts'])
    if b'<!DOCTYPE' in page.body.upper() or b'<!ENTITY' in page.body.upper():
        raise LinkError('Unsafe XML document')
    root=ET.fromstring(page.body)
    locations=[e.text.strip() for e in root.iter() if e.tag.rsplit('}',1)[-1]=='loc' and e.text][:15000]
    rng=random.Random(seed);rng.shuffle(locations)
    if root.tag.rsplit('}',1)[-1]=='sitemapindex':
        links=[]
        for child in locations[:2]:
            try:links.extend(_sitemap_links(child,source,seed,depth+1))
            except (LinkError,ET.ParseError,OSError):continue
        return links
    return locations


def discover(source,seed):
    links=[source['root']]
    try:
        root=fetch(source['root'],source['hosts'])
        doc=Document();doc.feed(root.body.decode('utf-8',errors='replace'))
        links.extend(urljoin(root.url,u) for u in doc.links)
    except LinkError:pass
    try:
        robots=fetch('https://'+urlsplit(source['root']).hostname+'/robots.txt',source['hosts'])
        maps=re.findall(r'^Sitemap:\s*(https://\S+)',robots.body.decode('utf-8',errors='replace'),re.I|re.M)
        # Only use advertised maps, never fabricate URLs or guess a sitemap path.
        for sitemap in maps[:1]:
            try:links.extend(_sitemap_links(sitemap,source,seed))
            except (LinkError,ET.ParseError,OSError):continue
    except LinkError:pass
    accepted=list(dict.fromkeys(canonical(u) for u in links if eligible(u,source)))
    random.Random(seed).shuffle(accepted)
    return [{'url':u,'source_id':source['id']} for u in accepted[:700]]


def seed_catalog(state):
    if not state.get('catalog'):
        state['catalog']=[{'url':s['root'],'source_id':s['id']} for s in sources()]


def _is_fresh(resource,state):
    return not set(resource['aliases']).intersection(state['used_urls']) and resource['fingerprint'] not in state['used_fingerprints']


def find_pair(state,audience,slot):
    seed_catalog(state)
    source_map={s['id']:s for s in sources()}
    matching=[s for s in source_map.values() if audience in s['audiences']]
    cursor=state.setdefault('discovery_cursors',{}).get(audience,0)
    # Rotate first-party sources and extend the backlog continually, not just at exhaustion.
    selected=[matching[(cursor+i)%len(matching)] for i in range(min(2,len(matching)))]
    for source in selected:
        state['catalog'].extend(discover(source,slot+source['id']))
    state['discovery_cursors'][audience]=cursor+len(selected)
    by_url={canonical(c['url']):c for c in state['catalog'] if canonical(c['url']) not in state['used_urls']}
    state['catalog']=list(by_url.values())[-MAX_CATALOG:]
    today=datetime.now(timezone.utc).date().isoformat()
    counts=defaultdict(int)
    for post in state['history']:
        if post['posted_at'][:10]==today:
            for resource in post.get('resources',[]):counts[domain(resource['url'])]+=1
    groups=defaultdict(list)
    rng=random.Random(slot)
    candidates=list(state['catalog']);rng.shuffle(candidates)
    for candidate in candidates:
        source=source_map.get(candidate['source_id'])
        if source and audience in source['audiences'] and counts[domain(candidate['url'])]<MAX_PER_DOMAIN_PER_DAY:
            groups[domain(candidate['url'])].append(candidate)
    ordered=[]
    domains=list(groups);rng.shuffle(domains);domains.sort(key=lambda host:counts[host])
    while groups and len(ordered)<MAX_PROBES:
        for host in domains:
            if groups.get(host):ordered.append(groups[host].pop())
        groups={k:v for k,v in groups.items() if v}
    ordered=ordered[:MAX_PROBES]
    def probe(candidate):
        try:return candidate,inspect(candidate['url'],source_map[candidate['source_id']])
        except (LinkError,ValueError,OSError):return candidate,None
    valid=[];rejected=set()
    with ThreadPoolExecutor(max_workers=4) as executor:
        # Only request the next bounded batch when the previous one has no pair.
        for start in range(0,len(ordered),4):
            for candidate,resource in executor.map(probe,ordered[start:start+4]):
                if resource is None or not _is_fresh(resource,state):
                    rejected.add(canonical(candidate['url']));continue
                if any(domain(old['url'])==domain(resource['url']) or old['fingerprint']==resource['fingerprint'] or set(old['aliases'])&set(resource['aliases']) for old in valid):continue
                valid.append(resource)
            if len(valid)>=2:break
    state['catalog']=[c for c in state['catalog'] if canonical(c['url']) not in rejected]
    if len(valid)<2:
        return None
    return valid[:2]
