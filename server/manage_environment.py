"""Explicit environment entry point; never deletes data or copies production secrets."""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from runtime_environment import ENVIRONMENTS, ROOT

DEPLOY = ROOT / 'server' / 'deploy'
PROFILES = DEPLOY / 'environments'
MODEL_KEYS = {'DDL_PROVIDER', 'DDL_MODEL', 'DDL_ENDPOINT', 'DDL_CODEX_BIN', 'QINIU_API_KEY', 'DEEPSEEK_API_KEY'}
COMPOSE_KEYS = {'APP_ORIGIN', 'HTTP_PORT', 'HTTPS_PORT', 'SITE_ADDRESS', 'API_IMAGE', 'WEB_IMAGE', 'COOKIE_SECURE'}
FIELDS = {'environment', 'transport', 'origin', 'listen', 'port', 'api_port', 'https_port', 'site_address',
          'api_image', 'web_image', 'data_volume', 'external_data_volume', 'adopt_existing_data', 'background_recovery'}


def defaults(name):
    public_port = {'development': 1431, 'test': 1531, 'production': 1331}[name]
    return dict(environment=name, transport='http', origin=f'http://127.0.0.1:{public_port}',
                listen='127.0.0.1', port=public_port, api_port=public_port + 1,
                https_port={'development':1443, 'test':1543, 'production':443}[name], site_address='', api_image='', web_image='',
                data_volume=f'niuma-{name}-data', external_data_volume=name == 'production',
                adopt_existing_data=False, background_recovery=name == 'production')


def initialize(name, root=PROFILES):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for filename, content in [('config.json', json.dumps(defaults(name), ensure_ascii=False, indent=2) + '\n'),
                              ('.env.model', (DEPLOY / '.env.model.example').read_text(encoding='utf-8'))]:
        path = folder / filename
        try:
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as file:
                file.write(content)
        except FileExistsError:
            pass
    return folder


def read_config(name, root=PROFILES):
    folder = (root / name).resolve()
    config = json.loads((folder / 'config.json').read_text(encoding='utf-8'))
    if set(config) != FIELDS or config['environment'] != name:
        raise ValueError('Invalid environment configuration or mismatched environment name')
    if config['transport'] not in ('http', 'https'):
        raise ValueError('transport must be http or https')
    for key in ('port', 'api_port', 'https_port'):
        if type(config[key]) is not int or not 1 <= config[key] <= 65535:
            raise ValueError(f'Invalid {key}')
    if config['api_port'] == config['port']:
        raise ValueError('API and web ports must differ')
    if config['listen'] not in ('127.0.0.1', '0.0.0.0'):
        raise ValueError('listen must be 127.0.0.1 or 0.0.0.0')
    origin = urlsplit(config['origin'])
    if (origin.scheme != config['transport'] or not origin.hostname or origin.username or origin.password
            or origin.path or origin.query or origin.fragment or any(c.isspace() for c in config['origin'])):
        raise ValueError('origin must be one complete HTTP(S) origin without credentials or a path')
    if config['transport'] == 'https' and config['site_address'] != origin.netloc:
        raise ValueError('site_address must match the HTTPS origin host and port')
    for key in ('external_data_volume', 'adopt_existing_data', 'background_recovery'):
        if type(config[key]) is not bool:
            raise ValueError(f'{key} must be boolean')
    if not isinstance(config['data_volume'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}', config['data_volume']):
        raise ValueError('Invalid data volume name')
    if name != 'production' and (config['data_volume'] != f'niuma-{name}-data'
                                 or config['external_data_volume'] or config['adopt_existing_data']):
        raise ValueError('Development/test must use their own new volume; production volume adoption is forbidden')
    for key in ('api_image', 'web_image'):
        value = config[key]
        if not isinstance(value, str) or (value and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,250}', value)):
            raise ValueError('Invalid image reference')
    # Only provider keys may enter the model file, never storage or routing overrides.
    model = folder / '.env.model'
    seen = set()
    for line in model.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or key not in MODEL_KEYS or key in seen or '${' in value:
            raise ValueError('Model file must contain unique literal provider settings only')
        seen.add(key)
    # A configuration typo must not point two profiles at the same persistent volume.
    for other in ENVIRONMENTS:
        path = root / other / 'config.json'
        if other != name and path.is_file():
            sibling = json.loads(path.read_text(encoding='utf-8'))
            if sibling.get('data_volume') == config['data_volume']:
                raise ValueError('Two environments reference the same data volume')
    return folder, config


def clean_environment():
    # Shell values outrank Compose env files. Remove inherited app/model/routing
    # settings so a terminal previously used for production cannot override test.
    return {key: value for key, value in os.environ.items()
            if not key.startswith(('NIUMA_', 'COMPOSE_', 'VITE_', 'DDL_', 'WECHAT_'))
            and key not in MODEL_KEYS | COMPOSE_KEYS | {'HARNESS_URL', 'HARNESS_TOKEN', 'TEXT_HARNESS_URL', 'TEXT_HARNESS_TOKEN'}}


def git_revision():
    return subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()


def compose_command(name, folder, config, docker='docker'):
    revision = git_revision()
    mode = 'ip' if config['transport'] == 'http' else 'https'
    values = dict(APP_ORIGIN=config['origin'], HTTP_PORT=f"{config['listen']}:{config['port']}",
                  HTTPS_PORT=f"{config['listen']}:{config['https_port']}", SITE_ADDRESS=config['site_address'],
                  API_IMAGE=config['api_image'] or f'niuma-manager-api:{revision[:12]}',
                  WEB_IMAGE=config['web_image'] or f'niuma-manager-web-{mode}:{revision[:12]}',
                  NIUMA_ENVIRONMENT=name, NIUMA_DATA_VOLUME=config['data_volume'],
                  NIUMA_DATA_VOLUME_EXTERNAL=str(config['external_data_volume']).lower(),
                  NIUMA_MODEL_ENV_FILE=(folder / '.env.model').as_posix(),
                  NIUMA_BACKGROUND_RECOVERY='1' if config['background_recovery'] else '0',
                  NIUMA_ADOPT_EXISTING_DATA='1' if config['adopt_existing_data'] else '0',
                  NIUMA_BUILD_REVISION=revision, COOKIE_SECURE='1' if config['transport'] == 'https' else '0')
    env_file = folder / '.env.compose'
    env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()), encoding='utf-8')
    compose = DEPLOY / ('compose.ip.yaml' if mode == 'ip' else 'compose.yaml')
    return [docker, 'compose', '--project-name', f'niuma-{name}', '--env-file', str(env_file), '-f', str(compose)]


