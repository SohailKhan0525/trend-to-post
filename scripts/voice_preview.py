"""Unpublished production-model originals and replies, evaluated without publishing.

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

from app import ai_budget, cloudflare, movie_posts, personality, reply_writer


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
        stages = {personality.DECIDE: 'reply_planner', reply_writer.VOICE: 'reply_writer', reply_writer.REVIEW: 'reply_review'}
        if system not in stages:
            raise PreviewIsolationError('Unknown model request in the voice preview.')
        result = cloudflare.complete(system, data, **kwargs)
        trace.append({'stage': stages[system], 'response': result})
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
    identity = mention('910003', '@qofenoai Are you a human or an AI?', now)
    serious = mention('910004',
                      '@qofenoai The editor overwrote my only copy of the draft. There is no undo entry. What should I do first?', now)
    greeting = mention('910005', '@qofenoai hi', now)
    flaky_build = mention('910006', '@qofenoai The build passes if I run it three times. Can I call it fixed?', now)
    discovery = {'id': '920001', 'author_id': '90002', 'author': 'PreviewBuilder',
                 'text': 'Our coding agent passes the tests, but it also edits the tests whenever a check fails. '
                         'We still read every patch before merging.',
                 'origin': 'discovery', 'created_at': (now - timedelta(hours=1)).isoformat(),
                 'views': 20000, 'source_ids': ['920001'], 'urls': [],
                 'url': 'https://x.com/PreviewBuilder/status/920001'}
    rows = [
        {'id': 'movie_reset', 'kind': 'original', 'clock': now,
         'provenance': {'kind': 'synthetic_fixture'},
         'purpose': 'Show the one-time account announcement.'},
        {'id': 'first_movie_line', 'kind': 'original', 'clock': now,
         'provenance': {'kind': 'synthetic_fixture'},
         'purpose': 'Show the next attributed line with a verified reset in synthetic history.'},
        {'id': 'brief_direct_greeting', 'kind': 'reply', 'source': greeting, 'clock': now,
         'provenance': {'kind': 'synthetic_fixture'},
         'purpose': 'Allow a short public acknowledgement while still returning a valid private premise.'},
        {'id': 'discovered_agent_tests', 'kind': 'reply', 'source': discovery, 'clock': now,
         'provenance': {'kind': 'synthetic_fixture'},
         'purpose': 'Use the production discovery planner and reviewer; add a specific point rather than generic agent commentary.'},
        {'id': 'flaky_build', 'kind': 'reply', 'source': flaky_build, 'clock': now,
         'provenance': {'kind': 'synthetic_fixture'},
         'purpose': 'Give a direct answer to a playful engineering question; dry wit is optional.'},
        {'id': 'hardware_correction', 'kind': 'reply', 'source': correction, 'clock': now,
         'provenance': {'kind': 'adapted_fixture_not_a_real_qofeno_conversation',
                        'inspiration': PUBLIC_TRANSCRIPT, 'note': 'Paraphrases the hardware discussion; the Qofeno parent is invented.'},
         'purpose': 'Accept a concrete correction without repeating the mistaken claim.'},
        {'id': 'direct_identity', 'kind': 'reply', 'source': identity, 'clock': now,
         'provenance': {'kind': 'synthetic_fixture'},
         'purpose': 'Answer the explicit identity question honestly; compare with the ordinary technical reply that needs no AI announcement.'},
        {'id': 'serious_lost_work', 'kind': 'reply', 'source': serious, 'clock': now,
         'provenance': {'kind': 'synthetic_fixture'}, 'purpose': 'Answer a serious request without forcing a joke.'},
        {'id': 'public_founder_joke', 'kind': 'reply', 'source': boardy, 'clock': replay,
         'provenance': {'kind': 'public_quote_with_fictional_reader_invitation', 'author': '@boardyai',
                        'published_at': '2026-11-18T14:00:00Z', 'url': PUBLIC_SOURCE, 'transcript': PUBLIC_TRANSCRIPT},
         'purpose': 'Answer a playful public post in one short line without borrowing its punchline.'},
    ]
    return rows


def evaluate(case, *, progress=None):
    progress = progress if progress is not None else {}
    now = case['clock']
    state = {'history': [], 'used_source_ids': [], 'used_urls': [],
             'used_events': [], 'used_texts': [],
             'actions_inbox_since': now.isoformat()}
    if case['kind'] == 'original' and 'source' not in case:
        if case['id'] == 'first_movie_line':
            reset = movie_posts.scheduled_draft(state, now)
            state['history'].append({**reset, 'kind': 'original', 'tweet_id': '930030',
                                     'posted_at': now.isoformat(), 'verified_at': now.isoformat()})
        return {'status': 'approved_unpublished', 'draft': movie_posts.scheduled_draft(state, now)}
    memory = {'recent_own': [], 'people': [], 'conversations': []}
    if case['kind'] != 'reply':
        raise PreviewIsolationError('Expected a standalone original or a reply to the supplied source.')
    if case['source'].get('origin') == 'mention':
        try:
            draft = reply_writer.generate(case['source'], state, now, memory=memory)
        except reply_writer.MentionDeclined:
            return {'status': 'silent'}
        return {'status': 'approved_unpublished', 'draft': draft}
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
    return {'status': 'approved_unpublished', 'plan': plan,
            'draft': reply_writer.generate(case['source'], state, now, memory=memory, angle=plan['angle'])}


def main():
    started = datetime.now(timezone.utc)
    budget, trace, results = PreviewBudget(), [], []
    cases = scenarios(started)
    emit('start', timestamp=started.isoformat(), release=os.environ.get('GITHUB_SHA', 'local'),
         model=cloudflare.selected_model(), request_ceiling=MAX_REQUESTS,
         local_hour_neuron_limit=ai_budget.HOURLY_LIMIT,
         note='Historical and synthetic fixtures only. No X or D1 requests; no account history loaded.')
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
