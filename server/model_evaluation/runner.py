"""Run fixed cases against existing model adapters in an isolated process."""
import argparse
import hashlib
import json
import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from ddl_text.chat_client import ChatClient
from ddl_text.config import Settings
from ddl_text.contracts import normalize_record, validate_tasks
from ddl_text.extraction import ExtractionAdapter
from ddl_text.media_pipeline import process_media_request
from task_planning import decomposition, planning_harness

MODULES = ('recognition', 'vision', 'decomposition', 'estimation')
FIXTURES = Path(__file__).with_name('fixtures')


def digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def write_json(path, value):
    """A completed JSON file is the resume boundary; partial writes stay private."""
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.receipt-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RecordedClient(ChatClient):
    def __init__(self, key, trace):
        self.trace = trace
        super().__init__(key, call_observer=self.observe)

    def observe(self, event):
        with self.trace.with_suffix('.calls.jsonl').open('a') as stream:
            stream.write(json.dumps(event) + '\n')

    def complete_json(self, instructions, input_json, settings):
        raw = super().complete_json(instructions, input_json, replace(settings, thinking_mode='disabled'))
        with self.trace.with_suffix('.raw.jsonl').open('a') as stream:
            stream.write(json.dumps(raw, ensure_ascii=False) + '\n')
        return raw


def image_path(case, base):
    path = (base / case['image']).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError('image must be inside the case directory')
    return path


def load_cases(path, vision=None):
    cases = json.loads(path.read_text())
    if not isinstance(cases, list):
        raise ValueError('cases must be an array')
    if vision:
        images = json.loads(vision.read_text())
        if not isinstance(images, list) or any(not isinstance(case, dict) for case in images):
            raise ValueError('vision cases must be an array of objects')
        cases += [dict(case, module='vision') for case in images]
    identifiers = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id'] or
                case['id'] in identifiers or case.get('module') not in MODULES):
            raise ValueError('cases require unique IDs and a supported module')
        identifiers.add(case['id'])
        if case['module'] == 'vision':
            image_path(case, path.parent).read_bytes()
        required = 'record' if case['module'] == 'recognition' else 'request'
        if not isinstance(case.get(required), dict):
            raise ValueError(f"{case['id']} requires {required}")
    return cases


def build_manifest(cases, models, base, source_version, endpoint):
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('DDL_ENDPOINT must be an HTTPS URL without credentials, query or fragment')
    source_root = Path(decomposition.__file__).resolve().parents[1]
    sources = {str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
               for package in ('ddl_text', 'task_planning', 'contracts')
               for path in sorted((source_root / package).rglob('*'))
               if path.is_file() and path.suffix in ('.py', '.md', '.json')}
    images = {case['id']: hashlib.sha256(image_path(case, base).read_bytes()).hexdigest()
              for case in cases if case['module'] == 'vision'}
    tools = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in Path(__file__).parent.glob('*.py')}
    manifest = dict(source_version=source_version, source_hash=digest(sources), sources=sources,
                    tool_hashes=tools, cases_hash=digest(cases),
                    case_ids=[case['id'] for case in cases],
                    case_hashes={case['id']: digest(case) for case in cases}, image_hashes=images,
                    models=models, endpoint=endpoint, thinking='disabled', max_tokens=8192,
                    temperature='provider_default', text_vision_timeout=45,
                    decomposition_estimation_timeout=20, credential_location='environment only')
    manifest['run_id'] = digest(manifest)
    return manifest


