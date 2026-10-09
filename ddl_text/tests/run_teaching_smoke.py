"""Opt-in paid preview using only fixed, non-private teaching notices."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from ddl_text.adapters import AgentError
from ddl_text.chat_client import ChatClient
from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.teaching_draft import compile_teaching_draft, teaching_instructions, validate_teaching_record
from ddl_text.tests.teaching_cases import live_cases, matches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', choices=[c['name'] for c in live_cases()])
    args = parser.parse_args()
    if not args.live or args.output.exists(): parser.error('require --live and a new output path')
    options = live_options(args.env_file)
    if options['provider'] != 'qiniu': parser.error('preview currently supports configured Qiniu transport only')
    client = ChatClient(options['key'])
    settings = Settings(mode='real', model=options['model'], endpoint=options['endpoint'], timeout_seconds=45, max_retries=0)
    instructions = teaching_instructions()
    report = dict(synthetic_only=True, scope='Internal preview; no HTTP integration or recall evaluation',
        model=options['model'], instructions_sha256=hashlib.sha256(instructions.encode()).hexdigest(), cases=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output: json.dump(report, output, ensure_ascii=False, indent=2)
    for case in live_cases():
        if args.cases and case['name'] not in args.cases: continue
        row = dict(case, passed=False)
        started = time.monotonic()
        try:
            validate_teaching_record(case['record'])
            row_instructions = teaching_instructions(case['record'])
            row['instructions_sha256'] = hashlib.sha256(row_instructions.encode()).hexdigest()
            row['model_draft'] = client.complete_json(row_instructions, json.dumps(dict(record=case['record'],
                source_verified_by_caller=case['source_verified']), ensure_ascii=False), settings)
            row['decisions'] = compile_teaching_draft(row['model_draft'], case['record'], source_verified=case['source_verified'])
            row['passed'] = matches(case, row['decisions'])
        except AgentError as error:
            row['error_code'] = error.code
        row['elapsed_seconds'] = round(time.monotonic() - started, 2)
        report['cases'].append(row)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({k: row[k] for k in ('name', 'passed', 'elapsed_seconds')}), flush=True)
    return 0 if report['cases'] and all(row['passed'] for row in report['cases']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
