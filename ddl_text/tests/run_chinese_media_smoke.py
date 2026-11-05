"""Opt-in Chinese image checks using generated, non-private task notices."""
import argparse
import hashlib
import io
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.media_pipeline import process_media_request
from ddl_text.tests.media_cases import ROOT, request_for, memory_loader
from ddl_text.tests.run_media_smoke import SyntheticCaptureClient


CASES = {
    'zh-explicit-asap': dict(lines=['合成课程通知 A17', '请于2026.10.08 16:25前提交课程回顾。',
                                  '请大家尽快完成，计入平时成绩。'], deadlines=['2026-10-08T16:25:00+08:00']),
    'zh-yearless': dict(lines=['合成课程作业 B23', '完成第1至5题，10月9日前提交。'], deadlines=['2026-10-09']),
    'zh-multiple': dict(lines=['合成课程任务 C31', '任务一：10月10日18:00前提交实验报告。',
                              '任务二：10月12日前填写课程反馈问卷。'],
                       deadlines=['2026-10-10T18:00:00+08:00', '2026-10-12']),
    'zh-long': dict(lines=['合成课程讲义 D42'] + [f'背景资料 {i}：介绍课程概念，仅供查阅。' for i in range(1, 43)] +
                          ['本周作业通知', '提交一份课程内容回顾，10月13日18:00前完成。'],
                   deadlines=['2026-10-13T18:00:00+08:00']),
    'zh-blurry': dict(lines=['合成作业 E56', '10月14日17:00前提交读书报告。'], deadlines=[], blurry=True),
}


def render_case(case, font_path):
    font = ImageFont.truetype(str(font_path), 28)
    canvas = Image.new('RGB', (1100, max(240, 90 + 48 * len(case['lines']))), 'white')
    draw = ImageDraw.Draw(canvas)
    for index, line in enumerate(case['lines']):
        if draw.textlength(line, font=font) > canvas.width - 80:
            raise ValueError('Synthetic line exceeds canvas; do not clip test content')
        draw.text((40, 35 + 48 * index), line, font=font, fill='#202020')
    if case.get('blurry'):
        canvas = canvas.resize((55, 12)).resize(canvas.size).filter(ImageFilter.GaussianBlur(7))
    output = io.BytesIO()
    canvas.save(output, format='PNG')
    return output.getvalue()


def acceptable(name, result):
    if CASES[name].get('blurry'):
        return (result['status'] == 'failed' and not result['tasks'] and
                any(e['code'] == 'low_content_quality' for e in result['errors']))
    tasks = result['tasks']
    if not (result['status'] == 'success' and len(tasks) == len(CASES[name]['deadlines']) and
            sorted(t['deadline']['value'] or '' for t in tasks) == sorted(CASES[name]['deadlines']) and
            all(t['deadline']['status'] == 'explicit' and t['needs_review'] is True for t in tasks)):
        return False
    return all(t['review_reasons'] == ['图片引文由模型读取，尚未经原文逐字核对，请确认。'] for t in tasks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--live', action='store_true')
    parser.add_argument('--font', type=Path, required=True, help='Local CJK font; never uploaded or copied into the repo')
    parser.add_argument('--images-dir', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--case', choices=list(CASES))
    args = parser.parse_args()
    if args.live and not args.output:
        parser.error('--live requires a new --output path')
    args.images_dir.mkdir(parents=True, exist_ok=True)
    prepared = []
    for name, case in CASES.items():
        if args.case and args.case != name:
            continue
        data = render_case(case, args.font)
        (args.images_dir / (name + '.png')).write_bytes(data)
        prepared.append((name, case, data))
    if args.prepare:
        print(json.dumps(dict(prepared=[x[0] for x in prepared]), ensure_ascii=False))
        return 0
    options = live_options(args.env_file)
    if options['provider'] != 'qiniu' or not options['key'].strip():
        parser.error('Verified Qiniu configuration required; no automatic provider switch')
    settings = Settings(mode='real', model=options['model'], endpoint=options['endpoint'], timeout_seconds=45)
    with args.output.open('x', encoding='utf-8') as output:
        results = []
        for name, case, data in prepared:
            request, _ = request_for(data)
            request['request_id'] = 'synthetic-chinese-' + name
            record = request['records'][0]
            record.update(input_id='synthetic:' + name, context=[], course_or_project='合成测试课程', source_label='合成课程群')
            record['attachments'][0].update(file_name=name + '.png', origin='original',
                original_published_at=dict(value='2026-10-01', precision='date', basis='source'))
            started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix='niuma-chinese-synthetic-') as directory:
                capture = Path(directory) / 'draft.json'
                result = process_media_request(request, memory_loader(data), SyntheticCaptureClient(options['key'], str(capture)), settings)
                raw = capture.read_text(encoding='utf-8') if capture.exists() else None
            passed = acceptable(name, result)
            results.append(dict(case=name, synthetic_text=case['lines'], degraded=case.get('blurry', False),
                request=request, raw_draft=raw, result=result, passed=passed,
                elapsed_seconds=round(time.monotonic() - started, 2), image_sha256=hashlib.sha256(data).hexdigest()))
            output.seek(0); output.truncate()
            json.dump(dict(tested_at=datetime.now(timezone.utc).isoformat(), model=options['model'],
                scope='Generated Chinese images only; no private screenshots, not a recall benchmark.', results=results),
                output, ensure_ascii=False, indent=2)
            output.flush()
            print(json.dumps(dict(case=name, passed=passed, status=result['status'],
                deadlines=[t['deadline']['value'] for t in result['tasks']]), ensure_ascii=False), flush=True)
        return 0 if all(item['passed'] for item in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
