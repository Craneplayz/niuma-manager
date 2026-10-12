"""Environment identity and storage boundaries shared by local and container runs."""
import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENTS = ('development', 'test', 'production')


def environment():
    value = os.environ.get('NIUMA_ENVIRONMENT', 'development')
    if value not in ENVIRONMENTS:
        raise ValueError('NIUMA_ENVIRONMENT must be development, test or production')
    return value


def managed():
    return 'NIUMA_ENVIRONMENT' in os.environ


def database_path():
    if os.environ.get('NIUMA_DB'):
        return Path(os.environ['NIUMA_DB']).resolve()
    if managed():
        if environment() == 'production':
            raise ValueError('Production requires an explicit NIUMA_DB; preserve the existing volume')
        return ROOT / 'server' / 'data' / environment() / 'niuma.sqlite3'
    # Existing local installs are not silently switched to an empty database.
    return ROOT / 'server' / 'data' / 'niuma.sqlite3'


def model_env_path(root=None):
    root = root or ROOT
    if os.environ.get('NIUMA_MODEL_ENV'):
        return Path(os.environ['NIUMA_MODEL_ENV']).resolve()
    if managed():
        # Containers receive model settings via env_file; never read a sibling
        # checkout's root .env when this environment has no model configuration.
        return root / 'server' / 'data' / environment() / '.env.model'
    return root / '.env'


def cookie_name():
    return 'niuma_session_' + environment() if managed() else 'niuma_session'


def port():
    value = int(os.environ.get('NIUMA_PORT', '1332'))
    if not 1 <= value <= 65535:
        raise ValueError('NIUMA_PORT must be between 1 and 65535')
    return value


def allow_background_recovery():
    return os.environ.get('NIUMA_BACKGROUND_RECOVERY', '1' if not managed() or environment() == 'production' else '0') == '1'


def validate_storage(db_path):
    """Claim a fresh data directory or reject a volume from another environment."""
    if not managed():
        return
    target = Path(db_path).resolve().parent
    target.mkdir(parents=True, exist_ok=True)
    marker = target / '.niuma-environment.json'
    expected = environment()
    try:
        identity = json.loads(marker.read_text(encoding='utf-8'))
    except FileNotFoundError:
        occupied = any(target.iterdir())
        adopting = expected == 'production' and os.environ.get('NIUMA_ADOPT_EXISTING_DATA') == '1'
        if occupied and not adopting:
            raise ValueError('Unlabelled existing data: back up and explicitly adopt in production; use a fresh directory for development/test')
        try:
            with marker.open('x', encoding='utf-8') as file:
                json.dump({'environment': expected}, file)
        except FileExistsError:
            pass
        identity = json.loads(marker.read_text(encoding='utf-8'))
    if not isinstance(identity, dict) or identity.get('environment') != expected:
        raise ValueError('Data directory belongs to another environment; refusing to open the database')


@lru_cache(maxsize=1)
def revision():
    value = os.environ.get('NIUMA_BUILD_REVISION', '')
    if not value:
        try:
            value = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, timeout=2, text=True).strip()
        except (OSError, subprocess.SubprocessError):
            value = 'unknown'
    return value if re.fullmatch(r'[0-9a-f]{7,40}', value) else 'unknown'


def public_version():
    return {'environment': environment(), 'managed': managed(), 'revision': revision(),
            'background_recovery': allow_background_recovery(), 'scheduled_sync_available': False}
