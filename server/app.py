"""Local development API. SQLite owns all account and workspace persistence."""
import hashlib
import hmac
import json
import os
import math
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
import recognition

DB = Path(os.environ.get('NIUMA_DB', str(Path(__file__).parent / 'data' / 'niuma.sqlite3')))
TZ = timezone(timedelta(hours=8))

def connection():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    return db

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()

def initialize():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace(user_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(user_id,name));
        CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, version INTEGER NOT NULL, result TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 0);
        ''')
        recognition.initialize(db)

def seed():
    initialize()
    email, password = 'demo@niuma.test', 'Niuma-Test-2026!'
    with connection() as db:
        if db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone():
            print('Test account already exists; data preserved.')
            return
        uid, salt = secrets.token_hex(16), secrets.token_hex(16)
        db.execute('INSERT INTO users VALUES(?,?,?,?,?)', (uid,email,'体验牛马',salt,password_hash(password,salt)))
        now = datetime.now(TZ)
        tasks = []
        for i, title in enumerate(['需求分析报告','数据库课程实验','小组展示准备','阅读论文并整理笔记','完成英语练习','整理上周会议记录']):
            tasks.append(dict(id=secrets.token_hex(16),title=title,description='测试账号模拟任务，可自由编辑。',category='课程作业',priority='high' if i==0 else 'medium',status='completed' if i>=3 else 'not_started',due=dict(date=(now+timedelta(days=i+2)).date().isoformat(),precision='date'),source='模拟数据',subtasks=[],plans=[],createdAt=now.isoformat()))
        day=(now+timedelta(days=1)).date().isoformat()
        tasks[2]['plans']=[dict(id=secrets.token_hex(16),title='讨论展示分工',startAt=day+'T19:00:00+08:00',endAt=day+'T20:00:00+08:00',origin='manual',isLocked=True)]
        db.execute('INSERT INTO workspace VALUES(?,?,?,1)',(uid,'tasks',json.dumps(tasks)))
        feedback=[dict(taskId=task['id'],actualMinutes=minutes,recordedAt=now.isoformat()) for task,minutes in zip(tasks[3:],[90,25,40])]
        db.execute('INSERT INTO workspace VALUES(?,?,?,1)',(uid,'feedback',json.dumps(feedback)))
        db.execute('INSERT INTO workspace VALUES(?,?,?,1)',(uid,'availability',json.dumps(dict(startTime='19:00',endTime='22:00',weekdays=[1,2,3,4,5]))))
    print('Test account: '+email+' / '+password+' (not logged in)')

class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

class Handler(BaseHTTPRequestHandler):
    def reply(self, status, value, cookie=None):
        data=json.dumps(value,ensure_ascii=False).encode()
        if status<400 and getattr(self,'db',None) is not None:
            self.db.commit()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(data)))
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        size=int(self.headers.get('Content-Length','0'))
        if size>2000000: raise ApiError(413,'数据过大')
        value=json.loads(self.rfile.read(size) or b'{}')
        if not isinstance(value,dict): raise ApiError(400,'无效请求')
        return value

    def user(self,db):
        cookie=SimpleCookie(self.headers.get('Cookie',''))
        raw=cookie.get('niuma_session')
        token=hashlib.sha256(raw.value.encode()).hexdigest() if raw else ''
        row=db.execute('SELECT users.id,email,name FROM sessions JOIN users ON user_id=users.id WHERE token=? AND expires>?',(token,time.time())).fetchone()
        if not row: raise ApiError(401,'登录已失效，请重新登录')
        return dict(row),token

    def route(self):
        origin=self.headers.get('Origin')
        if self.command!='GET' and origin and origin not in ['http://localhost:1331','http://127.0.0.1:1331']:
            raise ApiError(403,'不允许的请求来源')
        path=self.path.split('?')[0]
        body=self.body() if self.command in ['POST','PUT'] else {}
        with connection() as db:
            self.db=db
            if path in ['/api/auth/register','/api/auth/login'] and self.command=='POST':
                email=str(body.get('email','')).strip().lower()
                password=str(body.get('password',''))
                if not 8<=len(password)<=128 or '@' not in email or len(email)>254: raise ApiError(400,'请输入有效邮箱和至少 8 位密码')
                row=db.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                if path.endswith('register'):
                    name=str(body.get('name','')).strip()
                    if not name or len(name)>40: raise ApiError(400,'昵称需要 1–40 个字符')
                    if row: raise ApiError(409,'该邮箱已注册')
                    uid,salt=secrets.token_hex(16),secrets.token_hex(16)
                    db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,email,name,salt,password_hash(password,salt)))
                    row=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
                elif not row or not hmac.compare_digest(row['hash'],password_hash(password,row['salt'])):
                    raise ApiError(401,'邮箱或密码不正确')
                token=secrets.token_urlsafe(32)
                db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
                db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],time.time()+86400))
                return self.reply(200,dict(user={k:row[k] for k in ['id','name','email']}),'niuma_session='+token+'; HttpOnly; SameSite=Strict; Path=/api; Max-Age=86400')
            user,token=self.user(db)
            uid=user['id']
            if recognition.route(self,db,uid,path,body,ApiError): return
            if path=='/api/auth/me' and self.command=='GET': return self.reply(200,dict(user=user))
            if path=='/api/auth/logout' and self.command=='POST':
                db.execute('DELETE FROM sessions WHERE token=?',(token,))
                return self.reply(200,{},'niuma_session=; HttpOnly; SameSite=Strict; Path=/api; Max-Age=0')
            if path=='/api/workspace' and self.command=='GET':
                rows=db.execute('SELECT name,value,version FROM workspace WHERE user_id=?',(uid,)).fetchall()
                return self.reply(200,{r['name']:dict(value=json.loads(r['value']),version=r['version']) for r in rows})
            if path.startswith('/api/data/') and self.command=='PUT':
                name=path.rsplit('/',1)[1]
                if name not in ['tasks','candidates','availability','feedback','courses','focus']: raise ApiError(404,'未知数据类型')
                value=body.get('value')
                if not isinstance(value,dict if name in ['availability','focus'] else list): raise ApiError(400,'数据格式不正确')
                if name=='courses':
                    from urllib.parse import urlsplit
                    ids,names=set(),set()
                    for course in value:
                        if not isinstance(course,dict) or any(not isinstance(course.get(k),str) or len(course[k])>limit for k,limit in [('id',100),('name',120),('teacher',120),('notes',20000),('url',2000)]): raise ApiError(400,'课程格式不正确')
                        if not course['id'] or not course['name'].strip() or course['id'] in ids or course['name'].strip() in names: raise ApiError(400,'课程名称或编号为空或重复')
                        try:
                            link=urlsplit(course['url'])
                            if course['url'] and (link.scheme not in ['http','https'] or not link.hostname or link.username or link.password): raise ValueError()
                        except ValueError: raise ApiError(400,'资料链接必须是不带账号密码的 HTTP(S) 地址')
                        ids.add(course['id']);names.add(course['name'].strip())
                if name=='focus':
                    if type(value.get('duration'))!=int or value['duration'] not in [1500,3000] or type(value.get('remaining'))!=int or not 0<=value['remaining']<=value['duration'] or type(value.get('running'))!=bool or type(value.get('endsAt')) not in [int,float] or not math.isfinite(value['endsAt']) or value['endsAt']<0 or not isinstance(value.get('taskId'),str): raise ApiError(400,'专注计时格式不正确')
                    if value['running'] and not 0<value['endsAt']<=time.time()*1000+value['duration']*1000+60000: raise ApiError(400,'计时结束时间无效')
                    if value['taskId']:
                        taskrow=db.execute('SELECT value FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
                        if not any(t['id']==value['taskId'] for t in json.loads(taskrow['value'] if taskrow else '[]')): raise ApiError(400,'关联任务不存在或不属于当前账号')
                if name=='tasks':
                    ids=[]
                    for task in value:
                        if not isinstance(task,dict) or not isinstance(task.get('id'),str) or not isinstance(task.get('title'),str) or not isinstance(task.get('plans'),list) or not isinstance(task.get('subtasks'),list): raise ApiError(400,'任务格式不正确')
                        ids.append(task['id'])
                    if len(ids)!=len(set(ids)): raise ApiError(400,'任务编号重复')
                if name=='feedback':
                    taskrow=db.execute('SELECT value FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
                    completed={t['id'] for t in json.loads(taskrow['value'] if taskrow else '[]') if t['status']=='completed'}
                    if any(not isinstance(f,dict) or f.get('taskId') not in completed or not isinstance(f.get('actualMinutes'),(int,float)) or not 0<f['actualMinutes']<=100000 for f in value): raise ApiError(400,'只能记录已完成任务的有效用时')
                if name=='availability':
                    try:
                        start=datetime.strptime(value['startTime'],'%H:%M')
                        end=datetime.strptime(value['endTime'],'%H:%M')
                        if start>=end or not value['weekdays'] or any(type(d)!=int or d not in range(7) for d in value['weekdays']): raise ValueError()
                    except (ValueError,KeyError,TypeError): raise ApiError(400,'可用时间格式不正确')
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT version FROM workspace WHERE user_id=? AND name=?',(uid,name)).fetchone()
                version=row['version'] if row else 0
                if body.get('version')!=version: raise ApiError(409,'数据已在其他窗口修改，请刷新后重试')
                db.execute('INSERT INTO workspace VALUES(?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value,version=excluded.version',(uid,name,json.dumps(value),version+1))
                return self.reply(200,dict(version=version+1))
            if path=='/api/planning/proposals' and self.command=='POST':
                endpoint=os.environ.get('HARNESS_URL')
                if not endpoint: raise ApiError(503,'尚未接入模型服务；请配置 HARNESS_URL，由模型评估工时并生成规划。任务不会被修改。')
                rows={r['name']:r for r in db.execute('SELECT name,value,version FROM workspace WHERE user_id=?',(uid,))}
                tasks=json.loads(rows['tasks']['value']) if 'tasks' in rows else []
                pending=[t for t in tasks if t['status'] not in ['completed','cancelled'] and not t.get('planningExcluded') and not t['plans']]
                if not pending: raise ApiError(400,'没有需要规划的任务')
                payload=dict(schemaVersion='2.0',requestId=secrets.token_hex(16),requestedAt=datetime.now(TZ).isoformat(),timezone='Asia/Shanghai',tasks=pending,existingPlans=[dict(p,taskId=t['id']) for t in tasks if t['status']!='cancelled' for p in t['plans']],availability=json.loads(rows['availability']['value']) if 'availability' in rows else dict(startTime='19:00',endTime='22:00',weekdays=[1,2,3,4,5]),completionHistory=json.loads(rows['feedback']['value']) if 'feedback' in rows else [])
                payload['completionHistory']=[dict(f,task=next(t for t in tasks if t['id']==f['taskId'])) for f in payload['completionHistory'] if any(t['id']==f['taskId'] for t in tasks)]
                for task in payload['tasks']:
                    task.pop('estimatedMinutes',None)
                # Only the server knows the harness credential. The harness must return the documented JSON schema.
                headers={'Content-Type':'application/json'}
                if os.environ.get('HARNESS_TOKEN'): headers['Authorization']='Bearer '+os.environ['HARNESS_TOKEN']
                with urlopen(Request(endpoint,json.dumps(payload).encode(),headers),timeout=25) as response:
                    result=json.loads(response.read(2000000))
                validate_proposal(payload,result)
                result['requestId']=payload['requestId']
                db.execute('INSERT INTO proposals VALUES(?,?,?,?,0)',(payload['requestId'],uid,rows['tasks']['version'],json.dumps(result)))
                return self.reply(200,result)
            if path=='/api/planning/apply' and self.command=='POST':
                db.execute('BEGIN IMMEDIATE')
                proposal=db.execute('SELECT * FROM proposals WHERE id=? AND user_id=?',(body.get('requestId'),uid)).fetchone()
                row=db.execute('SELECT * FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
                if not proposal or not row: raise ApiError(404,'规划不存在')
                if proposal['applied']: raise ApiError(409,'规划已应用，请刷新')
                if row['version']!=proposal['version']: raise ApiError(409,'任务已变化，请重新规划')
                tasks=json.loads(row['value'])
                result=json.loads(proposal['result'])
                for block in result['blocks']:
                    if datetime.fromisoformat(block['startAt'])<datetime.now(TZ): raise ApiError(409,'规划时段已过期，请重新规划')
                    task=next(t for t in tasks if t['id']==block['taskId'])
                    task['plans'].append(dict(id=secrets.token_hex(16),title=block['title'],startAt=block['startAt'],endAt=block['endAt'],origin='ai'))
                db.execute('UPDATE workspace SET value=?,version=version+1 WHERE user_id=? AND name="tasks"',(json.dumps(tasks),uid))
                db.execute('UPDATE proposals SET applied=1 WHERE id=?',(proposal['id'],))
                return self.reply(200,dict(value=tasks,version=row['version']+1))
            raise ApiError(404,'接口不存在')

    def do_GET(self): self.handle_request()
    def do_POST(self): self.handle_request()
    def do_PUT(self): self.handle_request()
    def handle_request(self):
        try: self.route()
        except ApiError as e: self.reply(e.status,dict(error=e.message))
        except (ValueError,KeyError,TypeError): self.reply(400,dict(error='数据格式不正确'))
        except Exception as e:
            print(type(e).__name__,str(e))
            self.reply(502,dict(error='服务处理失败，请稍后重试；数据未变更'))
        finally:
            if getattr(self,'db',None) is not None: self.db.close()

def validate_proposal(request,result):
    if not isinstance(result,dict) or not isinstance(result.get('blocks'),list) or not isinstance(result.get('warnings'),list) or not all(isinstance(w,str) for w in result['warnings']): raise ApiError(502,'模型返回格式不正确')
    tasks={t['id']:t for t in request['tasks']}
    estimates=result.get('estimates',{})
    if not isinstance(estimates,dict) or any(type(estimates.get(t)) not in (int,float) or not math.isfinite(estimates[t]) or not 0<estimates[t]<=100000 for t in tasks): raise ApiError(502,'模型未提供有效工时评估')
    occupied=[]
    for p in request['existingPlans']:
        start=datetime.fromisoformat(p['startAt'])
        occupied.append((start,datetime.fromisoformat(p['endAt']) if p.get('endAt') else start+timedelta(hours=1)))
    totals={t:0 for t in tasks}
    a=request['availability']
    for b in result['blocks']:
        task=tasks.get(b.get('taskId'))
        if not task or not isinstance(b.get('title'),str) or not b['title'].strip(): raise ApiError(502,'模型返回未知任务')
        start,end=datetime.fromisoformat(b['startAt']),datetime.fromisoformat(b['endAt'])
        if start.utcoffset()!=timedelta(hours=8) or end.utcoffset()!=timedelta(hours=8): raise ApiError(502,'模型时间格式错误')
        if start>=end or start<datetime.fromisoformat(request['requestedAt']) or start.date()!=end.date() or (start.weekday()+1)%7 not in a['weekdays'] or start.strftime('%H:%M')<a['startTime'] or end.strftime('%H:%M')>a['endTime']: raise ApiError(502,'模型结果违反可用时间')
        due=task.get('due')
        if due and end>datetime.fromisoformat(due['date']+'T'+(due.get('time') or '23:59:59')+'+08:00'): raise ApiError(502,'模型结果超过真实截止时间')
        if any(start<y and x<end for x,y in occupied): raise ApiError(502,'模型结果存在时间冲突')
        occupied.append((start,end)); totals[task['id']]+=(end-start).total_seconds()/60
        if totals[task['id']]>estimates[task['id']]: raise ApiError(502,'计划超过模型评估工时')
    for tid in tasks:
        if totals[tid]<estimates[tid]: result['warnings'].append(tasks[tid]['title']+'：未全部安排，请检查约束或截止时间')

if __name__=='__main__':
    import sys
    initialize()
    if '--seed' in sys.argv: seed()
    else:
        print('牛马管理器 API: http://127.0.0.1:1332',flush=True)
        ThreadingHTTPServer(('127.0.0.1',1332),Handler).serve_forever()
