"""Traceable policy identity for the consumer's job metadata, not a new contract."""
import hashlib
import json
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import Settings

ROOT = Path(__file__).resolve().parent
POLICY_FORMAT = 'niuma-text-policy/1'
# Explicit runtime closure: changes in rendering, tests and README do not change
# recognition; changes in any runtime/validation/skill component below do.
COMPONENTS = (
    'policy_version.py', 'config.py', 'adapters.py', 'pipeline.py', 'runner.py',
    'provider.py', 'chat_client.py', 'codex_client.py', 'extraction.py',
    'classification.py', 'dates.py', 'time_validation.py', 'contracts.py',
    'contract_v02.py', 'draft_schema.py',
    'skills/text-task-extraction/SKILL.md',
    'skills/text-task-extraction/references/draft-schema.md',
    'skills/text-task-extraction/references/examples.md',
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _endpoint(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError('invalid_policy_endpoint')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError('policy_endpoint_must_not_contain_credentials')
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    port = parsed.port
    if port and port != (443 if parsed.scheme == 'https' else 80):
        host += ':' + str(port)
    return urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip('/'), '', ''))


def _label(value):
    if not isinstance(value, str) or not 0 < len(value) <= 200 or value != value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError('invalid_policy_label')
    return value


def text_policy_manifest(settings: Settings, *, provider: str, contract_version='0.2', deployment_revision=None):
    """Call at worker startup in an immutable release, using its actual settings.

    Persist this manifest/version with the run in server-owned storage. It does
    not mark old results current or trigger retries, and never reads .env, input
    messages, user IDs, Git, clock or credentials. Restart workers after edits;
    hashing changed source cannot make already imported Python code reload.
    Only built-in real text adapters are supported; media/custom adapters need a
    separately agreed profile rather than falsely reusing this identity.
    """
    if not isinstance(settings, Settings) or settings.mode != 'real' or provider not in ('qiniu', 'codex'):
        raise ValueError('unsupported_policy_execution')
    if contract_version not in ('0.1', '0.2'):
        raise ValueError('unsupported_policy_contract')
    components = {}
    for name in COMPONENTS:
        # Missing rules are a deployment error, never a fallback version. Ignore
        # UTF-8 BOM/CRLF differences across equivalent Windows/Linux checkouts.
        content = (ROOT / name).read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
        components[name] = hashlib.sha256(content.encode('utf-8')).hexdigest()
    try:
        timezone_version = version('tzdata')
    except PackageNotFoundError:
        timezone_version = 'system'
    manifest = dict(format=POLICY_FORMAT, contract_version=contract_version, components=components,
        execution=dict(provider=provider, model=_label(settings.model), endpoint=_endpoint(settings.endpoint),
                       timeout_seconds=float(settings.timeout_seconds), max_retries=settings.max_retries,
                       deployment_revision=_label(deployment_revision) if deployment_revision is not None else None),
        runtime=dict(python=platform.python_version(), system=platform.system(), tzdata=timezone_version))
    digest = hashlib.sha256(_canonical(manifest)).hexdigest()
    return dict(version='text-policy-v1:' + digest, **manifest)
