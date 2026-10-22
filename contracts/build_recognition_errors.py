"""Generate additive failure fields from the shared catalog; --check detects drift."""
import argparse
import json
from pathlib import Path
from contracts.recognition_errors import schema, diagnostic_schema, CATALOG
from contracts.build_v031_bundle import build, OUTPUT

ROOT = Path(__file__).parent


def generate(check=False):
    paths = [ROOT / name for name in ('schemas/common-v0.3.1.schema.json',
        'schemas/teaching-result-v0.4.schema.json', 'schemas/teaching-workspace-v0.1.schema.json',
        'schemas/text-import-jobs-v0.1.schema.json')]
    for path in paths:
        doc = json.loads(path.read_text(encoding='utf-8'))
        doc.setdefault('$defs', {}).update(recognition_failure=schema(), recognition_diagnostic=diagnostic_schema())
        def visit(node):
            if isinstance(node, dict):
                properties = node.get('properties', {})
                if {'code', 'message', 'retryable'} <= properties.keys():
                    properties['failure'] = {'$ref': '#/$defs/recognition_failure'}
                    properties['diagnostic'] = {'$ref': '#/$defs/recognition_diagnostic'}
                    properties['diagnostic_id'] = {'type': 'string', 'pattern': '^[0-9a-f]{24}$'}
                    properties['diagnostics'] = {'type': 'string', 'maxLength': 65536}
                for value in node.values(): visit(value)
            elif isinstance(node, list):
                for value in node: visit(value)
        visit(doc)
        content = json.dumps(doc, ensure_ascii=False, indent=2) + '\n'
        if check:
            if json.loads(path.read_text(encoding='utf-8')) != doc: raise SystemExit(f'Stale error fields: {path.name}')
        else: path.write_text(content, encoding='utf-8')
    report_path = ROOT / 'schemas/error-report-v0.1.schema.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    codes = report['properties']['code']['enum']
    changed = [key for key in CATALOG if key not in codes]
    if check and changed: raise SystemExit('Stale error-report codes')
    if changed:
        codes.extend(changed)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    content = json.dumps(build(), ensure_ascii=False, indent=2) + '\n'
    if check:
        if OUTPUT.read_text(encoding='utf-8') != content: raise SystemExit('Stale v0.3.1 bundle')
    else: OUTPUT.write_text(content, encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    generate(parser.parse_args().check)
