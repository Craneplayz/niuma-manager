"""One-shot host probe. No API process, business payloads, or shell commands."""
import argparse
from contextlib import closing
from datetime import datetime
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import smtplib
import sqlite3
import ssl
import stat
import subprocess
import sys
import time
from email.message import EmailMessage
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, HTTPRedirectHandler, build_opener

try:
    from server import operations
except ModuleNotFoundError:
    import operations

DELAY = 120
REPEAT = 1800
CODES = ('service_unavailable', 'import_failures', 'queue_backlog', 'disk_low')


class ProbeError(Exception):
    """Only constant, content-free errors cross the CLI boundary."""


def validate_environment(path, environment):
    path = Path(path).resolve()
    try:
        marker = json.loads((path.parent / '.niuma-environment.json').read_text(encoding='utf-8'))
        if environment not in ('development', 'test', 'production') or marker.get('environment') != environment:
            raise ValueError()
        if not path.is_file() or not operations.database(path).is_file():
            raise ValueError()
        with operations.connection(path) as db:
            db.execute('SELECT at,value FROM resources LIMIT 0')
            db.execute('SELECT code,state,first_seen,last_seen,last_notified,active_since,delivery_error FROM alerts LIMIT 0')
    except (OSError, ValueError, AttributeError, sqlite3.Error):
        raise ProbeError('environment_or_sidecar_unavailable') from None
    return path


def cpu_ticks(proc):
    values = (proc / 'stat').read_text(encoding='utf-8').splitlines()[0].split()
    if values[0] != 'cpu' or len(values) < 5:
        raise ValueError()
    # guest/guest_nice are already included in user/nice.
    ticks = [int(value) for value in values[1:9]]
    return sum(ticks), ticks[3] + (ticks[4] if len(ticks) > 4 else 0)


def host_resources(disk_path, *, proc=Path('/proc'), pause=time.sleep):
    result = dict(cpuPercent=None, memoryUsedBytes=None, memoryTotalBytes=None,
                  diskUsedBytes=None, diskTotalBytes=None, restarts=None, available=None)
    try:
        before = cpu_ticks(proc)
        pause(.1)
        after = cpu_ticks(proc)
        total, idle = after[0] - before[0], after[1] - before[1]
        if total > 0 and 0 <= idle <= total:
            result['cpuPercent'] = round(100 * (total - idle) / total, 2)
    except (OSError, ValueError, IndexError):
        pass
    try:
        memory = {}
        for line in (proc / 'meminfo').read_text(encoding='utf-8').splitlines():
            key, value = line.split(':', 1)
            if key in ('MemTotal', 'MemAvailable'):
                memory[key] = int(value.split()[0]) * 1024
        total, free = memory['MemTotal'], memory['MemAvailable']
        if total > 0 and 0 <= free <= total:
            result.update(memoryUsedBytes=total - free, memoryTotalBytes=total)
    except (OSError, ValueError, KeyError, IndexError):
        pass
    try:
        disk = shutil.disk_usage(disk_path)
        result.update(diskUsedBytes=disk.total - disk.free, diskTotalBytes=disk.total)
    except OSError:
        pass
    result['available'] = any(result[key] is not None for key in
                              ('cpuPercent', 'memoryUsedBytes', 'diskUsedBytes'))
    return result


