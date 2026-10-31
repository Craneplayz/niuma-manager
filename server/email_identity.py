"""Stable, account-scoped mailbox identities, retained across disconnects.

The first login keeps legacy message IDs. Other logins get opaque namespaces;
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


def remember(db, uid, address, username):
    if db.execute('SELECT 1 FROM email_source_identities WHERE user_id=? AND address=? AND username=?',
                  (uid,address,username)).fetchone():
        return
    known = db.execute('SELECT 1 FROM email_source_identities WHERE user_id=?', (uid,)).fetchone()
    db.execute('INSERT INTO email_source_identities VALUES(?,?,?,?)',
               (uid,address,username,secrets.token_hex(12) if known else None))


def namespace(db, uid):
    row = db.execute("""SELECT i.namespace FROM email_source_identities i
        JOIN email_source_credentials c ON c.user_id=i.user_id AND c.address=i.address AND c.username=i.username
        WHERE i.user_id=?""", (uid,)).fetchone()
    return row[0] if row else None
