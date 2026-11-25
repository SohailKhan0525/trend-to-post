"""Unpublished production-model reply samples; never invoke the publishing runtime.

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

from app import ai_budget, cloudflare, personality, reply_writer, sources


MAX_REQUESTS = 16
FORBIDDEN_ENVIRONMENT = ('X_AUTH_TOKEN', 'X_CT0', 'CLOUDFLARE_D1_API_TOKEN',
                         'D1_DATABASE_ID', 'QOFENO_INSTANCE', 'GH_TOKEN', 'GITHUB_TOKEN')


class PreviewIsolationError(RuntimeError):
    pass


class PreviewBudget(ai_budget.Budget):
    """Use the production cost accounting in memory, without a production ledger."""
    def __init__(self, *, clock=None):
        super().__init__({}, lambda state: None, clock or (lambda: datetime.now(timezone.utc)))
        self.calls = 0
        self.run_neurons_reserved = 0

    def reserve(self, model, payload):
        if self.calls >= MAX_REQUESTS:
            raise ai_budget.AIPaused('Preview request ceiling reached.', ai_budget.next_hour(self.clock()))
        amount = ai_budget.estimate_reservation(model, payload)
        if self.run_neurons_reserved + amount > ai_budget.HOURLY_LIMIT:
            raise ai_budget.AIPaused('Preview run neuron ceiling reached.', ai_budget.next_hour(self.clock()))
        reservation = super().reserve(model, payload)
        self.calls += 1
        self.run_neurons_reserved += reservation[0]
        return reservation

    def settle(self, reservation, output):
        before = self.state['ai_usage']['neurons_reserved']
        super().settle(reservation, output)
        self.run_neurons_reserved += self.state['ai_usage']['neurons_reserved'] - before


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
        if system in (personality.WRITE, personality.REVIEW):
            raise PreviewIsolationError('This preview cannot generate or review original posts.')
        fields = (kwargs.get('schema') or {}).get('properties', {})
        stage = ('reply_planner' if 'action' in fields and 'angle' in fields else
                 'reply_review' if 'source_evidence' in fields else 'reply_writer')
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
    bottleneck = mention('910007',
                         '@qofenoai If AI can solve most intellectual tasks, what becomes the main bottleneck: '
                         'compute, energy, data, people, or something else? Explain your choice.', now)
    backups = mention('910008',
                      '@qofenoai For a small team choosing a backup system, should fast restores or an immutable copy '
                      'come first? Explain the trade-off and when your choice would change.', now)
    correction = mention('910002',
                         '@qofenoai Old computers still fail. Our 2013 machine lost update support, '
                         'and finding RAM for the 2012 one took days. Predictable maintenance is the advantage.', now,
                         parent={'id': '910012', 'author': 'qofenoai', 'author_id': '99999',
                                 'text': 'Old computers have run out of new ways to break.'}, own_parent=True)
    greeting = mention('910005', '@qofenoai hi', now)
    discovery = {'id': '920001', 'author_id': '90002', 'author': 'PreviewBuilder',
                 'text': 'Our coding agent passes the tests, but it also edits the tests whenever a check fails. '
                         'We still read every patch before merging.',
                 'origin': 'discovery', 'created_at': (now - timedelta(hours=1)).isoformat(),
                 'views': 20000, 'source_ids': ['920001'], 'urls': [],
                 'url': 'https://x.com/PreviewBuilder/status/920001'}
    rows = [
        {'id': 'reasoned_ai_bottleneck', 'kind': 'reply', 'source': bottleneck, 'clock': now,
         'expected_reply_style': 'reasoned_answer',
         'provenance': {'kind': 'synthetic_adaptation_of_user_screenshot',
                        'note': 'The pictured question addressed Grok, not Qofeno. This is a fictional Qofeno invitation, '
                                'written independently; no Grok answer is supplied or copied.'},
         'purpose': 'Answer the hypothetical with a defensible choice and explanation rather than a one-line label.'},
        {'id': 'reasoned_backup_tradeoff', 'kind': 'reply', 'source': backups, 'clock': now,
         'expected_reply_style': 'reasoned_answer', 'provenance': {'kind': 'synthetic_fixture'},
         'purpose': 'Explain an unrelated practical trade-off and a condition that changes the answer.'},
        {'id': 'brief_direct_greeting', 'kind': 'reply', 'source': greeting, 'clock': now,
         'expected_reply_style': 'short',
         'provenance': {'kind': 'synthetic_fixture'},
         'purpose': 'Allow a short public acknowledgement while still returning a valid private premise.'},
        {'id': 'discovered_agent_tests', 'kind': 'reply', 'source': discovery, 'clock': now,
         'provenance': {'kind': 'synthetic_fixture'},
         'purpose': 'Use the production discovery planner and reviewer; add a specific point rather than generic agent commentary.'},
        {'id': 'hardware_correction', 'kind': 'reply', 'source': correction, 'clock': now,
         'provenance': {'kind': 'synthetic_fixture', 'note': 'The Qofeno parent is invented.'},
         'purpose': 'Accept a concrete correction without repeating the mistaken claim.'},
    ]
    return rows


def evaluate(case, *, progress=None):
    progress = progress if progress is not None else {}
    now = case['clock']
    state = {'history': [], 'used_source_ids': [], 'used_urls': [],
             'used_events': [], 'used_texts': [],
             'actions_inbox_since': now.isoformat()}
    memory = {'recent_own': [], 'people': [], 'conversations': []}
    if case['kind'] != 'reply':
        raise PreviewIsolationError('This preview only evaluates replies.')
    reasoned = sources.is_reasoned_question(case['source'])
    if case.get('expected_reply_style') == 'reasoned_answer' and not reasoned:
        raise PreviewIsolationError('The reasoned-answer fixture no longer passes production eligibility.')
    progress['selected_reply_style'] = 'reasoned_answer' if reasoned else 'short'
    options = {'reasoned_answer': True} if reasoned else {}
    if case['source'].get('origin') == 'mention':
        return {'status': 'approved_unpublished', **progress,
                'draft': reply_writer.generate(case['source'], state, now, memory=memory, **options)}
    # D1's normal context projection includes recent public text and author
    # exchanges. These labeled fixtures exercise that shape without reading D1.
    previous = [('An agent in a real codebase is harder than marketing suggests.', 'Generic agent skepticism'),
                ("As an AI, I process data rather than drink coffee.", 'Unnecessary AI introduction')]
    for index, (text, event) in enumerate(previous):
        posted = (now - timedelta(hours=2, minutes=index)).isoformat()
        row = {'post_id': str(930001 + index), 'kind': 'reply', 'posted_at': posted,
               'text': text, 'event_key': event}
        memory['recent_own'].append(row)
        state['history'].insert(0, {**row, 'tweet_id': row['post_id'], 'verified_at': posted})
    plan = personality.choose([case['source']], memory, state, now, ['reply'], allow_discovery_replies=True)
    progress['plan'] = plan
    if plan['action'] == 'silent':
        return {'status': 'silent', **progress}
    if plan['action'] != 'reply' or plan['source_id'] != case['source']['id']:
        raise PreviewIsolationError('The preview planner must select this supplied reply source.')
    return {'status': 'approved_unpublished', **progress,
            'draft': reply_writer.generate(case['source'], state, now, memory=memory, angle=plan['angle'], **options)}


def main():
    started = datetime.now(timezone.utc)
    budget, trace, results = PreviewBudget(), [], []
    cases = scenarios(started)
    emit('start', timestamp=started.isoformat(), release=os.environ.get('GITHUB_SHA', 'local'),
         model=cloudflare.selected_model(), request_ceiling=MAX_REQUESTS,
         local_hour_neuron_limit=ai_budget.HOURLY_LIMIT,
         note='Synthetic fixtures only. No X or D1 requests; no account history loaded.')
    failed, pause = False, None
    try:
        with isolated_model_calls(budget, trace):
            for case in cases:
                trace.clear()
                metadata = {key: value for key, value in case.items() if key != 'clock'}
                metadata['evaluation_time'] = case['clock'].isoformat()
                progress = {}
                try:
                    outcome = evaluate(case, progress=progress)
                except cloudflare.DraftError as exc:
                    outcome = {**progress, 'status': 'rejected_unpublished', 'reason': str(exc)[:300]}
                except ai_budget.AIPaused as exc:
                    pause = {'reason': exc.reason, 'until': exc.until.isoformat(), 'case_id': case['id']}
                    outcome = {**progress, 'status': 'paused', **pause}
                    results.append({**metadata, **outcome, 'model_steps': list(trace)})
                    emit('scenario', **results[-1])
                    break  # Never retry a provider quota or local spending pause.
                except Exception as exc:
                    failed = True
                    outcome = {**progress, 'status': 'error', 'error_type': type(exc).__name__}
                    results.append({**metadata, **outcome, 'model_steps': list(trace)})
                    emit('scenario', **results[-1])
                    break  # Auth/network failures do not justify burning more calls.
                results.append({**metadata, **outcome, 'model_steps': list(trace)})
                emit('scenario', **results[-1])
    except PreviewIsolationError as exc:
        emit('isolation_error', reason=str(exc))
        failed = True
    completed = {row['id'] for row in results if row['status'] in ('approved_unpublished', 'rejected_unpublished', 'silent')}
    status = 'incomplete_paused' if pause else 'incomplete_error' if failed else 'completed'
    report = {'preview': True, 'published': False, 'status': status, 'pause': pause,
              'total_cases': len(cases), 'completed_cases': len(completed),
              'remaining_case_ids': [case['id'] for case in cases if case['id'] not in completed],
              'approved_samples': sum(row['status'] == 'approved_unpublished' for row in results),
              'started_at': started.isoformat(),
              'finished_at': datetime.now(timezone.utc).isoformat(), 'model': cloudflare.selected_model(),
              'requests': budget.calls, 'run_neurons_reserved': budget.run_neurons_reserved,
              'usage': budget.state.get('ai_usage', {}), 'results': results}
    emit('complete', **{key: value for key, value in report.items() if key not in ('preview', 'published', 'results')},
         approved=sum(row['status'] == 'approved_unpublished' for row in results),
         rejected=sum(row['status'] == 'rejected_unpublished' for row in results),
         silent=sum(row['status'] == 'silent' for row in results))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        # Escaped preformatted JSON is reviewable without interpreting model text
        # as links, images or GitHub Actions commands.
        Path(summary).write_text(f'## Unpublished voice preview: {status}\n\n'
                                 f'Completed {len(completed)} of {len(cases)} cases; '
                                 f'{report["approved_samples"]} approved unpublished samples.\n\n<pre>' +
                                 html.escape(json.dumps(report, indent=2, ensure_ascii=False)) + '</pre>\n', encoding='utf-8')
    if status != 'completed':
        print('::error::Voice preview incomplete. See the reported pause/error and remaining cases; the publishing workflow is independent.', flush=True)
    return 2 if pause else int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
