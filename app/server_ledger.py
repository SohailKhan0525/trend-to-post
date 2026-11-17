"""Cloudflare D1 publication authority, outbox and server lease.

Publish the initial Git snapshot in a single D1 transaction. Subsequent saves
replace only changed pages, atomically fenced by both revision and server lease.
"""
import base64
from functools import lru_cache
import json
import re
import zlib
from uuid import uuid4

from .memory import Memory, MemoryUnavailable
from .store import preserve_history, validate


HEAD = 'qofeno_ledger_head_v1'
PAGES = 'qofeno_ledger_pages_v1'
LEASE = 'qofeno_server_lease_v1'
AUTHORITY = 'qofeno_publisher_authority_v1'
LEASE_SECONDS = 300
FRESH_SECONDS = 120
SCHEMA = (
    f'CREATE TABLE IF NOT EXISTS {HEAD} (id INTEGER PRIMARY KEY CHECK (id = 1), revision INTEGER NOT NULL, write_id TEXT NOT NULL)',
    f'CREATE TABLE IF NOT EXISTS {PAGES} (page TEXT PRIMARY KEY NOT NULL, body TEXT NOT NULL)',
    f'''CREATE TABLE IF NOT EXISTS {LEASE} (id INTEGER PRIMARY KEY CHECK (id = 1), instance TEXT NOT NULL,
        release TEXT NOT NULL, phase TEXT NOT NULL, seen INTEGER NOT NULL)''',
    f'''CREATE TABLE IF NOT EXISTS {AUTHORITY} (id INTEGER PRIMARY KEY CHECK (id = 1),
        owner TEXT NOT NULL, last_start INTEGER NOT NULL, last_success INTEGER)''',
    # The old VM runs a release which knows only the original lease table.
    # Enforce the handoff at that table so even its unmodified heartbeat is fenced.
    f'''CREATE TRIGGER IF NOT EXISTS qofeno_github_lease_v1 BEFORE INSERT ON {LEASE}
        WHEN NEW.instance NOT GLOB 'github-*' AND EXISTS (SELECT 1 FROM {AUTHORITY} WHERE id=1 AND owner='github')
        BEGIN SELECT RAISE(IGNORE); END''',
)
LIVE_LEASE = f'EXISTS (SELECT 1 FROM {LEASE} WHERE id=1 AND instance=? AND seen > unixepoch()-{LEASE_SECONDS})'
WINNING_WRITE = f'EXISTS (SELECT 1 FROM {HEAD} WHERE id=1 AND write_id=?)'