def local_environment(name, folder, config):
    if name == 'production' or config['transport'] != 'http' or config['listen'] != '127.0.0.1':
        raise ValueError('Native development servers are restricted to local development/test; deploy production with Compose')
    values = clean_environment()
    data = ROOT / 'server' / 'data' / name
    values.update(NIUMA_ENVIRONMENT=name, NIUMA_DB=str(data / 'niuma.sqlite3'),
                  NIUMA_MODEL_ENV=str(folder / '.env.model'), NIUMA_HOST='127.0.0.1',
                  NIUMA_PORT=str(config['api_port']), NIUMA_WEB_PORT=str(config['port']),
                  NIUMA_ALLOWED_ORIGINS=config['origin'], NIUMA_COOKIE_SECURE='0',
                  NIUMA_BACKGROUND_RECOVERY='1' if config['background_recovery'] else '0',
                  NIUMA_ENABLE_WECHAT='0', NIUMA_ENABLE_MANUAL_MATERIALS='1', VITE_ENABLE_WECHAT='0',
                  PYTHONUTF8='1', PYTHONUNBUFFERED='1', PYTHON=sys.executable)
    return values


def local_servers(name, folder, config):
    env = local_environment(name, folder, config)
    node, vite = shutil.which('node'), ROOT / 'frontend' / 'node_modules' / 'vite' / 'bin' / 'vite.js'
    if not node or not vite.is_file():
        raise ValueError('Install Node and run npm ci in frontend first')
    for value in (config['port'], config['api_port']):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', value))
    children = []
    try:
        children.append(subprocess.Popen([sys.executable, '-u', str(ROOT / 'server' / 'app.py')], cwd=ROOT, env=env))
        children.append(subprocess.Popen([node, str(vite), '--host', '127.0.0.1'], cwd=ROOT / 'frontend', env=env))
        print(f'{name}: {config["origin"]} (Ctrl+C stops both local services)', flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.2)
        return next(child.returncode for child in children if child.returncode is not None)
    except KeyboardInterrupt:
        return 130
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'check', 'build', 'up', 'stop', 'status', 'api', 'web', 'local'))
    parser.add_argument('environment', choices=(*ENVIRONMENTS, 'all'))
    args = parser.parse_args(argv)
    name = args.environment
    if args.command == 'init':
        for item in ENVIRONMENTS if name == 'all' else [name]:
            print('Prepared (existing files preserved):', initialize(item))
        return 0
    if name == 'all':
        parser.error('all is allowed only with init')
    folder, config = read_config(name)
    if args.command == 'local':
        return local_servers(name, folder, config)
    if args.command == 'check':
        print(json.dumps({'environment': name, 'project': f'niuma-{name}', 'origin': config['origin'],
                          'data_volume': config['data_volume'], 'background_recovery': config['background_recovery'],
                          'scheduled_sync_available': False}, ensure_ascii=False))
        return 0
    if args.command in ('api', 'web'):
        child_env = local_environment(name, folder, config)
        command = [sys.executable, '-u', str(ROOT / 'server' / 'app.py')]
        cwd = ROOT
        if args.command == 'web':
            node = shutil.which('node')
            vite = ROOT / 'frontend' / 'node_modules' / 'vite' / 'bin' / 'vite.js'
            if not node or not vite.is_file():
                raise ValueError('Install Node and run npm ci in frontend first')
            command, cwd = [node, str(vite), '--host', '127.0.0.1'], ROOT / 'frontend'
        return subprocess.call(command, cwd=cwd, env=child_env)
    docker = shutil.which('docker')
    if not docker:
        raise ValueError('Docker with Compose v2 is required for container commands')
    command = compose_command(name, folder, config, docker)
    env = clean_environment()
    subprocess.run(command + ['config', '-q'], cwd=DEPLOY, env=env, check=True)
    if args.command == 'build' and subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=normal'], text=True).strip():
        raise ValueError('Commit source changes before building a versioned deployment image')
    if args.command == 'up':
        if name == 'production' and (not config['api_image'] or not config['web_image']):
            raise ValueError('Production requires explicit API/web images already verified in test; do not use implicit local tags')
        owners = subprocess.check_output([docker, 'ps', '--filter', 'volume=' + config['data_volume'],
                                          '--format', '{{.Label "com.docker.compose.project"}}'], text=True, env=env)
        if any(owner != 'niuma-' + name for owner in owners.splitlines()):
            raise ValueError('Another running deployment owns this data volume; back up and stop it before migrating')
    action = {'build': ['build'], 'up': ['up', '-d', '--no-build', '--force-recreate', 'api', 'web'],
              'stop': ['stop'], 'status': ['ps']}[args.command]
    return subprocess.call(command + action, cwd=DEPLOY, env=env)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Do not print subprocess output, env dictionaries, or model values.
        print('Environment command failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
