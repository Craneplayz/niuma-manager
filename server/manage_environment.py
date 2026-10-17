"""Explicit environment entry point; never deletes data or copies production secrets."""
import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from runtime_environment import ENVIRONMENTS, ROOT, source_identity
from release_images import record_acceptance, verified_images
from mail_delivery import MAIL_KEYS, configured as mail_configured
from dev_accounts import AccountError, ERRORS as ACCOUNT_ERRORS, validate_identity, validate_password

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ddl_text.codex_client import load_env

DEPLOY = ROOT / 'server' / 'deploy'
PROFILES = DEPLOY / 'environments'
MODEL_KEYS = {'DDL_PROVIDER', 'DDL_MODEL', 'DDL_ENDPOINT', 'DDL_CODEX_BIN', 'QINIU_API_KEY', 'DEEPSEEK_API_KEY'}
COMPOSE_KEYS = {'APP_ORIGIN', 'HTTP_PORT', 'HTTPS_PORT', 'SITE_ADDRESS', 'API_IMAGE', 'WEB_IMAGE', 'COOKIE_SECURE'}
FIELDS = {'environment', 'transport', 'origin', 'listen', 'port', 'api_port', 'https_port', 'site_address',
          'api_image', 'web_image', 'data_volume', 'external_data_volume', 'adopt_existing_data', 'background_recovery', 'scheduled_sync'}


def defaults(name):
    public_port = {'development': 1431, 'test': 1531, 'production': 1331}[name]
    return dict(environment=name, transport='http', origin=f'http://127.0.0.1:{public_port}',
                listen='127.0.0.1', port=public_port, api_port=public_port + 1,
                https_port={'development':1443, 'test':1543, 'production':443}[name], site_address='', api_image='', web_image='',
                data_volume=f'niuma-{name}-data', external_data_volume=name == 'production',
                adopt_existing_data=False, background_recovery=name == 'production', scheduled_sync=False)


