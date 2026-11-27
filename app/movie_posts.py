"""Schedule the account reset, then original, attributed lines from films and series."""
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from . import reply_writer
from .cloudflare import DraftError


RESET_TEXT = ('Hi, I’m Qofeno AI. I got a reset. From now on, I’m sharing legendary one-liners '
              'from movies and series around the world. One line per post.')
RESET_EVENT = 'qofeno movie and series one-liners reset announcement'
RESET_DRAFT = {'text': RESET_TEXT, 'premise': 'Announce Qofeno’s new film and series format.', 'event_key': RESET_EVENT}
CATALOG = Path(__file__).with_name('movie_lines.json')
CREDIT_FIELDS = ('quote', 'speaker', 'title', 'source')
DEFINITE_REFUSALS = {'Forbidden', 'NotFound', 'TweetNotAvailable', 'TooManyRequests', 'ExpiredBeforeSend'}


def reset_pending(state):
    for row in state.get('history', []):
        ident = row.get('tweet_id')
        if (row.get('kind') in ('original', 'follow') and row.get('verified_at') and not row.get('source')
                and row.get('text') == RESET_TEXT and isinstance(ident, str) and ident.isascii()
                and ident.isdigit() and int(ident) > 0):
            return False
    return True


def reset_ready(state):
    if state.get('outbox') or state.get('pause_reason'):
        raise DraftError('A prior delivery requires reconciliation before the announcement.')
    attempts = [row for row in state.get('delivery_rejections', [])
                if row.get('text') == RESET_TEXT or row.get('event_key') == RESET_EVENT]
    if any(row.get('error') not in DEFINITE_REFUSALS or row.get('outcome') == 'unconfirmed' for row in attempts):
        raise DraftError('The reset announcement has an unconfirmed delivery.')
    burned = (reply_writer.fingerprint(RESET_TEXT) in state.get('used_texts', ())
              or reply_writer.fingerprint(RESET_EVENT) in state.get('used_events', ()))
    if burned and not attempts:
        raise DraftError('The reset announcement was reserved and has no confirmed delivery outcome.')


def vetted(entry):
    if not isinstance(entry, dict) or set(entry) != set(CREDIT_FIELDS):
        return False
    if any(not isinstance(value, str) or not value or value != value.strip() or not value.isprintable()
           for value in entry.values()):
        return False
    try:
        source = urlsplit(entry['source'])
        if (source.scheme != 'https' or not source.hostname or source.username or source.password
                or not source.path.strip('/') or any(char.isspace() for char in entry['source'])):
            return False
    except ValueError:
        return False
    return (all(reply_writer.normalized(entry[key]) for key in ('quote', 'speaker', 'title'))
            and not reply_writer.LINK_OR_TAG.search(entry['quote'] + ' ' + entry['speaker'])
            and reply_writer.weighted_length(f'“{entry["quote"]}” ~ {entry["speaker"]}') <= 280)


def catalog():
    try:
        rows = json.loads(CATALOG.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise DraftError('Unable to read the film and series quotation catalog.') from exc
    if not isinstance(rows, list):
        raise DraftError('Invalid film and series quotation catalog.')
    return [entry for entry in rows if vetted(entry)]


def line_draft(entry):
    quote = entry['quote']
    return {'text': f'“{quote}” ~ {entry["speaker"]}', 'premise': f'{entry["speaker"]} in {entry["title"]}',
            'event_key': 'movie-line ' + reply_writer.fingerprint(quote)}


def credit(draft, state):
    """Recover the exact source record for the draft before reserving a native send."""
    if draft == RESET_DRAFT:
        return None
    entries = catalog()
    extra = state.get('movie_quote_candidate')
    if vetted(extra):
        entries.append(extra)
    return next((entry for entry in entries if line_draft(entry) == draft), None)


def used_line_ids(state):
    used = set(state.get('used_events', ())) | set(state.get('used_texts', ()))
    rows = list(state.get('history', [])) + list(state.get('delivery_rejections', []))
    if state.get('outbox'):
        rows.append(state['outbox'])
    for row in rows:
        event = row.get('event_key')
        text = row.get('text') or row.get('reply')
        if isinstance(event, str) and event:
            used.add(reply_writer.fingerprint(event))
        if not isinstance(text, str) or not text:
            continue
        used.add(reply_writer.fingerprint(text))
        # Older records may lack permanent event IDs or give a different speaker.
        # Compare the dialogue itself, independent of attribution and punctuation.
        credit_row = row.get('movie_credit')
        quote = credit_row.get('quote') if isinstance(credit_row, dict) else None
        if not isinstance(quote, str):
            quote = re.split(r'\s+~\s+', text, maxsplit=1)[0]
        used.add(reply_writer.fingerprint(quote))
    return used


def unused(entry, used):
    draft = line_draft(entry)
    return not any(reply_writer.fingerprint(value) in used for value in (entry['quote'], draft['event_key'], draft['text']))


def validate(draft, state):
    if not isinstance(draft, dict) or set(draft) != set(RESET_DRAFT):
        raise DraftError('Expected a verified film quotation or the account reset announcement.')
    if reset_pending(state):
        if draft != RESET_DRAFT:
            raise DraftError('Verify the reset announcement on X before publishing dialogue.')
        reset_ready(state)
        return draft
    if draft == RESET_DRAFT:
        raise DraftError('The reset announcement has already been verified.')
    if state.get('outbox') or state.get('pause_reason'):
        raise DraftError('A prior publication requires reconciliation.')
    entry = credit(draft, state)
    if entry is None:
        raise DraftError('Dialogue, attribution and title must match a sourced catalog entry.')
    if not unused(entry, used_line_ids(state)):
        raise DraftError('This film or series line has already been published or reserved.')
    return draft


def scheduled_draft(state, now):
    if reset_pending(state):
        return validate(dict(RESET_DRAFT), state)
    if state.get('outbox') or state.get('pause_reason'):
        raise DraftError('A prior publication requires reconciliation.')
    history = state.get('history', [])
    used = used_line_ids(state)
    choices = [entry for entry in catalog() if unused(entry, used)]
    if choices:
        titles = [reply_writer.normalized(row['movie_credit']['title']) for row in history
                  if row.get('kind') == 'original' and row.get('verified_at') and isinstance(row.get('movie_credit'), dict)
                  and isinstance(row['movie_credit'].get('title'), str)]
        count = Counter(titles)
        entry = min(choices, key=lambda item: (reply_writer.normalized(item['title']) in titles[-3:],
                                               count[reply_writer.normalized(item['title'])]))
    else:
        entry = state.get('movie_quote_candidate')
        if not (vetted(entry) and unused(entry, used)):
            # Only the source reader can admit a new line. Keep the evidence with
            # the reservation so send-time validation never relies on another fetch.
            from .movie_catalog import find_quote
            def acceptable(item):
                return vetted(item) and unused(item, used)
            entry = find_quote(history, now=now, accept=acceptable)
            if not acceptable(entry):
                raise DraftError('No unused, source-verified film or series line was found.')
            state['movie_quote_candidate'] = dict(entry)
    return validate(line_draft(entry), state)
