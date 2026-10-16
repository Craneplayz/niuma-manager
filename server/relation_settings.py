"""Explicit opt-in for account-local automatic relation writes."""
from contracts import relation_settings as contract

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS task_relation_settings(
        user_id TEXT,account TEXT,mode TEXT,revision INTEGER,
        PRIMARY KEY(user_id,account))''')

def view(db,uid,account):
    row=db.execute('SELECT mode,revision FROM task_relation_settings WHERE user_id=? AND account=?',(uid,account)).fetchone()
    return contract.check(dict(contract_version=contract.VERSION,source_account_id=account,
        mode=row['mode'] if row else 'manual',revision=row['revision'] if row else 0))

def automatic(db,uid,account):
    return bool(account) and view(db,uid,account)['mode']=='automatic'

def update(db,uid,account,body,ApiError):
    contract.check(body,update=True)
    old=view(db,uid,account)
    if body['source_account_id']!=account or body['revision']!=old['revision']:
        raise ApiError(409,'关联设置或教学网账号已变化，请重新读取设置')
    if body['mode']=='automatic' and not body['consent']:
        raise ApiError(400,'请明确同意自动分析当前账号来源并合并任务')
    if body['mode']!=old['mode']:
        # Stop both directions: a manual in-flight request must not acquire
        # automatic write permission just because a setting changed later.
        db.execute("UPDATE task_relation_jobs SET state='stopped',error='关联模式已变化，本轮停止；已保存结果保留。' WHERE user_id=? AND account=? AND state='running'",(uid,account))
        db.execute('DELETE FROM task_relation_cache WHERE user_id=? AND account=?',(uid,account))
    db.execute('''INSERT INTO task_relation_settings VALUES(?,?,?,?)
        ON CONFLICT(user_id,account) DO UPDATE SET mode=excluded.mode,revision=excluded.revision''',
        (uid,account,body['mode'],old['revision']+1))
    return view(db,uid,account)
