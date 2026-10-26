"""Execution identity for the opt-in teaching pipeline; never a v0.2 identity."""
import hashlib
from importlib.metadata import version
from pathlib import Path

from .policy_version import text_policy_manifest, _canonical

ROOT=Path(__file__).resolve().parents[1]
EXTRA_COMPONENTS=(
    'ddl_text/teaching_attachment_reader.py', 'ddl_text/image_reader.py', 'ddl_text/pdf_reader.py',
    'ddl_text/docx_reader.py', 'ddl_text/media_worker.py', 'server/teaching_assets.py',
    'ddl_text/skills/teaching-file-reading/SKILL.md',
    'ddl_text/teaching_policy.py','ddl_text/teaching_pipeline.py','ddl_text/teaching_draft.py',
    'ddl_text/teaching_semantics.py',
    'ddl_text/skills/teaching-task-extraction/SKILL.md',
    'contracts/teaching_io.py','contracts/teaching_review_draft.py',
    'contracts/schemas/teaching-review-pair-v0.4-draft.schema.json',
    'contracts/schemas/teaching-request-v0.4.schema.json','contracts/schemas/teaching-result-v0.4.schema.json',
    'contracts/check_v031_examples.py','contracts/check_v03_examples.py',
)


def _components():
    from .policy_version import COMPONENTS
    paths=tuple('ddl_text/'+p for p in COMPONENTS)+EXTRA_COMPONENTS
    return {p:hashlib.sha256((ROOT/p).read_text(encoding='utf-8-sig').replace('\r\n','\n').replace('\r','\n').encode()).hexdigest()
            for p in paths}


# This module is imported in an immutable deployment, before any teaching job.
LOADED_COMPONENTS=_components()


def teaching_policy_manifest(settings):
    if settings.max_retries!=0:raise ValueError('teaching retries belong to caller')
    base=text_policy_manifest(settings,provider='qiniu')
    components=_components()
    if components!=LOADED_COMPONENTS:raise ValueError('teaching_policy_changed_restart_required')
    runtime=dict(base['runtime'],jsonschema=version('jsonschema'),rfc3339_validator=version('rfc3339-validator'))
    value=dict(format='niuma-teaching-policy/1',contract_version='0.4',components=components,
               execution=base['execution'],runtime=runtime)
    return dict(version='teaching-policy-v1:'+hashlib.sha256(_canonical(value)).hexdigest(),**value)
