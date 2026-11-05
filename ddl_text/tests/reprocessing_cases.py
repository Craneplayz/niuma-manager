"""Synthetic #112 handoff cases; all request/result bodies use shared v0.2.

The outer Python dictionaries are test instructions, not an HTTP/job protocol.
Before/after drafts simulate old/new model outcomes; they are not live evals.
"""
import copy
import json
from pathlib import Path

from ddl_text.extraction import ExtractionAdapter, OfflineDraftClient
from ddl_text.pipeline import process_request
from ddl_text.tests.test_extraction import draft, time_ref

ROOT = Path(__file__).resolve().parents[2]


def cases():
    request = json.loads((ROOT / 'contracts/source-to-text/text-frontend-v02-request.json').read_text(encoding='utf-8'))
    record = copy.deepcopy(request['records'][0])
    record.update(input_id='synthetic:reprocess:assignment-001', source_item_id='assignment-001', context=[],
                  source_label='合成课程作业', text='请于2026年10月8日18点前提交课程报告，请尽快完成。',
                  published_at=dict(value='2026-10-01', precision='date', basis='source'),
                  collected_at='2026-10-06T10:00:00+08:00')
    request.update(request_id='synthetic:reprocess:run', records=[record])
    good = draft(record, title='提交课程报告', description='提交课程报告。',
                 times=[time_ref(record, '2026年10月8日18点', '2026-10-08T18:00:00+08:00')])
    good['relevance'] = dict(state='current', reason='本次课程要求。', evidence=[])
    old = copy.deepcopy(good); old['review_reasons'] = ['合成旧规则误判：尽快需要核实。']
    result = []
    for decision in ('pending', 'confirmed', 'ignored', 'edited', 'completed'):
        result.append(dict(name='success-' + decision, account_alias='synthetic-user-a', source_alias='teaching-a',
            request=copy.deepcopy(request), before_drafts=[old], after_drafts=[good], user_decision=decision,
            expected_consumer_action='replace_pending' if decision == 'pending' else 'preserve_user_decision'))
    result.append(dict(name='no-tasks-to-task', account_alias='synthetic-user-a', source_alias='teaching-a',
        request=copy.deepcopy(request), before_drafts=[], after_drafts=[good], user_decision='pending',
        expected_consumer_action='create_once_after_explicit_reprocess'))
    retry = copy.deepcopy(result[0]); retry.update(name='new-recognition-fails', after_drafts=None,
        expected_consumer_action='retain_previous_result_and_report_failure')
    result.append(retry)
    other = copy.deepcopy(result[0]); other.update(name='other-account-same-item', account_alias='synthetic-user-b',
        expected_consumer_action='never_reuse_other_accounts_result')
    result.append(other)
    # One stable source item has multiple actions; reorder drafts to demonstrate
    # why positional candidate IDs cannot identify persisted user decisions.
    multiple = copy.deepcopy(result[0]); multiple.update(name='multiple-actions-reordered',
        expected_consumer_action='match_actions_without_title_or_candidate_id_as_sole_key')
    r = multiple['request']['records'][0]
    r['text'] += '另请于2026年10月9日前提交问卷。'
    questionnaire = draft(r, title='提交问卷', description='提交问卷。',
        times=[time_ref(r, '2026年10月9日', '2026-10-09')])
    multiple.update(before_drafts=[good, questionnaire], after_drafts=[questionnaire, good])
    result.append(multiple)
    return result


def replay(case, revision):
    """No real model, database or task writes; synthetic failure uses real envelope."""
    if revision not in ('before', 'after'):
        raise ValueError('invalid_revision')
    drafts = case[revision + '_drafts']
    if drafts is None:
        # The production unavailable adapter emits a failed record, never no_tasks.
        return process_request(copy.deepcopy(case['request']))
    return process_request(copy.deepcopy(case['request']),
        ExtractionAdapter(OfflineDraftClient([dict(record=case['request']['records'][0], response=dict(tasks=copy.deepcopy(drafts)))])))
