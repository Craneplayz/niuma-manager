"""Explicit opt-in paid calls with generated non-private images only."""
import argparse
import copy
import hashlib
import json
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ddl_text.chat_client import ChatClient
from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.media_pipeline import process_media_request
from ddl_text.tests.media_cases import ROOT, image_bytes, memory_loader, request_for


CASES = [
    ('exact-and-asap', 'Task M7P2\nSubmit the circuit review by 2026.10.08 16:25.\nPlease finish as soon as possible.',
     '2026-10-08T16:25:00+08:00', 'datetime'),
    ('yearless-date', 'Homework N8Q3\nComplete questions 1-5.\nDue October 9.', '2026-10-09', 'date'),
    ('no-deadline', 'Homework P3R5\nComplete exercises 1-3.\nNo deadline has been specified.', None, 'unknown'),
    ('no-action', 'Reference information\nThe library opened in 2001.\nThis is historical background only.', None, None),
    ('two-images', ['Homework R9T1\nSubmit one experiment summary.\nRequirements: review experiments 1 and 2.',
                    'Deadline for Homework R9T1\nSubmit the experiment summary by October 10, 2026 at 18:00.'],
     '2026-10-10T18:00:00+08:00', 'datetime'),
]


class SyntheticCaptureClient(ChatClient):
    """Capture only this script's generated-data replies, never provider headers."""
    def __init__(self, key, path):
        super().__init__(key)
        self.path = path

    def complete_json(self, instructions, parts, settings):
        raw = super().complete_json(instructions, parts, settings)
        Path(self.path).write_text(raw, encoding='utf-8')
        return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=[c[0] for c in CASES])
    args = parser.parse_args()
    options = live_options(args.env_file)
    if options['provider'] != 'qiniu' or not options['key'].strip():
        parser.error('Configure the verified Qiniu provider and key; no automatic provider switch.')
    # Reserve the destination before any billed request; no overwriting earlier evidence.
    with args.output.open('x', encoding='utf-8') as output:
        settings = Settings(mode='real', model=options['model'], endpoint=options['endpoint'], timeout_seconds=45)
        results = []
        for name, text, deadline, precision in CASES:
            if args.case and args.case != name:
                continue
            texts = text if isinstance(text, list) else [text]
            data = image_bytes(size=(1400, 230), text=texts[0])
            request, _ = request_for(data)
            request['request_id'] = 'synthetic-media-smoke-' + name
            record = request['records'][0]
            record.update(input_id='synthetic:media:' + name, context=[], course_or_project='Synthetic circuits',
                          source_label='Synthetic course group')
            asset = record['attachments'][0]
            asset.update(file_name=name + '.png', origin='original',
                         original_published_at=dict(value='2026-10-01T20:00:00+08:00', precision='datetime', basis='source'))
            files = {asset['file_ref']: data}
            for index, extra_text in enumerate(texts[1:], 2):
                extra_data = image_bytes(size=(1400, 230), text=extra_text)
                extra = copy.deepcopy(asset)
                extra.update(asset_id=f'asset-{index:03}', file_ref=f'asset:synthetic_asset_{index:03}_001',
                             file_name=f'{name}-{index}.png', size_bytes=len(extra_data), sha256=hashlib.sha256(extra_data).hexdigest())
                record['attachments'].append(extra); files[extra['file_ref']] = extra_data
            def loader(ref, *, max_bytes, timeout_seconds):
                if ref not in files:
                    raise FileNotFoundError()
                return files[ref]
            started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix='niuma-synthetic-image-') as folder:
                capture = Path(folder) / 'draft.json'
                result = process_media_request(request, loader, SyntheticCaptureClient(options['key'], str(capture)), settings)
                raw_draft = capture.read_text(encoding='utf-8') if capture.exists() else None
            tasks = result['tasks']
            passed = (result['status'] == 'no_tasks' and not tasks) if precision is None else (
                result['status'] == 'success' and len(tasks) == 1 and
                tasks[0]['deadline']['value'] == deadline and tasks[0]['deadline']['precision'] == precision and
                tasks[0]['needs_review'] is True and
                (precision != 'unknown' or tasks[0]['deadline']['status'] == 'not_provided'))
            results.append(dict(case=name, synthetic_image_text=text, image_sha256=hashlib.sha256(data).hexdigest(),
                                request=request, raw_draft=raw_draft, result=result,
                                elapsed_seconds=round(time.monotonic()-started, 2), passed=passed))
            output.seek(0); output.truncate()
            json.dump(dict(tested_at=datetime.now(timezone.utc).isoformat(), model=options['model'],
                           scope='Generated PNG smoke tests; not private data, recall evaluation or UI integration.',
                           results=results), output, ensure_ascii=False, indent=2)
            output.flush()
            print(json.dumps(dict(case=name, passed=passed, status=result['status'],
                                  deadlines=[t['deadline']['value'] for t in tasks]), ensure_ascii=False), flush=True)
        return 0 if all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
