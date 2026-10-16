"""Seven-day active-list retention; preserve imported work in the existing trash.

This is a product retention setting, not a semantic validity classifier. A user
restore explicitly opts out for that task. Sources, decisions and audit survive.
"""
from datetime import datetime,timedelta,timezone
import json

LOCAL=timezone(timedelta(hours=8))
DAYS=7

def expired(due,now=None):
    now=now or datetime.now(LOCAL)
    try:
        # Date-only remains active through the entire deadline day.
        end=datetime.fromisoformat(due['date']+'T'+(due.get('time') or '23:59:59.999999')).replace(tzinfo=LOCAL)
        return now.astimezone(LOCAL)-end>timedelta(days=DAYS)
    except (KeyError,ValueError,TypeError):return False

def archive(task,now=None):
    if (not task.get('recognitionHistory') or task.get('retentionOverride') or task.get('recycleBin')
            or task.get('status') in ('completed','cancelled') or not expired(task.get('due'),now)):
        return False
    task['recycleBin']=dict(deletedAt=(now or datetime.now(LOCAL)).isoformat(),status=task['status'],reason='expired_retention')
    task['status']='cancelled'
    return True

def sweep(db,uid,now=None):
    row=db.execute('SELECT value,version FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
    tasks=json.loads(row['value']) if row else [];changed=False
    for task in tasks:changed=archive(task,now) or changed
    if changed:
        db.execute('UPDATE workspace SET value=?,version=? WHERE user_id=? AND name="tasks"',
                   (json.dumps(tasks),row['version']+1,uid))
    for candidate in db.execute("SELECT id,candidate FROM recognition_candidates WHERE user_id=? AND state='pending' AND version=1",(uid,)).fetchall():
        card=json.loads(candidate['candidate']);deadline=card.get('deadline',{})
        if card.get('notice_type')!='create' or deadline.get('status')!='explicit':continue
        value=deadline.get('value');due=None
        try:
            if deadline.get('precision')=='date':due=dict(date=value)
            elif deadline.get('precision')=='datetime':
                local=datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(LOCAL)
                due=dict(date=local.date().isoformat(),time=local.strftime('%H:%M'))
        except (ValueError,AttributeError,TypeError):continue
        if expired(due,now):
            db.execute("UPDATE recognition_candidates SET state='ignored',version=version+1,resolution=? WHERE id=? AND user_id=?",
                (json.dumps(dict(action='expired_retention',days=DAYS)),candidate['id'],uid))
