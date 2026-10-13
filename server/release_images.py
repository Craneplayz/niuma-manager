"""Bind production startup to the exact pair of images accepted in test."""
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone


def _query(docker, env, *args):
    return subprocess.check_output([docker, *args], env=env, text=True, timeout=30).strip()


def _digest(value):
    return isinstance(value, str) and bool(re.fullmatch(r'sha256:[0-9a-f]{64}', value))


def _image(docker, env, ref):
    # Read selected metadata only; never serialize container environments/secrets.
    image = json.loads(_query(docker, env, 'image', 'inspect', '--format',
                             '{"id":{{json .Id}},"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}', ref))
    if not _digest(image.get('id')) or not re.fullmatch(r'[0-9a-f]{40}', image.get('revision') or ''):
        raise ValueError('Image must have an immutable ID and a full source revision label')
    return image


def _running_pair(docker, env, transport):
    pair = {}
    for service in ('api', 'web'):
        ids = _query(docker, env, 'ps', '--filter', 'label=com.docker.compose.project=niuma-test',
                     '--filter', 'label=com.docker.compose.service=' + service, '--quiet').splitlines()
        if len(ids) != 1 or not re.fullmatch(r'[0-9a-f]{12,64}', ids[0]):
            raise ValueError('Acceptance requires exactly one running test API and web container')
        identity = json.loads(_query(docker, env, 'inspect', '--format',
                              '{"image":{{json .Image}},"state":{{json .State}},"transport":{{json (index .Config.Labels "niuma.transport")}}}', ids[0]))
        state = identity['state']
        if (not state.get('Running') or state.get('Health', {}).get('Status', 'missing' if service == 'api' else 'healthy') != 'healthy'
                or identity['transport'] != transport):
            raise ValueError('Test containers must be running and healthy before acceptance')
        pair[service] = _image(docker, env, identity['image'])
    if pair['api']['revision'] != pair['web']['revision']:
        raise ValueError('Test frontend and API have different source revisions')
    return pair


def record_acceptance(docker, env, config, path, evidence):
    if not isinstance(evidence, str) or not 1 <= len(evidence.strip()) <= 2000:
        raise ValueError('Provide a nonempty acceptance result/link (at most 2000 characters; no secrets)')
    pair = _running_pair(docker, env, config['transport'])
    # Check the actual running pair, not merely the configured image tags.
    # Changing test configuration without deploying it must not certify that change.
    for service in ('api', 'web'):
        ref = config[service + '_image']
        if ref and _image(docker, env, ref) != pair[service]:
            raise ValueError('Configured test image differs from the running image')
    record = dict(version=1, environment='test', transport=config['transport'], images=pair,
                  accepted_at=datetime.now(timezone.utc).isoformat(), evidence=evidence.strip())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = file.name
            json.dump(record, file, ensure_ascii=False, indent=2)
            file.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return record


def verified_images(docker, env, config, path):
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        valid = (record['version'] == 1 and record['environment'] == 'test'
                 and record['transport'] == config['transport']
                 and isinstance(record['evidence'], str) and bool(record['evidence'].strip())
                 and isinstance(record['accepted_at'], str) and bool(record['accepted_at']))
        images = record['images']
        for service in ('api', 'web'):
            image = images[service]
            valid = valid and _digest(image['id']) and bool(re.fullmatch(r'[0-9a-f]{40}', image['revision']))
        valid = valid and images['api']['revision'] == images['web']['revision']
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError('Valid matching test acceptance record required; run accept test after verification')
    result = {}
    for service in ('api', 'web'):
        ref = config[service + '_image']
        # An implicit tag means latest too. Registry port colons are not tags.
        pinned = _digest(ref) or bool(re.fullmatch(r'.+@sha256:[0-9a-f]{64}', ref))
        tag = ref.rsplit('/', 1)[-1].partition(':')[2]
        if not ref or (not pinned and (not tag or tag.lower() == 'latest')):
            raise ValueError('Production requires explicit non-latest images already verified in test')
        if _image(docker, env, ref) != images[service]:
            raise ValueError('Production image does not match the accepted test image: ' + service)
        # Pin resolved IDs in Compose so moving a tag after validation cannot swap artifacts.
        result[service + '_image'] = images[service]['id']
    return result
