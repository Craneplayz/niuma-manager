"""Stable, account-scoped mailbox identities, retained across disconnects.

A known legacy login keeps its message IDs. Other logins get opaque namespaces;
password rotation preserves identity and historical recognition is never erased.
"""
import secrets


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS email_source_identities(
        user_id TEXT NOT NULL,address TEXT NOT NULL,username TEXT NOT NULL,namespace TEXT,
        PRIMARY KEY(user_id,address,username))""")
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='email_source_credentials'").fetchone():
        db.execute("""INSERT OR IGNORE INTO email_source_identities(user_id,address,username,namespace)
            SELECT user_id,address,username,NULL FROM email_source_credentials""")


def _has_unbound_history(db, uid):
    # A disconnected legacy mailbox has no trustworthy login to attach to its
    # retained results/exclusions. Preserve the history without adopting it.
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('email_recognition_cache', 'email_import_exclusions', 'email_import_jobs',
                  'email_import_body_snapshots', 'email_cached_headers', 'email_header_snapshots'):
        if table in tables and db.execute(f'SELECT 1 FROM {table} WHERE user_id=? LIMIT 1', (uid,)).fetchone():
            return True
    if 'source_versions' in tables and db.execute(
            "SELECT 1 FROM source_versions WHERE user_id=? AND input_id LIKE 'email:%' LIMIT 1", (uid,)).fetchone():
        return True
    return 'recognition_batches' in tables and bool(db.execute("""SELECT 1 FROM recognition_batches
        WHERE user_id=? AND EXISTS(SELECT 1 FROM json_each(request,'$.records')
            WHERE json_extract(value,'$.platform')='email') LIMIT 1""", (uid,)).fetchone())


def remember(db, uid, address, username):
    if db.execute('SELECT 1 FROM email_source_identities WHERE user_id=? AND address=? AND username=?',
                  (uid,address,username)).fetchone():
        return
    known = db.execute('SELECT 1 FROM email_source_identities WHERE user_id=?', (uid,)).fetchone()
    db.execute('INSERT INTO email_source_identities VALUES(?,?,?,?)',
               (uid,address,username,secrets.token_hex(12) if known or _has_unbound_history(db,uid) else None))


def namespace(db, uid):
    row = db.execute("""SELECT i.namespace FROM email_source_identities i
        JOIN email_source_credentials c ON c.user_id=i.user_id AND c.address=i.address AND c.username=i.username
        WHERE i.user_id=?""", (uid,)).fetchone()
    return row[0] if row else None
