"""Opt-in v0.4 batch entry. Authentication, persistence and HTTP remain server-owned."""
from copy import deepcopy
from dataclasses import replace
import json
import math
import time

from contracts.teaching_io import (MAX_BYTES, aggregate_status, canonical, check_pair,
    check_request, snapshot_digest, verified_set)

from .adapters import AgentError, OUTPUT_DIAGNOSTICS, safe_schema_detail
from .chat_client import ChatClient
from .teaching_draft import CLASSIFICATION_MODE, process_teaching_draft
from .teaching_policy import teaching_policy_manifest
from contracts.recognition_errors import describe
from . import diagnostics

MESSAGES={
    'agent_timeout':'识别超时，保留其他记录的结果。',
    'agent_unavailable':'识别服务暂不可用。',
    'invalid_agent_output':'识别结果未通过结构或证据校验。',
    'limit_exceeded':'单条结果超过批次容量，请拆分输入后重试。',
}


def validate_teaching_result(request,result,*,verified_snapshot_digests=()):
    """Check source, evidence and output consistency; never reclassify model decisions."""
    check_pair(request,result,verified_snapshot_digests=verified_snapshot_digests)
    return result


def _failure(entry,code,retryable=False,attempted=False,diagnostic=None,detail=None):
    row = dict(input_id=entry['record']['input_id'],snapshot_id=entry['record']['snapshot_id'],
        source=deepcopy(entry['source']),attempted=attempted,status='failed',decisions=[],warnings=[],
        error=dict(code=code,message=(OUTPUT_DIAGNOSTICS.get(diagnostic, MESSAGES[code])
                   if code=='invalid_agent_output' else MESSAGES[code]),retryable=retryable))
    safe_detail = safe_schema_detail(detail)
    row['error'] = describe(row['error'], reason=diagnostic)
    if safe_detail and diagnostic == 'draft_schema': row['error']['diagnostic'] = safe_detail
    return row


@diagnostics.traced
def process_teaching_request(request,*,client,settings,verified_snapshot_digests=(),budget_seconds=90,diagnostic_event=None):
    """Return v0.4 JSON for a bounded batch; JSON itself never grants source trust.

    The server computes verified_snapshot_digests only after resolving the exact
    entries from this authenticated user's captured source. No retries, fetching,
    writes, scheduling, auto-completion or action-identity matching happen here.
    """
    try:
        check_request(request)
        request=deepcopy(request)
        trusted=verified_set(request,verified_snapshot_digests)
        if (isinstance(budget_seconds,bool) or not isinstance(budget_seconds,(int,float))
                or not math.isfinite(budget_seconds) or not 0<budget_seconds<=180 or settings.max_retries!=0):
            raise ValueError('invalid teaching budget')
        if settings.mode=='real' and type(client) is not ChatClient:
            raise ValueError('unsupported teaching provider')
    except Exception:
        raise AgentError('invalid_input',False) from None
    # Fail before any execution if deployed files no longer match loaded code.
    policy=teaching_policy_manifest(settings) if settings.mode=='real' else None
    result=dict(contract_version='0.4',classification_mode=CLASSIFICATION_MODE,request_id=request['request_id'],
        policy_version=policy['version'] if policy else None,status='failed',record_results=[])
    expires=time.monotonic()+budget_seconds
    emit = diagnostics.emit
    def unchanged_policy():
        if policy is None:return
        try:
            if teaching_policy_manifest(settings)==policy:return
        except Exception:
            pass
        raise AgentError('agent_unavailable',False)

    for i,entry in enumerate(request['records']):
        emit('record', record_index=i, status='started')
        attempted=False
        try:
            remaining=expires-time.monotonic()
            if remaining<=0:raise AgentError('agent_timeout',True)
            unchanged_policy()
            attempted=True
            started=time.monotonic()
            emit('task_recognition',timeout_seconds=min(settings.timeout_seconds,remaining))
            preview=process_teaching_draft(entry['record'],client=client,
                settings=replace(settings,timeout_seconds=min(settings.timeout_seconds,remaining)),
                source_verified=snapshot_digest(entry) in trusted)
            unchanged_policy()
            row=dict(input_id=entry['record']['input_id'],snapshot_id=entry['record']['snapshot_id'],
                source=deepcopy(entry['source']),attempted=True,status='success' if preview.decisions else
                'no_tasks' if preview.no_action_complete else 'incomplete',
                decisions=list(preview.decisions),warnings=list(preview.warnings),error=None,
                empty_decision=preview.empty_decision)
            single=dict(request,records=[entry])
            single_trust={snapshot_digest(entry)} if snapshot_digest(entry) in trusted else set()
            validate_teaching_result(single,dict(result,record_results=[row],status=aggregate_status([row])),
                                     verified_snapshot_digests=single_trust)
        except AgentError as error:
            emit('task_recognition_failed',code=error.code,diagnostic=error.diagnostic,schema=error.detail,
                 seconds=time.monotonic()-started if attempted else 0)
            code=error.code if error.code in MESSAGES else 'agent_unavailable'
            row=_failure(entry,code,error.retryable is True,attempted=attempted,diagnostic=error.diagnostic,detail=error.detail)
        except Exception as error:
            from jsonschema import ValidationError
            reason = 'result_contract' if isinstance(error,(ValueError,ValidationError)) else 'internal_error'
            diagnostics.failure(error, stage='task_validation_failed',reason=reason)
            row=_failure(entry,'invalid_agent_output' if reason=='result_contract' else 'agent_unavailable',attempted=attempted,diagnostic=reason)
        # Reserve exact safe failure envelopes for all later records. A large
        # earlier result must not swallow normal later records or truncate proof.
        reserve=[_failure(e,'limit_exceeded') for e in request['records'][i+1:]]
        # Reserve the longest aggregate status as well as later row envelopes.
        proposal=dict(result,status='partial_success',record_results=result['record_results']+[row]+reserve)
        if len(canonical(proposal))>MAX_BYTES:row=_failure(entry,'limit_exceeded',attempted=attempted)
        result['record_results'].append(row)
        emit('task_result',status=row['status'])
    result['status']=aggregate_status(result['record_results'])
    return validate_teaching_result(request,result,verified_snapshot_digests=trusted)
