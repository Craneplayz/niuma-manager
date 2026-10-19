"""Opt-in, read-only replay of one saved test email through the current Qiniu model.

Run from the repo root: python3 -m server.diagnose_email_response --live
--request-id <saved-request-id> --output /tmp/email-response-diagnostic.json
No IMAP, recognition persistence, task changes, raw responses, or body logging.
"""
import argparse
from contextlib import closing
from dataclasses import replace
import json
import linecache
import unicodedata
import os
from pathlib import Path
import sqlite3
import time
from unittest.mock import patch

from ddl_text.adapters import AgentError
from ddl_text.chat_client import ChatClient
from ddl_text.codex_client import ENV_KEYS, live_options
from ddl_text.config import email_model_settings, EMAIL_RECOGNITION_TIMEOUT
from ddl_text.contracts import check_request, normalize_record
from ddl_text.extraction import ExtractionAdapter

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = ROOT / 'server/data/test/niuma.sqlite3'
TEST_MODEL = ROOT / 'server/deploy/environments/test/.env.model'


def load_record(database, request_id):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT request FROM recognition_batches WHERE request_id=?', (request_id,)).fetchall()
    if len(rows) != 1:
        raise ValueError('request_not_unique_or_missing')
    request = json.loads(rows[0][0])
    records = check_request(request)
    if len(records) != 1 or records[0].get('platform') != 'email':
        raise ValueError('requires_one_saved_email')
    return normalize_record(records[0], request['contract_version'])


def test_options(path):
    # Never inherit a shell credential, model, or endpoint from another environment.
    with patch.dict(os.environ, {k:v for k,v in os.environ.items() if k not in ENV_KEYS}, clear=True):
        options = live_options(path)
    if options['provider'] != 'qiniu' or not options['key'].strip():
        raise ValueError('configured_test_qiniu_required')
    return options


def validation_detail(error):
    """Inspect trusted compiler frames, returning only fixed labels and booleans.

    The original response and source remain in memory; never serialize locals,
    arbitrary exception text, quotes, identifiers, or provider output.
    """
    from ddl_text import extraction
    detail = {}
    context = error.__context__
    while context is not None:
        tb = context.__traceback__
        while tb:
            frame = tb.tb_frame
            if Path(frame.f_code.co_filename).resolve() == Path(extraction.__file__).resolve():
                if frame.f_code.co_name == 'compile_drafts':
                    line = linecache.getline(frame.f_code.co_filename,tb.tb_lineno)
                    detail['compiler_line'] = tb.tb_lineno
                    for marker,field in [('course_evidence','course'),('target_evidence','target'),('time_evidence','times'),('evidence.extend','relevance'),('evidence = [','evidence'),('prerequisites','prerequisites')]:
                        if marker in line:
                            detail['field'] = field
                            break
                if frame.f_code.co_name == '_reference':
                    ref = frame.f_locals.get('ref'); available = frame.f_locals.get('available',{})
                    extra = frame.f_locals.get('extra',())
                    if not isinstance(ref,dict) or set(ref) != {'input_id','quote'} | set(extra):
                        detail['check'] = 'reference_shape'
                    elif not isinstance(ref['input_id'],str) or ref['input_id'] not in available:
                        detail['check'] = 'unknown_source_id'
                    elif not isinstance(ref['quote'],str) or not ref['quote'].strip():
                        detail['check'] = 'empty_quote'
                    else:
                        quote,source = ref['quote'],available[ref['input_id']]['text']
                        detail.update(check='quote_not_verbatim',quote_chars=len(quote),
                            matches_after_whitespace_normalization=' '.join(quote.split()) in ' '.join(source.split()),
                            matches_after_unicode_normalization=unicodedata.normalize('NFKC',quote) in unicodedata.normalize('NFKC',source),
                            contains_ellipsis=('…' in quote or '...' in quote))
            tb = tb.tb_next
        context = context.__context__
    if detail and 'check' not in detail:
        detail['check'] = 'compiler_constraint'
    return detail


def diagnostic_settings(options, timeout_seconds=EMAIL_RECOGNITION_TIMEOUT):
    # Per-replay overrides do not change the normal email execution budget.
    return replace(email_model_settings(options), timeout_seconds=timeout_seconds)


def replay(record, client, settings):
    started = time.monotonic()
    report = {'scope': 'saved_test_email_replay', 'writes_application_data': False,
              'imap_access': False, 'max_attempts': 1,
              'thinking_mode': settings.thinking_mode or 'provider_default',
              'timeout_seconds': settings.timeout_seconds}
    try:
        tasks, metrics = ExtractionAdapter(client).extract_with_metrics(record, settings, 0)
        report.update(status='success' if tasks else 'no_tasks', task_count=len(tasks))
    except AgentError as error:
        metrics = getattr(error, 'telemetry', {})
        report.update(status='failed', error_code=error.code,
                      diagnostic=error.diagnostic, retryable=error.retryable)
        detail = validation_detail(error)
        if detail: report['validation_detail'] = detail
    report['timings_ms'] = {k:round(metrics[k]) for k in ('generation_ms','validation_ms') if k in metrics}
    report['validation_reached'] = 'validation_ms' in metrics
    if metrics.get('reason'):
        report['reason'] = metrics['reason']
    report['elapsed_ms'] = round((time.monotonic() - started) * 1000)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='send this saved email to the configured test model once')
    parser.add_argument('--request-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--timeout-seconds', type=float, default=EMAIL_RECOGNITION_TIMEOUT, help='diagnostic wait budget (default 270), greater than 0 and at most 300 seconds')
    args = parser.parse_args()
    if not 0 < args.timeout_seconds <= 300:
        parser.error('--timeout-seconds must be greater than 0 and at most 300')
    if not args.live:
        parser.error('--live is required; this sends one saved email body to the test model')
    # Reserve a new private report before any paid request. Never overwrite output.
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        report = {'status':'setup_failed'}
        responses = []
        try:
            record = load_record(TEST_DB, args.request_id)
            options = test_options(TEST_MODEL)
            settings = diagnostic_settings(options, args.timeout_seconds)
            report = replay(record, ChatClient(options['key'], response_observer=responses.append), settings)
            report['provider_response'] = responses
        except Exception:
            # No exception text or traceback: either can contain private input.
            report = {'status':'diagnostic_failed','provider_response':responses}
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write('\n')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] in ('success','no_tasks') else 1


if __name__ == '__main__':
    raise SystemExit(main())