def initialize(name, root=PROFILES):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for filename, content in [('config.json', json.dumps(defaults(name), ensure_ascii=False, indent=2) + '\n'),
                              ('.env.model', (DEPLOY / '.env.model.example').read_text(encoding='utf-8')),
                              ('.env.mail', (DEPLOY / '.env.mail.example').read_text(encoding='utf-8'))]:
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
    if isinstance(config,dict): config.setdefault('scheduled_sync',False)
    if not isinstance(config, dict) or set(config) != FIELDS or config['environment'] != name:
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
    if not isinstance(config['origin'], str) or not re.fullmatch(r'https?://(?:[a-zA-Z0-9.-]+|\[[0-9a-fA-F:]+\])(?::[0-9]+)?', config['origin']):
        raise ValueError('origin must be one HTTP(S) origin with an ASCII host and optional port')
    origin = urlsplit(config['origin'])
    if (origin.scheme != config['transport'] or not origin.hostname or origin.username or origin.password
            or origin.path or origin.query or origin.fragment or any(c.isspace() for c in config['origin'])):
        raise ValueError('origin must be one complete HTTP(S) origin without credentials or a path')
    if config['transport'] == 'https' and config['site_address'] != origin.netloc:
        raise ValueError('site_address must match the HTTPS origin host and port')
    default_port = 443 if origin.scheme == 'https' else 80
    public_port = origin.port if origin.port is not None else default_port
    if public_port == 0:
        raise ValueError('origin port must be between 1 and 65535')
    if public_port != config['https_port' if origin.scheme == 'https' else 'port']:
        raise ValueError('origin port must match the published HTTP or HTTPS port')
    if config['transport'] == 'https' and config['port'] == config['https_port']:
        raise ValueError('HTTP redirect and HTTPS ports must differ')
    # Browsers lowercase hosts and omit :80/:443 in Origin. Match what they send
    # so a valid configured URL does not result in rejected login/write requests.
    host = '[' + origin.hostname + ']' if ':' in origin.hostname else origin.hostname
    authority = host + (':' + str(public_port) if public_port != default_port else '')
    config['origin'] = origin.scheme + '://' + authority
    if config['transport'] == 'https':
        config['site_address'] = authority
    for key in ('external_data_volume', 'adopt_existing_data', 'background_recovery', 'scheduled_sync'):
        if type(config[key]) is not bool:
            raise ValueError(f'{key} must be boolean')
    if not isinstance(config['data_volume'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}', config['data_volume']):
        raise ValueError('Invalid data volume name')
    if name != 'production' and (config['data_volume'] != f'niuma-{name}-data'
                                 or config['external_data_volume'] or config['adopt_existing_data']):
        raise ValueError('Development/test must use their own new volume; production volume adoption is forbidden')
    if name == 'production' and (not config['external_data_volume'] or config['data_volume'] in {'niuma-development-data', 'niuma-test-data'}):
        raise ValueError('Production requires an existing external volume separate from development/test')
    for key in ('api_image', 'web_image'):
        value = config[key]
        if not isinstance(value, str) or (value and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,250}', value)):
            raise ValueError('Invalid image reference')
    read_model_config(folder / '.env.model')
    read_mail_config(folder / '.env.mail')
    # A configuration typo must not point two profiles at the same persistent volume.
    for other in ENVIRONMENTS:
        path = root / other / 'config.json'
        if other != name and path.is_file():
            sibling = json.loads(path.read_text(encoding='utf-8'))
            if sibling.get('data_volume') == config['data_volume']:
                raise ValueError('Two environments reference the same data volume')
    return folder, config


def read_model_config(path):
    # Use exactly the same literal syntax as the native model client. Never let
    # Compose expand a provider value using an unrelated variable in the shell.
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    if any(line.strip() and not line.lstrip().startswith('#')
           and line.partition('=')[0].strip() not in MODEL_KEYS for line in lines):
        raise ValueError('Model file must contain unique literal provider settings only')
    try:
        values = load_env(path)
    except ValueError:
        raise ValueError('Model file must contain unique literal provider settings only') from None
    if any(any(ord(c) < 32 for c in value) for value in values.values()):
        raise ValueError('Model settings must not contain control characters')
    return values


def read_mail_config(path):
    """The same literal dotenv subset, limited to mail keys and no expansion."""
    # Existing profiles without mail configuration keep serving existing accounts.
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or key not in MAIL_KEYS or key in values:
            raise ValueError('Mail file must contain unique literal mail settings only')
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError('Mail file must contain unique literal mail settings only')
            value = value[1:-1]
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('Mail settings must not contain control characters')
        values[key] = value
    return values


def write_compose_env(path, values):
    # Double quotes preserve spaces/#/backslashes; $$ prevents interpolation.
    # Atomically replace a private file, including when a value ends in a slash.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            for key, value in values.items():
                file.write(key + '=' + json.dumps(value, ensure_ascii=False).replace('$', '$$') + '\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
                  NIUMA_MODEL_ENV_FILE=(folder / '.env.model.compose').as_posix(),
                  NIUMA_MAIL_ENV_FILE=(folder / '.env.mail.compose').as_posix(),
                  NIUMA_HTTPS_TARGET_PORT=str(urlsplit(config['origin']).port or 443),
                  NIUMA_BACKGROUND_RECOVERY='1' if config['background_recovery'] else '0',
                  NIUMA_ENABLE_SCHEDULED_SYNC='1' if config.get('scheduled_sync',False) else '0',
                  NIUMA_ADOPT_EXISTING_DATA='1' if config['adopt_existing_data'] else '0',
                  NIUMA_BUILD_REVISION=revision, COOKIE_SECURE='1' if config['transport'] == 'https' else '0')
    env_file = folder / '.env.compose'
    write_compose_env(folder / '.env.model.compose', read_model_config(folder / '.env.model'))
    write_compose_env(folder / '.env.mail.compose', read_mail_config(folder / '.env.mail'))
    write_compose_env(env_file, values)
    compose = DEPLOY / ('compose.ip.yaml' if mode == 'ip' else 'compose.yaml')
    return [docker, 'compose', '--project-name', f'niuma-{name}', '--env-file', str(env_file), '-f', str(compose)]


def local_environment(name, folder, config):
    if name == 'production' or config['transport'] != 'http' or config['listen'] != '127.0.0.1':
        raise ValueError('Native development servers are restricted to local development/test; deploy production with Compose')
    if urlsplit(config['origin']).hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('Native origin must use localhost or 127.0.0.1')
    values = clean_environment()
    values.update(read_mail_config(folder / '.env.mail'))
    revision, dirty = source_identity()
    data = ROOT / 'server' / 'data' / name
    values.update(NIUMA_ENVIRONMENT=name, NIUMA_DB=str(data / 'niuma.sqlite3'),
                  NIUMA_MODEL_ENV=str(folder / '.env.model'), NIUMA_HOST='127.0.0.1',
                  NIUMA_PORT=str(config['api_port']), NIUMA_WEB_PORT=str(config['port']),
                  NIUMA_ALLOWED_ORIGINS=config['origin'], NIUMA_COOKIE_SECURE='0',
                  NIUMA_BACKGROUND_RECOVERY='1' if config['background_recovery'] else '0',
                  NIUMA_ENABLE_SCHEDULED_SYNC='1' if config.get('scheduled_sync',False) else '0',
                  NIUMA_ENABLE_WECHAT='0', NIUMA_ENABLE_MANUAL_MATERIALS='1', VITE_ENABLE_WECHAT='0',
                  NIUMA_BUILD_REVISION=revision, VITE_BUILD_REVISION=revision,
                  NIUMA_SOURCE_DIRTY='unknown' if dirty is None else str(int(dirty)),
                  VITE_SOURCE_DIRTY='unknown' if dirty is None else str(int(dirty)),
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


def create_test_account(name, folder, config, *, email=None, display_name=None,
                        container=False, generate_password=False, password_stdin=False):
    if name not in ('development', 'test'):
        raise AccountError('production_forbidden')
    email, display_name = validate_identity(
        'tester-' + secrets.token_hex(5) + '@niuma.test' if email is None else email,
        display_name if display_name is not None else '测试账号')
    if generate_password:
        password = secrets.token_urlsafe(24)
    elif password_stdin:
        # One bounded line; password contents never become a command argument.
        password = sys.stdin.readline(131).removesuffix('\n').removesuffix('\r')
    else:
        if not sys.stdin.isatty():
            raise ValueError('Use --generate-password or --password-stdin in non-interactive sessions')
        password = getpass.getpass('测试账号密码（至少 8 位，不显示输入）: ')
        if password != getpass.getpass('再次输入密码: '):
            raise ValueError('Passwords do not match; no account was created')
    validate_password(password)
    payload = json.dumps(dict(email=email, name=display_name, password=password), ensure_ascii=False)
    if container:
        docker = shutil.which('docker')
        if not docker:
            raise ValueError('Docker with Compose v2 is required for --container')
        env, cwd = clean_environment(), DEPLOY
        command = compose_command(name, folder, config, docker) + [
            'exec', '-T', 'api', 'python', 'server/dev_accounts.py', name]
    else:
        env, cwd = local_environment(name, folder, config), ROOT
        # This operation does not need mail/model credentials or background jobs.
        for key in MAIL_KEYS | MODEL_KEYS:
            env.pop(key, None)
        env.update(NIUMA_BACKGROUND_RECOVERY='0', NIUMA_ENABLE_SCHEDULED_SYNC='0')
        command = [sys.executable, str(ROOT / 'server' / 'dev_accounts.py'), name]
    result = subprocess.run(command, input=payload, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env, timeout=30)
    try:
        user = json.loads(result.stdout)
    except ValueError:
        raise AccountError('create_failed') from None
    if result.returncode:
        code = user.get('error') if isinstance(user, dict) else None
        raise AccountError(code if isinstance(code, str) and code in ACCOUNT_ERRORS else 'create_failed')
    if (not isinstance(user, dict) or set(user) != {'id', 'email', 'name', 'emailVerified'}
            or not isinstance(user['id'], str) or user['email'] != email
            or user['name'] != display_name or user['emailVerified'] is not True):
        raise AccountError('create_failed')
    output = dict(environment=name, storage='container' if container else 'native', user=user)
    if generate_password:
        # Explicit opt-in: show a newly generated password only after creation.
        # It is not persisted to a credentials file or printed on failure.
        output['password'] = password
    print(json.dumps(output, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'check', 'build', 'up', 'stop', 'status', 'api', 'web', 'local', 'accept', 'test-account'))
    parser.add_argument('environment', choices=(*ENVIRONMENTS, 'all'))
    parser.add_argument('--evidence', help='Acceptance result/link; required for accept test')
    parser.add_argument('--acceptance', type=Path, help='Saved acceptance record for up production (including rollback)')
    parser.add_argument('--email', help='Reserved .test address for test-account; random when omitted')
    parser.add_argument('--name', help='Display name for test-account')
    parser.add_argument('--container', action='store_true', help='Create test-account in the running environment API container')
    passwords = parser.add_mutually_exclusive_group()
    passwords.add_argument('--generate-password', action='store_true', help='Generate and display a new test-account password once')
    passwords.add_argument('--password-stdin', action='store_true', help='Read the test-account password from one stdin line')
    args = parser.parse_args(argv)
    name = args.environment
    if args.command != 'test-account' and (args.email is not None or args.name is not None
            or args.container or args.generate_password or args.password_stdin):
        parser.error('Account options are only valid with test-account')
    if args.command == 'test-account' and name not in ('development', 'test'):
        parser.error('test-account is allowed only in development/test; production is disabled')
    if args.evidence is not None and args.command != 'accept':
        parser.error('--evidence is only valid with accept test')
    if args.acceptance is not None and (args.command, name) != ('up', 'production'):
        parser.error('--acceptance is only valid with up production')
    if args.command == 'accept' and (name != 'test' or not args.evidence or not args.evidence.strip()):
        parser.error('Use accept test --evidence <acceptance result or link>')
    if args.command == 'init':
        for item in ENVIRONMENTS if name == 'all' else [name]:
            print('Prepared (existing files preserved):', initialize(item))
        return 0
    if name == 'all':
        parser.error('all is allowed only with init')
    if args.command == 'build' and name == 'production':
        raise ValueError('Build in development/test, then promote the same verified images to production')
    if args.command in ('stop', 'status'):
        return existing_project(args.command, name)
    folder, config = read_config(name)
    if args.command == 'test-account':
        return create_test_account(name, folder, config, email=args.email, display_name=args.name,
                                   container=args.container, generate_password=args.generate_password,
                                   password_stdin=args.password_stdin)
    if args.command == 'local':
        return local_servers(name, folder, config)
    if args.command == 'check':
        print(json.dumps({'environment': name, 'project': f'niuma-{name}', 'origin': config['origin'],
                          'data_volume': config['data_volume'], 'background_recovery': config['background_recovery'],
                          'mail_configured': mail_configured(read_mail_config(folder / '.env.mail')),
                          'scheduled_sync_available': config.get('scheduled_sync',False)}, ensure_ascii=False))
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
    if args.command == 'build' and (config['api_image'] or config['web_image']):
        raise ValueError('Clear explicit image references before building; never overwrite a release tag')
    docker = shutil.which('docker')
    if not docker:
        raise ValueError('Docker with Compose v2 is required for container commands')
    env = clean_environment()
    if args.command == 'accept':
        path = folder / 'accepted-images.json'
        record_acceptance(docker, env, config, path, args.evidence)
        print('Recorded verified test images:', path)
        return 0
    if args.command == 'up' and name == 'production':
        acceptance = args.acceptance or PROFILES / 'test' / 'accepted-images.json'
        config = dict(config, **verified_images(docker, env, config, acceptance))
    command = compose_command(name, folder, config, docker)
    subprocess.run(command + ['config', '-q'], cwd=DEPLOY, env=env, check=True)
    if args.command == 'build' and subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=normal'], text=True).strip():
        raise ValueError('Commit source changes before building a versioned deployment image')
    if args.command == 'up':
        owners = subprocess.check_output([docker, 'ps', '--filter', 'volume=' + config['data_volume'],
                                          '--format', '{{.Label "com.docker.compose.project"}}'], text=True, env=env)
        if any(owner != 'niuma-' + name for owner in owners.splitlines()):
            raise ValueError('Another running deployment owns this data volume; back up and stop it before migrating')
    action = {'build': ['build'], 'up': ['up', '-d', '--no-build', '--pull', 'never', '--force-recreate', 'api', 'web']}[args.command]
    return subprocess.call(command + action, cwd=DEPLOY, env=env)


def existing_project(action, name):
    # A broken/missing config must never prevent stopping an existing project.
    docker = shutil.which('docker')
    if not docker:
        raise ValueError('Docker is required for container commands')
    env = clean_environment()
    command = [docker, 'ps', '--filter', 'label=com.docker.compose.project=niuma-' + name]
    if action == 'status':
        return subprocess.call(command + ['--all'], env=env)
    ids = subprocess.check_output(command + ['--quiet'], text=True, env=env).splitlines()
    if any(not re.fullmatch(r'[a-f0-9]{12,64}', value) for value in ids):
        raise ValueError('Unexpected container identity; refusing to stop')
    return subprocess.call([docker, 'stop', *ids], env=env) if ids else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Do not print subprocess output, env dictionaries, or model values.
        print('Environment command failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
