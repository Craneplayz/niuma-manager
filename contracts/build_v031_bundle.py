"""Build one self-contained v0.3.1 Schema; --check detects stale output offline."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'schemas/bundle-v0.3.1.schema.json'
ENTRIES = {'request': 'request', 'result': 'result', **{
    name: 'wechat-import__' + name for name in ('asset_ack', 'ack', 'job', 'error', 'retry')}}


def build():
    sources = {doc['$id']: doc for path in ROOT.rglob('*v0.3.1.schema.json')
               if path != OUTPUT for doc in [json.loads(path.read_text(encoding='utf-8'))]}
    definitions = {}

    def include(uri, owner):
        base, _, fragment = uri.partition('#')
        base = base or owner
        node = sources[base]
        key = base.split(':')[-2]
        if fragment:
            assert fragment.startswith('/$defs/')
            for part in fragment[1:].split('/'):
                node = node[part.replace('~1', '/').replace('~0', '~')]
            key += '__' + fragment.split('/')[-1]
        if key not in definitions:
            definitions[key] = {}  # Break recursive schema references.
            definitions[key] = rewrite(node, base)
        return '#/$defs/' + key

    def rewrite(value, owner):
        if isinstance(value, list): return [rewrite(v, owner) for v in value]
        if not isinstance(value, dict): return value
        return {key: include(v, owner) if key == '$ref' else rewrite(v, owner)
                for key, v in value.items() if key not in ('$id', '$schema', '$defs')}

    for name in ENTRIES:
        kind = name if name in ('request', 'result') else 'wechat-import'
        fragment = '' if kind != 'wechat-import' else '#/$defs/' + name
        include('urn:niuma:contracts:' + kind + ':0.3.1' + fragment, '')
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            '$id': 'urn:niuma:contracts:bundle:0.3.1',
            'title': 'v0.3.1 envelopes; select an entry for endpoint-specific validation',
            'oneOf': [{'$ref': '#/$defs/' + key} for key in ENTRIES.values()], '$defs': definitions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    content = json.dumps(build(), ensure_ascii=False, indent=2) + '\n'
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding='utf-8') != content:
            raise SystemExit('Bundle is stale: run python contracts/build_v031_bundle.py')
        print('PASS: self-contained v0.3.1 bundle matches source schemas.')
    else:
        OUTPUT.write_text(content, encoding='utf-8')


if __name__ == '__main__': main()
