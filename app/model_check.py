"""Bounded live model contract check using fictional sources, never X posting.

This checks endpoint compatibility and the writer's existing validation on three
fixed examples. It is not a general accuracy or engagement benchmark.
"""
import json
from unittest.mock import patch

from . import cloudflare, queue, writer
from .ai_budget import AIPaused, budget_scope


MODEL = '@cf/zai-org/glm-4.7-flash'
FORMATS = ('emoji_question', 'commentary', 'news')
MAX_REQUESTS = 15


class ModelCheckError(ValueError):
    """The replacement has not passed all required fixture formats."""


def fixture_source(now):
    """Synthetic evidence for validation only; this identity is never fetched."""
    return {
        'id': '999000001', 'author': 'AtlasDevKit', 'author_id': '999000002',
        'url': 'https://x.com/AtlasDevKit/status/999000001',
        'text': ('Atlas DevKit 2.4 is available today for AI coding agents. '
                 'Its local test runner now executes 8 worker tests in parallel, '
                 'with no cloud connection required. It keeps test logs on your '
                 'machine. The free plan includes 50 runs per month.'),
        'created_at': now.isoformat(), 'views': 100_000,
        'source_ids': ['999000001'], 'urls': [],
    }


def check():
    """Fail unless all three formats pass; preserve only real AI budget changes."""
    state = queue.load()
    queue.require_open(state)
    now = queue.utcnow()
    source = fixture_source(now)
    report = {'model': MODEL, 'fixture_only': True, 'published': False,
              'maximum_requests': MAX_REQUESTS, 'cases': []}
    calls = 0
    original_complete = writer.complete

    def evaluate_complete(system, data, **kwargs):
        nonlocal calls
        if calls >= MAX_REQUESTS:
            raise ModelCheckError('Model evaluation reached its request bound')
        calls += 1
        result = original_complete(system, data, **{**kwargs, 'model': MODEL})
        stage = 'classify' if system == writer.SOURCE_GATE else 'review' if system == writer.CRITIC else 'write'
        # Only fictional-source model output is logged, never request credentials.
        queue.summary('Fixture model output ' + json.dumps({'stage': stage, 'result': result}, ensure_ascii=True)[:4000])
        return result

    failure = None
    try:
        with budget_scope(state, queue.save_and_push, queue.utcnow), \
             patch.object(writer, 'complete', evaluate_complete):
            for mode in FORMATS:
                # No production history is copied into synthetic evaluation,
                # and fictional events can never enter the publication ledger.
                evaluation_state = {'history': [], 'used_source_ids': [],
                                    'used_urls': [], 'used_events': [], 'used_texts': []}
                try:
                    with patch.object(writer, 'select_format', return_value=mode):
                        draft = writer.generate(source, evaluation_state, now)
                    writer.validate(draft, source)
                    if draft.get('format') != mode:
                        raise ModelCheckError('Model evaluation returned a different format')
                except cloudflare.DraftError as exc:
                    report['cases'].append({'format': mode, 'passed': False,
                                            'reason': str(exc)[:600]})
                    failure = 'At least one fixture failed editorial validation'
                    continue
                report['cases'].append({'format': mode, 'passed': True,
                                        'text': draft['text'], 'reply': draft.get('reply', '')})
    except AIPaused:
        failure = 'Model evaluation paused at an AI spending or provider limit'
    except ModelCheckError as exc:
        failure = str(exc)
    except Exception as exc:
        # Provider/request exception text can contain sensitive data.
        failure = 'Model evaluation stopped (' + type(exc).__name__ + ')'
    finally:
        # patch contexts restore both hooks even after errors. Settle the final
        # token adjustment without adding synthetic history or duplicate claims.
        if calls:
            queue.save_and_push(state)
    report['requests'] = calls
    report['passed'] = (not failure and len(report['cases']) == len(FORMATS)
                        and all(row['passed'] for row in report['cases']))
    if failure:
        report['failure'] = failure
    queue.summary('Replacement model fixture check — synthetic evidence, no X requests or posts.\n\n'
                  '```json\n' + json.dumps(report, ensure_ascii=True, indent=2) + '\n```')
    if not report['passed']:
        raise ModelCheckError('Replacement model did not pass every fixture')
    return report