def serialize(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def encode(value):
    return base64.b64encode(zlib.compress(serialize(value).encode(), level=3)).decode('ascii')


def pack(state):
    """Stable page boundaries keep the unchanged part of the history off the write path."""
    fields, pages = {}, {}
    for key, value in state.items():
        if isinstance(value, list) and value:
            for index in range(0, len(value), 64):
                pages[f'list:{index}:{key}'] = encode(value[index:index + 64])
        else:
            fields[key] = value
    pages['fields'] = encode(fields)
    return pages


def unpack(pages):
    def decode(key):
        return json.loads(zlib.decompress(base64.b64decode(pages[key], validate=True)))

    try:
        state = decode('fields')
        chunks = {}
        for name in pages:
            if name != 'fields':
                marker, offset, key = name.split(':', 2)
                if marker != 'list' or key in state:
                    raise ValueError()
                chunks.setdefault(key, []).append((int(offset), name))
        for key, parts in chunks.items():
            values = []
            for offset, name in sorted(parts):
                chunk = decode(name)
                if offset != len(values) or not isinstance(chunk, list) or not 0 < len(chunk) <= 64:
                    raise ValueError()
                values.extend(chunk)
            state[key] = values
        return validate(state)
    except (KeyError, TypeError, ValueError, zlib.error, AttributeError):
        raise MemoryUnavailable('D1 publication ledger pages are incomplete or invalid.') from None


def update_pages(previous, pages, write_id):
    """Only the winning transaction's nonce can mutate pages after its CAS."""
    removed = sorted(previous.keys() - pages.keys())
    statements = []
    if removed:
        statements.append({'sql': f'DELETE FROM {PAGES} WHERE page IN (SELECT value FROM json_each(?)) AND {WINNING_WRITE}',
                           'params': [serialize(removed), write_id]})
    changed = [[key, value] for key, value in pages.items() if previous.get(key) != value]
    # Keep each bound parameter comfortably below D1's value size limit.
    group = []
    size = 0
    def write_group():
        statements.append({'sql': f'''INSERT INTO {PAGES}(page, body)
            SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]') FROM json_each(?) WHERE {WINNING_WRITE}
            ON CONFLICT(page) DO UPDATE SET body = excluded.body''',
                           'params': [serialize(group), write_id]})

    for pair in changed:
        if group and size + len(pair[1]) > 150_000:
            write_group()
            group = []
            size = 0
        group.append(pair)
        size += len(pair[1])
    if group:
        write_group()
    return statements


class ServerState(dict):
    """A mutable publication decision bound to the exact D1 version it read."""

    def __init__(self, revision, pages):
        super().__init__(unpack(pages))
        self.revision = revision
        self.pages = pages


class ServerLedger:
    def __init__(self, database):
        self.database = database
        self.query = database._batch
        self.query([{'sql': sql} for sql in SCHEMA])
        self.cached_revision = None
        self.cached_pages = None

    def revision(self):
        rows, = self.query([{'sql': f'SELECT revision FROM {HEAD} WHERE id=1'}])
        return rows[0]['revision'] if rows else None

    def active(self):
        return self.revision() is not None

    def seed(self, state, release):
        """A repeated deployment can never reset or replace an active outbox."""
        if not release or not re.fullmatch(r'[a-f0-9]{40}', release):
            raise ValueError('Seeding requires the exact server release commit.')
        ready = f"""EXISTS (SELECT 1 FROM {LEASE} WHERE id=1 AND release=? AND phase IN ('standby','active')
            AND seen > unixepoch()-{FRESH_SECONDS})"""
        pages = pack(validate(state))
        write_id = uuid4().hex
        statements = [{'sql': f'INSERT INTO {HEAD}(id,revision,write_id) SELECT 1,1,? WHERE {ready} '
                              'ON CONFLICT DO NOTHING RETURNING revision', 'params': [write_id, release]}]
        statements.extend(update_pages({}, pages, write_id))
        seeded = bool(self.query(statements)[0])
        if not seeded and not self.active():
            raise MemoryUnavailable('Handover requires a fresh healthy heartbeat for the requested Azure release.')
        return seeded

    def load(self):
        revision = self.revision()
        if revision is None:
            raise MemoryUnavailable('D1 publication ledger is awaiting the GitHub handover.')
        if revision != self.cached_revision:
            # A consistent snapshot, even when another worker committed between queries.
            head, rows = self.query([{'sql': f'SELECT revision FROM {HEAD} WHERE id=1'},
                                     {'sql': f'SELECT page, body FROM {PAGES}'}])
            revision = head[0]['revision']
            self.cached_pages = {row['page']: row['body'] for row in rows}
            self.cached_revision = revision
        return ServerState(revision, self.cached_pages)

    def save(self, state, instance):
        if not instance or not isinstance(state, ServerState):
            raise MemoryUnavailable('D1 publication writes require a leased server and loaded revision.')
        validate(state)
        preserve_history(unpack(state.pages), state)
        pages = pack(state)
        write_id = uuid4().hex
        statements = [{'sql': f'''UPDATE {HEAD} SET revision=revision+1, write_id=?
            WHERE id=1 AND revision=? AND {LIVE_LEASE} RETURNING revision''',
                       'params': [write_id, str(state.revision), instance]}]
        statements.extend(update_pages(state.pages, pages, write_id))
        result = self.query(statements)[0]
        if not result:
            raise MemoryUnavailable('Publication state or server lease changed. Reload before the next decision.')
        state.revision = self.cached_revision = result[0]['revision']
        state.pages = self.cached_pages = pages

    def heartbeat(self, instance, release, phase):
        if not instance or not re.fullmatch(r'[a-f0-9]{40}', release) or phase not in ('standby', 'active', 'degraded'):
            raise ValueError('Invalid server heartbeat identity or phase.')
        result, = self.query([{'sql': f'''INSERT INTO {LEASE}(id, instance, release, phase, seen)
            VALUES(1, ?, ?, ?, unixepoch()) ON CONFLICT(id) DO UPDATE SET instance=excluded.instance,
            release=excluded.release, phase=excluded.phase, seen=excluded.seen
            WHERE {LEASE}.instance=excluded.instance OR {LEASE}.seen <= unixepoch()-{LEASE_SECONDS}
            RETURNING id''', 'params': [instance, release, phase]}])
        return bool(result)

    def claim_github(self, instance, release):
        """Acquire the existing ledger without displacing an unexpired writer."""
        if not re.fullmatch(r'github-[0-9a-f]{32}', instance) or not re.fullmatch(r'[0-9a-f]{40}', release):
            raise ValueError('Invalid GitHub publishing identity.')
        if not self.active():
            raise MemoryUnavailable('The existing D1 publication ledger is missing. Refusing a fresh start.')
        rows = self.query([
            {'sql': f'''INSERT INTO {AUTHORITY}(id,owner,last_start)
                SELECT 1,'github',unixepoch() WHERE NOT EXISTS (
                    SELECT 1 FROM {LEASE} WHERE id=1 AND seen > unixepoch()-{LEASE_SECONDS})
                ON CONFLICT(id) DO NOTHING'''},
            {'sql': f'''INSERT INTO {LEASE}(id,instance,release,phase,seen)
                SELECT 1,?,?,'active',unixepoch() WHERE EXISTS (
                    SELECT 1 FROM {AUTHORITY} WHERE id=1 AND owner='github')
                ON CONFLICT(id) DO UPDATE SET instance=excluded.instance, release=excluded.release,
                    phase=excluded.phase, seen=excluded.seen
                WHERE {LEASE}.seen <= unixepoch()-{LEASE_SECONDS} RETURNING id''', 'params': [instance, release]},
            {'sql': f'''UPDATE {AUTHORITY} SET last_start=unixepoch()
                WHERE id=1 AND {LIVE_LEASE}''', 'params': [instance]},
        ])
        return bool(rows[1])

    def finish_github(self, instance):
        done, _ = self.query([
            {'sql': f'''UPDATE {AUTHORITY} SET last_success=unixepoch()
                WHERE id=1 AND owner='github' AND {LIVE_LEASE} RETURNING id''', 'params': [instance]},
            {'sql': f'DELETE FROM {LEASE} WHERE id=1 AND instance=?', 'params': [instance]},
        ])
        if not done:
            raise MemoryUnavailable('GitHub lost its publishing lease before recording completion.')

    def release(self, instance):
        self.query([{'sql': f'DELETE FROM {LEASE} WHERE id=1 AND instance=?', 'params': [instance]}])

    def check_lease(self, instance):
        if not instance or not self.query([{'sql': f'SELECT 1 WHERE {LIVE_LEASE}', 'params': [instance]}])[0]:
            raise MemoryUnavailable('The server process does not own a live D1 publishing lease.')

    def status(self, release, ready=False):
        if not release or not re.fullmatch(r'[a-f0-9]{40}', release):
            raise ValueError('Specify the exact 40-character lowercase release commit.')
        phases = "('standby','active')" if ready else "('active')"
        rows, = self.query([{'sql': f'''SELECT phase FROM {LEASE} WHERE id=1 AND release=? AND phase IN {phases}
            AND seen > unixepoch()-{FRESH_SECONDS} AND (phase='standby' OR EXISTS (SELECT 1 FROM {HEAD} WHERE id=1))''',
                             'params': [release]}])
        return rows[0]['phase'] if rows else None


@lru_cache(maxsize=1)
def connect():
    return ServerLedger(Memory.connect())