def docker_restarts(project, *, execute=subprocess.run):
    """Docker formats expose counts only; never inspect container environments."""
    counts, running = {}, []
    try:
        for service in ('api', 'web'):
            response = execute(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project,
                                '--filter', 'label=com.docker.compose.service=' + service],
                               capture_output=True, text=True, timeout=5, check=True)
            ids = response.stdout.split()
            if not ids:
                counts[service] = None
                running.append(False)
                continue
            if any(re.fullmatch(r'[0-9a-f]{12,64}', item) is None for item in ids):
                raise ValueError()
            response = execute(['docker', 'inspect', '--format', '{{.RestartCount}} {{.State.Running}}', *ids],
                               capture_output=True, text=True, timeout=5, check=True)
            rows = [line.split() for line in response.stdout.splitlines()]
            if len(rows) != len(ids) or any(len(row) != 2 or row[1] not in ('true', 'false') for row in rows):
                raise ValueError()
            values = [int(row[0]) for row in rows]
            if any(value < 0 for value in values):
                raise ValueError()
            counts[service] = sum(values)
            running.append(all(row[1] == 'true' for row in rows))
        return counts, all(running)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_available(url):
    try:
        # Ignore host proxy settings; no redirects, cookies, response body or logs.
        with build_opener(ProxyHandler({}), NoRedirect()).open(url, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def sample(disk_path, project, health_url):
    result = host_resources(disk_path)
    restarts, containers = docker_restarts(project)
    result['restarts'] = sum(restarts.values()) if restarts is not None and all(
        value is not None for value in restarts.values()) else None
    http = http_available(health_url)
    result['serviceAvailable'] = False if http is False or containers is False else True if containers is True else None
    return result


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def business_conditions(path, now):
    """Only queued timestamps cross the business DB boundary, never snapshots."""
    backlog = None
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            rows = db.execute("""SELECT COALESCE(json_extract(snapshot,'$.updated_at'),
                json_extract(snapshot,'$.created_at'))
                FROM source_sync_runs WHERE state='queued'""").fetchall()
        queued, unknown_queue = [], False
        for (queued_at,) in rows:
            at = timestamp(queued_at)
            if at is None or at > now:
                unknown_queue = True
            else:
                queued.append(now - at)
        backlog = True if queued and max(queued) > 120 else None if unknown_queue else False
    except (OSError, sqlite3.Error):
        pass
    failures = None
    try:
        with closing(sqlite3.connect(operations.database(Path(path).resolve()).as_uri() + '?mode=ro',
                                     uri=True, timeout=.2)) as db:
            recent = db.execute("""SELECT code,quantity FROM events WHERE stage='run'
                AND operation IN ('sync','materials','reprocessing','public')
                AND code IN ('succeeded','partial_success','failed','stopped','interrupted')
                AND at>=? AND at<=?""", (now - 600, now)).fetchall()
        failures = sum(row[1] for row in recent) >= 3 and all(row[0] == 'failed' for row in recent)
    except (OSError, sqlite3.Error):
        pass
    return dict(import_failures=failures, queue_backlog=backlog)


def conditions(resource, business):
    used, total = resource.get('diskUsedBytes'), resource.get('diskTotalBytes')
    low = None
    if isinstance(used, (int, float)) and isinstance(total, (int, float)) and 0 <= used <= total and total > 0:
        free = total - used
        low = free / total < .1 or free < 1024 ** 3
    available = resource.get('serviceAvailable')
    return dict(business, service_unavailable=None if available is None else not available, disk_low=low)


def advance(db, code, condition, now):
    row = db.execute('SELECT * FROM alerts WHERE code=?', (code,)).fetchone()
    if row is None:
        if condition is not True:
            return
        alert = dict(code=code, state='pending', first_seen=now, last_seen=now,
                     last_notified=None, active_since=None, delivery_error=0)
    else:
        alert = dict(row)
    if condition is True:
        if alert['state'] in ('resolved', 'recovering'):
            # Cancel a pending recovery if the same incident resumes.
            alert['state'] = 'active' if alert['active_since'] is not None else 'pending'
            alert['first_seen'] = now
        if alert['state'] == 'pending' and (now < alert['last_seen'] or now - alert['last_seen'] > 180):
            alert['first_seen'] = now
        delay = 0 if code == 'import_failures' else DELAY
        if alert['state'] == 'pending' and now - alert['first_seen'] >= delay:
            alert.update(state='firing', active_since=now)
        alert['last_seen'] = now
    elif condition is False:
        if alert['state'] in ('active', 'firing'):
            alert['state'] = 'recovering'
        elif alert['state'] == 'pending':
            alert.update(state='resolved', active_since=None, delivery_error=0)
        alert['last_seen'] = now
    elif alert['state'] == 'pending':
        # Missing evidence breaks continuous duration; it never proves recovery.
        alert.update(state='resolved', active_since=None, delivery_error=0)
    db.execute('''INSERT OR REPLACE INTO alerts
        (code,state,first_seen,last_seen,last_notified,active_since,delivery_error)
        VALUES(:code,:state,:first_seen,:last_seen,:last_notified,:active_since,:delivery_error)''', alert)


def run_once(path, environment, resource, send_notice, *, now=None):
    """send_notice(environment, code, 'firing'|'recovered'); raise on failure.

    State commits before delivery. Exceptions are recorded, never printed; a
    nonzero return tells the scheduler that delivery still needs attention.
    """
    path = validate_environment(path, environment)
    now = time.time() if now is None else now
    checks = conditions(resource, business_conditions(path, now))
    with operations.connection(path) as db:
        db.execute('INSERT OR REPLACE INTO resources(at,value) VALUES(?,?)',
                   (now, json.dumps({key: resource.get(key) for key in
                    ('cpuPercent', 'memoryUsedBytes', 'memoryTotalBytes', 'diskUsedBytes',
                     'diskTotalBytes', 'restarts', 'available', 'serviceAvailable')}, allow_nan=False)))
        db.execute('DELETE FROM resources WHERE at<?', (now - 14 * 86400,))
        for code in CODES:
            advance(db, code, checks[code], now)
        pending = [dict(row) for row in db.execute('SELECT * FROM alerts')
                   if row['code'] in CODES and (row['state'] in ('firing', 'recovering') or
                   row['state'] == 'active' and (row['delivery_error'] or row['last_notified'] is None or
                                                now - row['last_notified'] >= REPEAT))]
    for alert in pending:
        recovery = alert['state'] == 'recovering'
        try:
            if send_notice(environment, alert['code'], 'recovered' if recovery else 'firing') is False:
                raise ProbeError('notice_failed')
        except Exception:
            with operations.connection(path) as db:
                db.execute('UPDATE alerts SET delivery_error=1 WHERE code=?', (alert['code'],))
        else:
            with operations.connection(path) as db:
                db.execute('UPDATE alerts SET state=?,last_notified=?,active_since=?,delivery_error=0 WHERE code=?',
                           ('resolved' if recovery else 'active', now,
                            None if recovery else alert['active_since'], alert['code']))
    with operations.connection(path) as db:
        return db.execute('SELECT COUNT(*) FROM alerts WHERE delivery_error<>0').fetchone()[0]


def smtp_notifier(config):
    """Use exactly the verification mailer's shared, persistent attempt budget."""
    def send_notice(environment, code, transition):
        try:
            delivery = importlib.import_module('mail_delivery')
            quota = delivery.mail_budget
            settings = quota.configuration(config['mail'])
            # mail_budget allows None for verification mail; probe never does.
            if settings is None or config.get('smtp_tls') != 'implicit':
                raise ValueError()
            host, port, sender, password, timeout = delivery._configuration(config['mail'])
            recipient = config['recipient']
            if not delivery._address(recipient):
                raise ValueError()
            message = EmailMessage()
            message['From'], message['To'] = sender, recipient
            message['Subject'] = 'niuma operations: ' + environment + ' ' + code + ' ' + transition
            message.set_content('environment=' + environment + '\ncode=' + code + '\nstate=' + transition + '\n')
            quota.reserve(settings)
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) as client:
                client.login(sender, password)
                if client.send_message(message, from_addr=sender, to_addrs=[recipient]):
                    raise ValueError()
        except Exception:
            raise ProbeError('notice_delivery_unavailable') from None
    return send_notice


def load_config(filename):
    if os.name != 'posix':
        raise ProbeError('unsupported_platform')
    try:
        path = Path(filename)
        with path.open(encoding='utf-8') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
                raise ValueError()
            config = json.load(file)
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', config['compose_project']):
            raise ValueError()
        url = urlsplit(config['health_url'])
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path != '/api/health'):
            raise ValueError()
        validate_environment(config['database'], config['environment'])
        if not Path(config['database']).is_absolute() or not Path(config['disk_path']).is_absolute():
            raise ValueError()
        return config
    except (OSError, ValueError, TypeError, KeyError):
        raise ProbeError('invalid_private_configuration') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Private JSON, owned by this UID, mode 0600')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        config = load_config(args.config)
        # systemd template also uses flock; this lock protects direct CLI runs.
        import fcntl
        with Path(str(operations.database(config['database'])) + '.probe.lock').open('a', encoding='utf-8') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            errors = run_once(config['database'], config['environment'],
                              sample(config['disk_path'], config['compose_project'], config['health_url']),
                              smtp_notifier(config))
        if errors:
            print('operations_probe: notice_delivery_pending', file=sys.stderr)
            return 1
        return 0
    except Exception:
        print('operations_probe: sampling_or_configuration_failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
