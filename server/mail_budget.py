"""Optional shared SMTP attempt budget; stores counts/timestamps, never mail data."""
from contextlib import closing
import os
from pathlib import Path, PurePosixPath
import sqlite3
import time

KEYS = frozenset({'NIUMA_MAIL_BUDGET_FILE', 'NIUMA_MAIL_BUDGET_DAILY', 'NIUMA_MAIL_BUDGET_TOTAL'})


class BudgetError(Exception):
    pass


def configuration(values, *, container=False):
    if not any(values.get(key) for key in KEYS):
        return None
    try:
        path = (PurePosixPath if container else Path)(values['NIUMA_MAIL_BUDGET_FILE'])
        daily = int(values['NIUMA_MAIL_BUDGET_DAILY'])
        total = int(values['NIUMA_MAIL_BUDGET_TOTAL'])
        if not path.is_absolute() or not 1 <= daily <= 200 or not 1 <= total <= 2000:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('Mail budget requires an absolute file and explicit daily/total free limits') from None
    return path, daily, total


def reserve(settings):
    if settings is None:
        return
    path, daily, total = settings
    try:
        # The parent is provisioned explicitly; never replace missing shared
        # storage with an environment-local counter or reset an existing file.
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with closing(sqlite3.connect(path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS mail_attempts(id INTEGER PRIMARY KEY, reserved_at REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS mail_limits(id INTEGER PRIMARY KEY CHECK(id=1), daily INTEGER NOT NULL, total INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO mail_limits VALUES(1,?,?)', (daily, total))
            saved_daily, saved_total = db.execute('SELECT daily,total FROM mail_limits WHERE id=1').fetchone()
            daily, total = min(daily, saved_daily), min(total, saved_total)
            now = time.time()
            used, recent = db.execute('SELECT COUNT(*),COALESCE(SUM(reserved_at>=?),0) FROM mail_attempts',
                                      (now - 86400,)).fetchone()
            if used >= total or recent >= daily:
                raise BudgetError()
            db.execute('INSERT INTO mail_attempts(reserved_at) VALUES(?)', (now,))
    except (OSError, sqlite3.Error):
        raise BudgetError() from None
