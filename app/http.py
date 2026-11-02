"""Bounded public HTTPS reads; redirects never escape the reviewed source hosts."""
import gzip
import io
import ipaddress
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit,urlunsplit,urljoin,unquote

MAX_BYTES = 2_000_000
TIMEOUT = 12
USER_AGENT = 'UsefulLinksBot/1.0 (+https://github.com/SohailKhan0525/trend-to-post)'


class LinkError(ValueError):
    pass


def canonical(url):
    if not isinstance(url,str) or re.search(r'[\s\x00-\x1f\\]',url):
        raise LinkError('Invalid URL characters')
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):
        raise LinkError('Only public HTTPS URLs without credentials are allowed')
    host=p.hostname.lower().rstrip('.')
    if not re.fullmatch(r'[a-z0-9.-]+',host) or host in {'localhost','localhost.localdomain'}:
        raise LinkError('Invalid public hostname')
    path=re.sub(r'%([0-9a-fA-F]{2})',lambda m: unquote(m[0]) if re.fullmatch(r'[A-Za-z0-9._~-]',unquote(m[0])) else m[0].upper(),p.path)
    path=re.sub('/+','/',path).rstrip('/')
    # Queries and fragments do not create a new resource identity. Discovery only
    # accepts query-free URLs; this also collapses tracking and anchor variants.
    return urlunsplit(('https',host,path,'',''))


def domain(url):
    return '.'.join(urlsplit(url).hostname.split('.')[-2:])


def assert_public(url,hosts):
    key=canonical(url)
    host=urlsplit(key).hostname
    if host not in hosts:
        raise LinkError('Destination is outside the approved source hosts')
    try:
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    except OSError as exc:
        raise LinkError('DNS lookup failed') from exc
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise LinkError('Private or non-public destination')
    parsed=urlsplit(url)
    # Preserve a server's trailing slash during requests. Canonical identities
    # remove it, but stripping it here would create an endless slash redirect.
    return urlunsplit(('https',host,parsed.path or '/',parsed.query,''))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


@dataclass
class Page:
    url: str
    body: bytes
    content_type: str
    redirects: list


def fetch(url,hosts):
    opener=urllib.request.build_opener(NoRedirect())
    current=url
    redirects=[]
    for _ in range(5):
        current=assert_public(current,hosts)
        request=urllib.request.Request(current,headers={'User-Agent':USER_AGENT,'Accept':'text/html,application/xml,text/xml,text/plain;q=0.9','Accept-Encoding':'identity'})
        try:
            response=opener.open(request,timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code in (301,302,303,307,308) and exc.headers.get('Location'):
                redirects.append(current)
                current=urljoin(current,exc.headers['Location'])
                continue
            raise LinkError('HTTP verification failed') from exc
        except (OSError,urllib.error.URLError) as exc:
            raise LinkError('Network verification failed') from exc
        with response:
            if response.status!=200:
                raise LinkError('Resource did not return HTTP 200')
            raw=response.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:
                raise LinkError('Resource exceeded the read limit')
            if raw[:2]==b'\x1f\x8b':
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:
                    raw=zipped.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES:
                    raise LinkError('Expanded resource exceeded the read limit')
            return Page(current,raw,response.headers.get('Content-Type','').lower(),redirects)
    raise LinkError('Too many redirects')
