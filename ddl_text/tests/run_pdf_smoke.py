"""Explicit paid PDF tests using generated pages only; no personal documents."""
import argparse
import hashlib
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.media_pipeline import MediaLimits, process_media_request
from ddl_text.pdf_reader import PdfLimits
from ddl_text.tests.media_cases import ROOT, memory_loader
from ddl_text.tests.pdf_cases import pdf_bytes, pdf_request
from ddl_text.tests.run_chinese_media_smoke import render_case
from ddl_text.tests.run_media_smoke import SyntheticCaptureClient

CASES = {
    'pdf-text': dict(pages=['Synthetic assignment PDF-T1\nSubmit the experiment review by October 15, 2026 at 17:00.\nPlease finish as soon as possible.'],
                     deadline='2026-10-15T17:00:00+08:00', deadline_page=1),
    'pdf-zh-cross-page': dict(pages=[['合成作业 PDF-C2', '请完成实验1、2的回顾并提交实验报告。', '提交期限见下一页。'],
                                    ['合成作业 PDF-C2（续页）', '本实验报告请于10月16日18:00前提交。', '请大家尽快完成。']],
                              deadline='2026-10-16T18:00:00+08:00', deadline_page=2),
    'pdf-zh-page-limit': dict(pages=[['合成作业 PDF-P3', '请于10月17日18:00前提交读书报告。'],
                                    ['本页仅介绍课程背景，无行动要求。'],
                                    ['本页有另一项作业：10月20日前提交问卷。']],
                             deadline='2026-10-17T18:00:00+08:00', deadline_page=1, max_pages=1),
}


def acceptable(case, result):
    partial = 'max_pages' in case
    if result['status'] != ('partial_success' if partial else 'success') or len(result['tasks']) != 1:
        return False
    task = result['tasks'][0]
    if (task['deadline']['value'] != case['deadline'] or task['deadline']['status'] != 'explicit'
            or not task['needs_review']):
        return False
    deadlines = [e for e in task['evidence'] if 'deadline' in e['supports']]
    if not deadlines or any(e.get('locator', {}).get('page') != case['deadline_page'] for e in deadlines):
        return False
    if partial:
        return (not result['record_results'][0]['coverage']['complete'] and
                any(e['code'] == 'limit_exceeded' for e in result['errors']))
    return task['review_reasons'] == ['图片引文由模型读取，尚未经原文逐字核对，请确认。']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true'); mode.add_argument('--live', action='store_true')
    parser.add_argument('--font', type=Path, required=True)
    parser.add_argument('--pdf-dir', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--case', choices=list(CASES))
    args = parser.parse_args()
    if args.live and not args.output:
        parser.error('--live requires a new output file')
    args.pdf_dir.mkdir(parents=True, exist_ok=True)
    prepared = []
    for name, case in CASES.items():
        if args.case and args.case != name:
            continue
        pages = [render_case(dict(lines=page), args.font) if isinstance(page, list) else page for page in case['pages']]
        data = pdf_bytes(pages)
        (args.pdf_dir / (name + '.pdf')).write_bytes(data)
        prepared.append((name, case, data))
    if args.prepare:
        print(f'Prepared {len(prepared)} synthetic PDFs')
        return 0
    options = live_options(args.env_file)
    if options['provider'] != 'qiniu' or not options['key'].strip():
        parser.error('Verified Qiniu configuration required')
    settings = Settings(mode='real', model=options['model'], endpoint=options['endpoint'], timeout_seconds=45)
    results = []
    with args.output.open('x', encoding='utf-8') as output:
        for name, case, data in prepared:
            request = pdf_request(data)
            request['request_id'] = 'synthetic-' + name
            record = request['records'][0]
            record.update(context=[], input_id='synthetic:' + name, course_or_project='合成测试课程')
            record['attachments'][0].update(file_name=name + '.pdf', origin='original',
                original_published_at=dict(value='2026-10-01', precision='date', basis='source'))
            started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix='niuma-pdf-synthetic-') as folder:
                capture = Path(folder) / 'draft.json'
                result = process_media_request(request, memory_loader(data), SyntheticCaptureClient(options['key'], str(capture)),
                    settings, MediaLimits(pdf=PdfLimits(max_pages=case.get('max_pages', 6))))
                raw = capture.read_text(encoding='utf-8') if capture.exists() else None
            passed = acceptable(case, result)
            results.append(dict(case=name, synthetic_pages=case['pages'], expected=case, pdf_sha256=hashlib.sha256(data).hexdigest(),
                request=request, raw_draft=raw, result=result, elapsed_seconds=round(time.monotonic()-started, 2), passed=passed))
            output.seek(0); output.truncate()
            json.dump(dict(tested_at=datetime.now(timezone.utc).isoformat(), model=options['model'],
                scope='Generated PDF pages only; not private documents or WeChat integration.', results=results), output, ensure_ascii=False, indent=2)
            output.flush()
            print(json.dumps(dict(case=name, passed=passed, status=result['status'],
                deadlines=[t['deadline']['value'] for t in result['tasks']]), ensure_ascii=False), flush=True)
    return 0 if all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
