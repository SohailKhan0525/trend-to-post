"""D1 memory derived from verified public X posts. The posting ledger owns delivery."""
import json
import os
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID


DATABASE_NAME = 'qofeno-memory'
TABLE = 'qofeno_x_posts_v1'
COLUMNS = ('post_id', 'kind', 'posted_at', 'text', 'event_key', 'author_id', 'author', 'source_id', 'source_text')
SCHEMA = (
    f'''CREATE TABLE IF NOT EXISTS {TABLE} (
        post_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, posted_at TEXT NOT NULL,
        text TEXT NOT NULL, event_key TEXT NOT NULL, author_id TEXT NOT NULL,
        author TEXT NOT NULL, source_id TEXT NOT NULL, source_text TEXT NOT NULL)''',
    f'CREATE INDEX IF NOT EXISTS qofeno_x_people_v1 ON {TABLE}(author_id, posted_at DESC)',
    f'CREATE INDEX IF NOT EXISTS qofeno_x_kinds_v1 ON {TABLE}(kind, posted_at DESC)',
)


class MemoryUnavailable(RuntimeError):
    """Public-safe diagnostic. Never include a token, request, or raw API error."""


def database_uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        raise MemoryUnavailable('D1 database ID must be a valid UUID.') from None


class Memory:
    def __init__(self, account_id, token):
        self.base_url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database'
        self.token = token
        self.database_id = None

    def _request(self, path, payload=None):
        request = Request(self.base_url + path, headers={'Authorization': 'Bearer ' + self.token,
                          'Content-Type': 'application/json'},
                          data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urlopen(request, timeout=25) as response:
                body = json.load(response)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise MemoryUnavailable('D1 access denied. Grant the token Account D1 Write (Edit) '
                                        'for the configured Cloudflare account.') from None
            if exc.code == 404:
                raise MemoryUnavailable('D1 resource missing. Check D1_DATABASE_ID and the Cloudflare account.') from None
            raise MemoryUnavailable(f'D1 returned HTTP {exc.code}. Check the database and account limits.') from None
        except (URLError, OSError, ValueError) as exc:
            raise MemoryUnavailable(f'D1 response unavailable ({type(exc).__name__}).') from None
        if not isinstance(body, dict) or body.get('success') is not True or 'result' not in body:
            raise MemoryUnavailable('D1 rejected the request. Check the database and token D1 Write access.')
        return body['result']

    def _find(self):
        result = self._request('?name=' + DATABASE_NAME + '&per_page=10000')
        if not isinstance(result, list) or any(not isinstance(db, dict) for db in result):
            raise MemoryUnavailable('Invalid D1 database listing.')
        matches = [db for db in result if db.get('name') == DATABASE_NAME]
        if len(matches) > 1:
            raise MemoryUnavailable('Multiple matching D1 databases. Set D1_DATABASE_ID explicitly.')
        return database_uuid(matches[0].get('uuid')) if matches else None

    @classmethod
    def connect(cls):
        account = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '').strip()
        token = (os.environ.get('CLOUDFLARE_D1_API_TOKEN') or os.environ.get('CLOUDFLARE_API_TOKEN', '')).strip()
        if not re.fullmatch(r'[a-fA-F0-9]{32}', account) or not token:
            raise MemoryUnavailable('D1 needs CLOUDFLARE_ACCOUNT_ID and a Cloudflare token with account D1 Write access.')
        mem = cls(account, token)
        configured = os.environ.get('D1_DATABASE_ID', '').strip()
        mem.database_id = database_uuid(configured) if configured else mem._find()
        if mem.database_id is None:
            try:
                created = mem._request('', {'name': DATABASE_NAME})
                if not isinstance(created, dict):
                    raise MemoryUnavailable('Invalid D1 database creation response.')
                mem.database_id = database_uuid(created.get('uuid'))
            except MemoryUnavailable as error:
                # A concurrent create or lost response may have created precisely this database.
                mem.database_id = mem._find()
                if mem.database_id is None:
                    raise error
        mem._batch([{'sql': sql} for sql in SCHEMA])
        return mem

    def _batch(self, statements):
        result = self._request('/' + self.database_id + '/query', {'batch': statements})
        if (not isinstance(result, list) or len(result) != len(statements)
                or any(not isinstance(item, dict) or item.get('success') is not True for item in result)):
            raise MemoryUnavailable('D1 did not confirm every SQL statement. Check D1 Write access and database schema.')
        rows = [item.get('results', []) for item in result]
        if any(not isinstance(group, list) or any(not isinstance(row, dict) for row in group) for group in rows):
            raise MemoryUnavailable('D1 returned an invalid SQL result.')
        return rows

    def sync(self, history):
        """Replay the append-only ledger after the last committed post. Never store an outbox draft."""
        latest, = self._batch([{'sql': f'SELECT post_id FROM {TABLE} ORDER BY rowid DESC LIMIT 1'}])
        start = 0
        if latest:
            for i in range(len(history) - 1, -1, -1):
                if str(history[i].get('tweet_id')) == latest[0]['post_id']:
                    start = i + 1
                    break
        rows = []
        for row in history[start:]:
            if not row.get('verified_at'):
                continue
            if (not row.get('posted_at') or not row.get('text') or not str(row.get('tweet_id', '')).isdigit()
                    or not row.get('kind')):
                raise MemoryUnavailable('Verified X history contains an incomplete post. Check the publication ledger.')
            try:
                posted = datetime.fromisoformat(row['posted_at'].replace('Z', '+00:00'))
                if posted.tzinfo is None:
                    raise ValueError()
                posted_at = posted.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError, AttributeError):
                raise MemoryUnavailable('Verified X history contains an invalid publication timestamp.') from None
            source = row.get('source') or {}
            rows.append((str(row['tweet_id']), row['kind'], posted_at, row['text'][:500],
                         str(row.get('event_key') or '')[:160], str(source.get('author_id') or ''),
                         str(source.get('author') or '')[:32], str(source.get('id') or ''),
                         str(source.get('text') or '')[:240]))
        # Each statement stays below D1's 100-bind limit. Each batch is replay-safe independently.
        inserts = []
        for i in range(0, len(rows), 10):
            chunk = rows[i:i+10]
            placeholders = ','.join(['(' + ','.join(['?'] * len(COLUMNS)) + ')'] * len(chunk))
            inserts.append({'sql': f'INSERT INTO {TABLE} ({",".join(COLUMNS)}) VALUES {placeholders} '
                                   'ON CONFLICT(post_id) DO NOTHING',
                            'params': [val for row in chunk for val in row]})
        for i in range(0, len(inserts), 8):
            self._batch(inserts[i:i+8])
        return len(rows)

    def context(self, sources):
        """Recent voice plus prior public exchanges with the authors under consideration."""
        recent = f'SELECT post_id, kind, posted_at, substr(text,1,160) AS text, event_key FROM {TABLE}'
        queries = [{'sql': recent + " WHERE kind = 'reply' ORDER BY posted_at DESC LIMIT 3"},
                   {'sql': recent + " WHERE kind <> 'reply' ORDER BY posted_at DESC LIMIT 3"}]
        authors = list(dict.fromkeys(str(row['author_id']) for row in sources if row.get('author_id')))[:16]
        if authors:
            placeholders = ','.join(['?'] * len(authors))
            by_author = f'FROM {TABLE} WHERE author_id IN ({placeholders})'
            queries.extend([
                {'sql': f'SELECT author_id, author, count(*) AS interactions, max(posted_at) AS last_at {by_author} '
                        'GROUP BY author_id ORDER BY last_at DESC', 'params': authors},
                {'sql': 'WITH ranked AS (SELECT author_id, source_id, source_text, text, posted_at, '
                        f'row_number() OVER (PARTITION BY author_id ORDER BY posted_at DESC) AS recency {by_author}) '
                        'SELECT author_id, source_id, source_text, text, posted_at FROM ranked WHERE recency <= 2 '
                        'ORDER BY recency, posted_at DESC LIMIT 8', 'params': authors}])
        result = self._batch(queries)
        return {'recent_own': sorted(result[0] + result[1], key=lambda row: row['posted_at'], reverse=True),
                'people': result[2] if authors else [], 'conversations': result[3] if authors else []}
