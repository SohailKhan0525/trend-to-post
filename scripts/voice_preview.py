"""Unpublished production-model voice samples; never import the publishing runtime.

Run with ``python -m scripts.voice_preview``. Fixed fixtures exercise the actual
planner, writer and independent reviewers. Silence is a valid editorial result.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import urllib.request

from app import ai_budget, cloudflare, personality, reply_writer


MAX_REQUESTS = 20
PUBLIC_SOURCE = 'https://x.com/boardyai/status/2123053655528210596'
PUBLIC_TRANSCRIPT = 'https://www.teapawt.org/boardyai/status/2123053655528210596'
BOARDY_TEXT = "seed-stage founders are the most prepared for post-agi because they're already used to not getting paid"
FORBIDDEN_ENVIRONMENT = ('X_AUTH_TOKEN', 'X_CT0', 'CLOUDFLARE_D1_API_TOKEN',
                         'D1_DATABASE_ID', 'QOFENO_INSTANCE', 'GH_TOKEN', 'GITHUB_TOKEN')


class PreviewIsolationError(RuntimeError):
    pass


class PreviewBudget(ai_budget.Budget):
    """Use the production cost accounting in memory, without a production ledger."""
    def __init__(self, started):
        super().__init__({}, lambda state: None, lambda: started)
        self.calls = 0

    def reserve(self, model, payload):
        if self.calls >= MAX_REQUESTS:
            raise ai_budget.AIPaused('Preview request ceiling reached.', ai_budget.next_hour(self.clock()))
        reservation = super().reserve(model, payload)
        self.calls += 1
        return reservation


def emit(event, **fields):
    # JSON keeps model-generated newlines from becoming workflow commands.
    print(json.dumps({'preview': True, 'published': False, 'event': event, **fields}, ensure_ascii=False), flush=True)


@contextmanager
def isolated_model_calls(budget, trace):
    if any(os.environ.get(key) for key in FORBIDDEN_ENVIRONMENT):
        raise PreviewIsolationError('Preview refuses X, D1 or GitHub write credentials.')
    account = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '').strip()
    model = cloudflare.selected_model()
    expected = f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}'

    class NoRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, response, code, message, headers, url):
            return None

    opener = urllib.request.build_opener(NoRedirects)
    previous_urlopen = urllib.request.urlopen
    previous_personality = personality.complete
    previous_reply = reply_writer.complete

    def only_ai(request, *args, **kwargs):
        if not isinstance(request, urllib.request.Request) or request.full_url != expected or request.get_method() != 'POST':
            raise PreviewIsolationError('Preview blocked a request outside the selected Workers AI endpoint.')
        return opener.open(request, *args, **kwargs)

    def observed_complete(system, data, **kwargs):
        stage = ('original_planner' if system == personality.DECIDE else
                 'original_writer' if system == personality.WRITE else
                 'original_review' if system == personality.REVIEW else
                 'reply_writer' if system == reply_writer.VOICE else 'reply_review')
        result = cloudflare.complete(system, data, **kwargs)
        trace.append({'stage': stage, 'response': result})
        return result

    token = ai_budget.ACTIVE.set(budget)
    urllib.request.urlopen = only_ai
    personality.complete = reply_writer.complete = observed_complete
    try:
        yield
    finally:
        personality.complete, reply_writer.complete = previous_personality, previous_reply
        urllib.request.urlopen = previous_urlopen
        ai_budget.ACTIVE.reset(token)


def mention(ident, text, now, *, parent=None, own_parent=False):
    author = 'PreviewReader'
    row = {'id': str(ident), 'author_id': '90001', 'author': author, 'text': text,
           'origin': 'mention', 'intent': 'invite', 'views': None,
           'created_at': (now - timedelta(minutes=1)).isoformat(),
           'source_ids': [str(ident)], 'urls': [], 'url': f'https://x.com/{author}/status/{ident}'}
    if parent:
        row.update(parent_id=parent['id'], parent_author=parent['author'], parent_context=parent)
        if own_parent:
            row['conversation_role'] = 'reply_to_qofeno'
    return row


def scenarios(now):
    # This historical replay retains the actual quoted post's date. The reader
    # invitation is fictional; no claim is made that Boardy addressed Qofeno.
    replay = datetime(2026, 11, 18, 14, 5, tzinfo=timezone.utc)
    boardy = mention('910001', '@qofenoai what do you make of this?', replay,
                     parent={'id': '2123053655528210596', 'author': 'boardyai', 'text': BOARDY_TEXT})
    correction = mention('910002',
                         '@qofenoai Old computers still fail. Our 2013 machine lost update support, '
                         'and finding RAM for the 2012 one took days. Predictable maintenance is the advantage.', now,
                         parent={'id': '910012', 'author': 'qofenoai', 'author_id': '99999',
                                 'text': 'Old computers have run out of new ways to break.'}, own_parent=True)
    disagreement = mention('910003',
                           '@qofenoai If an AI coding agent passes the tests, nobody needs to read its patch.', now)
    serious = mention('910004',
                      '@qofenoai The editor overwrote my only copy of the draft. There is no undo entry. What should I do first?', now)
    originals = (
        ('original_internet_culture', 'The same group-chat sticker now means both running late and already outside; '
         'even the sender needs the previous message to tell which.'),
        ('original_founder', 'A founder removed the account signup from a demo so visitors could try the product immediately.'),
    )
    rows = [
        {'id': 'public_founder_joke', 'kind': 'reply', 'source': boardy, 'clock': replay,
         'provenance': {'kind': 'public_quote_with_fictional_reader_invitation', 'author': '@boardyai',
                        'published_at': '2026-11-18T14:00:00Z', 'url': PUBLIC_SOURCE, 'transcript': PUBLIC_TRANSCRIPT},
         'purpose': 'Answer a playful public post in one short line without borrowing its punchline.'},
        {'id': 'hardware_correction', 'kind': 'reply', 'source': correction, 'clock': now,
         'provenance': {'kind': 'adapted_fixture_not_a_real_qofeno_conversation',
                        'inspiration': PUBLIC_TRANSCRIPT, 'note': 'Paraphrases the hardware discussion; the Qofeno parent is invented.'},
         'purpose': 'Accept a concrete correction without repeating the mistaken claim.'},
        {'id': 'disagreement', 'kind': 'reply', 'source': disagreement, 'clock': now,
         'provenance': {'kind': 'synthetic_fixture'}, 'purpose': 'Give a useful disagreement without flattery or insults.'},
        {'id': 'serious_lost_work', 'kind': 'reply', 'source': serious, 'clock': now,
         'provenance': {'kind': 'synthetic_fixture'}, 'purpose': 'Answer a serious request without forcing a joke.'},
    ]
    for index, (ident, text) in enumerate(originals):
        rows.append({'id': ident, 'kind': 'original', 'clock': now + timedelta(minutes=30 * index),
                     'source': {'id': str(920001 + index), 'author_id': '90002', 'author': 'PreviewBuilder',
                                'text': text, 'created_at': now.isoformat(), 'views': 20000,
                                'source_ids': [str(920001 + index)], 'urls': []},
                     'provenance': {'kind': 'synthetic_fixture'},
                     'purpose': 'Let the production planner choose an original angle or silence, then independently review it.'})
    # Cover both original niches and substantive exchanges before optional humor
    # if the shared, conservative preview budget cannot cover all six cases.
    return [rows[4], rows[2], rows[1], rows[5], rows[3], rows[0]]


def evaluate(case):
    now = case['clock']
    state = {'history': [], 'used_source_ids': [], 'used_urls': [],
             'actions_inbox_since': now.isoformat()}
    memory = {'recent_own': [], 'people': [], 'conversations': []}
    if case['kind'] == 'reply':
        return {'status': 'approved_unpublished', 'draft': reply_writer.generate(case['source'], state, now, memory=memory)}
    plan = personality.choose([case['source']], memory, state, now, ['original'])
    if plan['action'] == 'silent':
        return {'status': 'silent', 'plan': plan}
    return {'status': 'approved_unpublished', 'plan': plan,
            'draft': personality.original(plan, memory, state, now)}


def main():
    started = datetime.now(timezone.utc)
    budget, trace, results = PreviewBudget(started), [], []
    emit('start', timestamp=started.isoformat(), release=os.environ.get('GITHUB_SHA', 'local'),
         model=cloudflare.selected_model(), request_ceiling=MAX_REQUESTS,
         local_hour_neuron_limit=ai_budget.HOURLY_LIMIT,
         note='Historical and synthetic fixtures only. No X or D1 requests; no account history loaded.')
    failed = False
    try:
        with isolated_model_calls(budget, trace):
            for case in scenarios(started):
                trace.clear()
                metadata = {key: value for key, value in case.items() if key != 'clock'}
                metadata['evaluation_time'] = case['clock'].isoformat()
                try:
                    outcome = evaluate(case)
                except cloudflare.DraftError as exc:
                    outcome = {'status': 'rejected_unpublished', 'reason': str(exc)[:300]}
                except ai_budget.AIPaused as exc:
                    outcome = {'status': 'paused', 'reason': exc.reason, 'until': exc.until.isoformat()}
                    results.append({**metadata, **outcome, 'model_steps': list(trace)})
                    emit('scenario', **results[-1])
                    break  # Never retry a provider quota or local spending pause.
                except Exception as exc:
                    failed = True
                    outcome = {'status': 'error', 'error_type': type(exc).__name__}
                    results.append({**metadata, **outcome, 'model_steps': list(trace)})
                    emit('scenario', **results[-1])
                    break  # Auth/network failures do not justify burning more calls.
                results.append({**metadata, **outcome, 'model_steps': list(trace)})
                emit('scenario', **results[-1])
    except PreviewIsolationError as exc:
        emit('isolation_error', reason=str(exc))
        failed = True
    report = {'preview': True, 'published': False, 'started_at': started.isoformat(),
              'finished_at': datetime.now(timezone.utc).isoformat(), 'model': cloudflare.selected_model(),
              'requests': budget.calls, 'usage': budget.state.get('ai_usage', {}), 'results': results}
    emit('complete', **{key: value for key, value in report.items() if key not in ('preview', 'published', 'results')},
         approved=sum(row['status'] == 'approved_unpublished' for row in results),
         rejected=sum(row['status'] == 'rejected_unpublished' for row in results),
         silent=sum(row['status'] == 'silent' for row in results))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        # Escaped preformatted JSON is reviewable without interpreting model text
        # as links, images or GitHub Actions commands.
        Path(summary).write_text('## Unpublished voice preview\n\n<pre>' +
                                 html.escape(json.dumps(report, indent=2, ensure_ascii=False)) + '</pre>\n', encoding='utf-8')
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