def run(case, model, out, base, run_id, endpoint):
    identity = digest(dict(case=case, model=model, run_id=run_id))
    target = out / (identity + '.json')
    expected = dict(id=case['id'], module=case['module'], model=model,
                    case_sha256=digest(case), run_id=run_id)
    if target.exists():
        cached = json.loads(target.read_text())
        if (any(cached.get(key) != value for key, value in expected.items()) or
                cached.get('status') not in ('ok', 'error') or
                any(key not in cached for key in ('seconds', 'observations', 'raw_outputs'))):
            raise ValueError('cached receipt is incomplete or has a different identity')
        return cached
    trace = out / identity
    if any(trace.with_suffix(suffix).exists() for suffix in ('.calls.jsonl', '.raw.jsonl')):
        raise ValueError('incomplete call traces exist; archive them before retrying or use a new output directory')
    key = os.environ['QINIU_API_KEY']
    options = dict(key=key, endpoint=endpoint, model=model)
    client = lambda credential: RecordedClient(credential, trace)
    started = time.monotonic()
    record = dict(expected, status='error')
    try:
        settings = Settings(mode='real', model=model, endpoint=endpoint, timeout_seconds=45,
                            max_retries=0, thinking_mode='disabled')
        if case['module'] == 'recognition':
            source = normalize_record(case['record'])
            result = ExtractionAdapter(client(key)).extract(source, settings, 0)
            result = validate_tasks(result, source)
        elif case['module'] == 'vision':
            data = image_path(case, base).read_bytes()

            def loader(ref, **bounds):
                expected_ref = case['request']['records'][0]['attachments'][0]['file_ref']
                if ref != expected_ref or len(data) > bounds['max_bytes']:
                    raise ValueError('invalid fixture file reference or size')
                return data

            result = process_media_request(case['request'], loader, client(key), settings)
        elif case['module'] == 'decomposition':
            with patch.object(decomposition, 'ChatClient', client):
                result = decomposition.generate_decomposition(case['request'], options)
        else:
            with patch.object(planning_harness, 'ChatClient', client):
                result = planning_harness.generate(case['request'], options)
        failed = case['module'] == 'vision' and result['status'] not in ('success', 'no_tasks')
        record.update(status='error' if failed else 'ok', output=result)
    except Exception as error:
        # Keep classification, never serialize an exception containing credentials or a URL.
        record.update(error=type(error).__name__, code=getattr(error, 'code', None))
    record.update(seconds=round(time.monotonic() - started, 3),
                  observations=read_lines(trace.with_suffix('.calls.jsonl')),
                  raw_outputs=read_lines(trace.with_suffix('.raw.jsonl')))
    write_json(target, record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=FIXTURES / 'cases.json')
    parser.add_argument('--vision-cases', type=Path, help='Defaults to fixtures only when --cases is omitted')
    parser.add_argument('--model', action='append', required=True)
    parser.add_argument('--module', action='append', choices=MODULES)
    parser.add_argument('--case-id', action='append')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--source-version', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if len(set(args.model)) != len(args.model):
        parser.error('duplicate model IDs')
    vision = args.vision_cases
    if vision is None and args.cases == FIXTURES / 'cases.json':
        vision = FIXTURES / 'vision-cases.json'
    try:
        cases = load_cases(args.cases, vision)
        if args.case_id and set(args.case_id) - {case['id'] for case in cases}:
            raise ValueError('unknown case ID')
        if args.module:
            cases = [case for case in cases if case['module'] in args.module]
        if args.case_id:
            cases = [case for case in cases if case['id'] in args.case_id]
        if not cases:
            raise ValueError('no selected cases')
        endpoint = os.environ.get('DDL_ENDPOINT', 'https://api.qnaigc.com/v1')
        manifest = build_manifest(cases, args.model, args.cases.parent, args.source_version, endpoint)
        if args.dry_run:
            print(json.dumps(dict(cases=len(cases), scheduled=len(cases) * len(args.model), manifest=manifest)))
            return
        os.umask(0o077)
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / 'manifest.json'
        if path.exists() and json.loads(path.read_text()) != manifest:
            raise ValueError('manifest differs; choose a new output directory')
        # Resume does not require a credential; a new provider call still does.
        if not path.exists():
            write_json(path, manifest)
        for model in args.model:
            for case in cases:
                result = run(case, model, args.out, args.cases.parent, manifest['run_id'], endpoint)
                print(json.dumps({key: result[key] for key in ('id', 'module', 'model', 'status', 'seconds')}), flush=True)
    except (ValueError, KeyError, OSError) as error:
        # A missing key is named, not its value; no client exception reaches this path.
        parser.error(str(error))


if __name__ == '__main__':
    main()
