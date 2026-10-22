"""Local development API. SQLite owns all account and workspace persistence."""
import hashlib
import hmac
import json
import os
import math
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as _ThreadingHTTPServer
from pathlib import Path
import recognition
import planning
import source_sync
import source_jobs
import submission_runtime
import public_sources
import source_schedule
import wechat_media
import manual_materials
import task_decomposition
import reprocessing
import runtime_environment
import usage
import error_reports
import account_avatar
import email_verification
import ipaddress
from server import model_queue
from server import operations


class ThreadingHTTPServer(_ThreadingHTTPServer):
    request_queue_size = 128

DB = runtime_environment.database_path()
TZ = timezone(timedelta(hours=8))

def wechat_enabled():
    return os.environ.get('NIUMA_ENABLE_WECHAT', '1') == '1'

@contextmanager
def connection():
    db = sqlite3.connect(DB, timeout=10, factory=operations.TimedConnection)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()

def initialize_accounts(db):
    """Account schema only; safe for tools that must not recover or prune jobs."""
    account_avatar.initialize(db)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS workspace(user_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(user_id,name));
    CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, version INTEGER NOT NULL, result TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 0);
    ''')
    email_verification.initialize(db)

def initialize(recover=True):
    runtime_environment.validate_storage(DB)
    DB.parent.mkdir(parents=True, exist_ok=True)
    try:operations.initialize(DB)
    except (OSError,sqlite3.Error):print('{"event":"operations_unavailable"}',flush=True)
    with connection() as db:
        initialize_accounts(db)
        recognition.initialize(db)
        usage.initialize(db)
        error_reports.initialize(db)
        source_sync.initialize(db)
        source_jobs.initialize(db, recover=recover)
        source_schedule.initialize(db)
        public_sources.initialize(db,recover=recover)
        reprocessing.initialize(db,recover=recover)
        if wechat_enabled() or manual_materials.enabled():
            wechat_media.initialize(db,recover=recover and wechat_enabled())
        if manual_materials.enabled():
            manual_materials.initialize(db,recover=recover and not wechat_enabled())
        task_decomposition.initialize(db)
    if recover and wechat_enabled() and runtime_environment.allow_background_recovery():
        wechat_media.resume(DB)

def seed():
    if runtime_environment.environment() == 'production':
        raise ValueError('Seeding demo accounts is disabled in production')
    initialize(recover=False)
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

def allowed_origins():
    configured=os.environ.get('NIUMA_ALLOWED_ORIGINS')
    return configured.split() if configured is not None else ['http://localhost:1331','http://127.0.0.1:1331']

def session_cookie(token, max_age, admin=False):
    secure='; Secure' if os.environ.get('NIUMA_COOKIE_SECURE')=='1' else ''
    name=runtime_environment.cookie_name()+('_admin' if admin else '')
    return name+'='+token+'; HttpOnly; SameSite=Strict; Path='+('/api/admin' if admin else '/api')+'; Max-Age='+str(max_age)+secure

def public_user(db, row):
    return dict(id=row['id'], name=row['name'], email=row['email'], avatar=account_avatar.read(db, row['id']),
                emailVerified=email_verification.verified(db, row['id']))

class Handler(BaseHTTPRequestHandler):
    def log_message(self,format,*args):
        pass  # Structured request logs never include raw URLs or query data.

    def reply(self, status, value, cookie=None):
        self.ops_status=status
        if status<400 and not usage.successful(status,value,getattr(self,'usage_request_id',None)):
            self.ops_code='upstream_error'
        data=b'' if status==204 else json.dumps(value,ensure_ascii=False).encode()
        if getattr(self,'usage_uid',None) and not getattr(self,'usage_recorded',False):
            event=usage.operation(self.path.split('?')[0]) if self.command=='POST' else None
            if event:
                # Recognition can return HTTP 200 with a failed business result.
                success=usage.successful(status,value,getattr(self,'usage_request_id',None))
                if status<400:
                    usage.record(self.db,self.usage_uid,event,success,(time.monotonic()-self.usage_started)*1000)
                else:
                    with connection() as metrics_db:
                        usage.record(metrics_db,self.usage_uid,event,success,(time.monotonic()-self.usage_started)*1000)
                self.usage_recorded=True
        if status<400 and getattr(self,'usage_uid',None) and getattr(self,'usage_tasks',None) is not None:
            current=self.db.execute('SELECT value FROM workspace WHERE user_id=? AND name="tasks"',(self.usage_uid,)).fetchone()
            usage.workspace_changes(self.db,self.usage_uid,self.usage_tasks,json.loads(current['value']) if current else [])
            self.usage_tasks=None
        if status<400 and getattr(self,'db',None) is not None:
            self.db.commit()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        context=operations.captured()
        if context:self.send_header('X-Request-ID',context['request_id'])
        self.send_header('Content-Length',str(len(data)))
        if cookie: self.send_header('Set-Cookie',cookie)
        if getattr(self, 'retry_after', None): self.send_header('Retry-After', str(self.retry_after))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        if self.path.startswith(('/api/sources/wechat/','/api/materials/')):
            try:
                value=json.loads(wechat_media.read_body(self,2000000) or b'{}')
            except (ValueError,UnicodeError):
                raise wechat_media.MediaError(400,'invalid_request','请求须为有效 JSON') from None
            if not isinstance(value,dict):
                raise wechat_media.MediaError(400,'invalid_request','请求须为 JSON 对象')
            return value
        size=int(self.headers.get('Content-Length','0'))
        if size>2000000:
            if self.path.startswith('/api/sources/wechat/'):
                raise wechat_media.MediaError(413,'limit_exceeded','数据超过 2 MB')
            raise ApiError(413,'数据过大')
        value=json.loads(self.rfile.read(size) or b'{}')
        if not isinstance(value,dict): raise ApiError(400,'无效请求')
        return value

    def user(self,db):
        cookie=SimpleCookie(self.headers.get('Cookie',''))
        raw=cookie.get(runtime_environment.cookie_name()+('_admin' if self.path.startswith('/api/admin/') else ''))
        token=hashlib.sha256(raw.value.encode()).hexdigest() if raw else ''
        row=db.execute('SELECT users.id,email,name FROM sessions JOIN users ON user_id=users.id WHERE token=? AND expires>?',(token,time.time())).fetchone()
        if not row: raise ApiError(401,'登录已失效，请重新登录')
        return public_user(db, row),token

    def email_client_ip(self):
        # Compose exposes only Caddy, which overwrites this private header.
        value = self.client_address[0]
        if os.environ.get('NIUMA_TRUST_PROXY_CLIENT_IP') == '1':
            forwarded = self.headers.get('X-Niuma-Client-IP', '')
            try:
                value = str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        return value

    def route(self):
        origin=self.headers.get('Origin')
        if self.command!='GET' and origin and origin not in allowed_origins():
            raise ApiError(403,'不允许的请求来源')
        path=self.path.split('?')[0]
        if path=='/api/error-reports' and int(self.headers.get('Content-Length','0'))>4096:
            self.close_connection=True
            raise ApiError(413,'问题报告过大')
        if path.startswith('/api/materials/') and (not manual_materials.enabled() or self.headers.get('Authorization')):
            self.close_connection=True
            raise wechat_media.MediaError(403,'forbidden','此入口仅接受已登录用户主动上传的材料')
        if path.startswith('/api/sources/wechat') and not wechat_enabled():
            self.close_connection=True
            raise ApiError(403,'此部署未开放微信接入')
        body=self.body() if self.command in ['POST','PUT','DELETE'] and not (path in ('/api/sources/wechat/assets','/api/materials/assets') and self.headers.get('Content-Type','').startswith('multipart/form-data;')) else {}
        self.usage_request_id=body.get('request_id')
        if path.startswith('/api/recognition/') and manual_materials.enabled() and isinstance(body.get('records'),list):
            for record in body['records']:
                if isinstance(record,dict) and record.get('platform')=='manual' and isinstance(record.get('text'),str):
                    wechat_media._reject_encrypted(record['text'].encode('utf-8'))
        if path.startswith('/api/recognition/') and not wechat_enabled() and isinstance(body.get('records'),list) and any(
                isinstance(record,dict) and record.get('platform')=='wechat'
                for record in body['records']):
            raise ApiError(403,'此部署未开放微信接入')
        with connection() as db:
            self.db=db
            self.media_db_path=DB
            if path=='/api/health' and self.command=='GET':
                db.execute('SELECT 1')
                return self.reply(200,dict(status='ok'))
            if path=='/api/version' and self.command=='GET':
                return self.reply(200,runtime_environment.public_version())
            if path=='/api/usage/guest-visit' and self.command=='POST':
                if body: raise ApiError(400,'访问统计不接受额外字段')
                try:
                    self.user(db)
                    return self.reply(200,dict(saved=True))
                except ApiError as error:
                    if error.status!=401: raise
                name=runtime_environment.cookie_name()+'_visitor'
                cookie=SimpleCookie(self.headers.get('Cookie',''))
                token=cookie[name].value if name in cookie else ''
                if not re.fullmatch('[0-9a-f]{32}',token): token=secrets.token_hex(16)
                usage.guest_visit(db,token)
                secure='; Secure' if os.environ.get('NIUMA_COOKIE_SECURE')=='1' else ''
                return self.reply(200,dict(saved=True),name+'='+token+'; HttpOnly; SameSite=Strict; Path=/api/usage; Max-Age=7776000'+secure)
            if path=='/api/error-reports' and self.command=='POST':
                try:
                    report_user,_=self.user(db)
                except ApiError as error:
                    if error.status!=401: raise
                    report_user=None
                return self.reply(200,error_reports.save(db,body,report_user['id'] if report_user else None,self.client_address[0],ApiError))
            if path=='/api/auth/email-code' and self.command=='POST':
                purpose = body.get('purpose')
                if purpose == 'register':
                    email = email_verification.normalize_email(body.get('email'))
                    if email_verification.account_for_email(db, email):
                        raise email_verification.VerificationError(409, 'email_registered', '该邮箱已注册，请登录后验证邮箱')
                    uid = ''
                elif purpose == 'verify_email':
                    user, _ = self.user(db)
                    uid, email = user['id'], user['email']
                    if user['emailVerified']:
                        raise email_verification.VerificationError(409, 'email_already_verified', '当前邮箱已经验证')
                elif purpose == 'reset_password':
                    email = body.get('email')
                    uid = ''
                else:
                    raise email_verification.VerificationError(400, 'email_code_invalid', '验证码用途不正确')
                result = email_verification.request_code(db, email, purpose, uid, self.email_client_ip(), DB.parent / 'email-verification.key')
                return self.reply(200, result)
            if path=='/api/auth/reset-password' and self.command=='POST':
                email = body.get('email')
                email_verification.normalize_email(email)
                new = body.get('newPassword')
                if not isinstance(new, str) or not 8 <= len(new) <= 128:
                    raise ApiError(400, '新密码需要 8–128 个字符')
                db.execute('BEGIN IMMEDIATE')
                row = email_verification.account_for_email(db, email)
                eligible = bool(row and email_verification.verified(db, row['id']))
                email_verification.consume_code(db, email, 'reset_password', row['id'] if eligible else '',
                                                body.get('challengeId'), body.get('code'), self.email_client_ip(),
                                                DB.parent / 'email-verification.key', credentials=row if eligible else None)
                salt = secrets.token_hex(16)
                db.execute('UPDATE users SET salt=?,hash=? WHERE id=?', (salt, password_hash(new, salt), row['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
                email_verification.invalidate_account_codes(db, row['id'])
                return self.reply(200, dict(reset=True))
            if path in ['/api/auth/register','/api/auth/login','/api/admin/auth/login'] and self.command=='POST':
                email=str(body.get('email','')).strip().lower()
                password=str(body.get('password',''))
                if not 8<=len(password)<=128 or '@' not in email or len(email)>254: raise ApiError(400,'请输入有效邮箱和至少 8 位密码')
                row=None
                if not path.endswith('register'):
                    try:
                        row=email_verification.account_for_email(db,str(body.get('email','')))
                    except email_verification.VerificationError:
                        # Legacy addresses outside current validation can still log in.
                        row=db.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                        if row:
                            try:
                                email_verification.normalize_email(row['email'])
                            except email_verification.VerificationError:
                                pass
                            else:
                                row=None
                if path.endswith('register'):
                    email = email_verification.normalize_email(body.get('email'))
                    name=str(body.get('name','')).strip()
                    if not name or len(name)>40: raise ApiError(400,'昵称需要 1–40 个字符')
                    db.execute('BEGIN IMMEDIATE')
                    # Legacy Unicode-domain addresses keep their stored value,
                    # but share one identity with their canonical IDNA address.
                    row=email_verification.account_for_email(db, email)
                    if row: raise ApiError(409,'该邮箱已注册')
                    email_verification.consume_code(db, email, 'register', '', body.get('challengeId'), body.get('code'),
                                                    self.email_client_ip(), DB.parent / 'email-verification.key')
                    uid,salt=secrets.token_hex(16),secrets.token_hex(16)
                    db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,email,name,salt,password_hash(password,salt)))
                    email_verification.mark_verified(db, uid)
                    row=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
                elif not row or not hmac.compare_digest(row['hash'],password_hash(password,row['salt'])):
                    raise ApiError(401,'邮箱或密码不正确')
                else:
                    # Password hashing happens outside the write lock. Recheck the
                    # snapshot inside it so a concurrent reset cannot create a
                    # new session authenticated by the previous password.
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('SELECT * FROM users WHERE id=?', (row['id'],)).fetchone()
                    if not current or current['salt'] != row['salt'] or not hmac.compare_digest(current['hash'], row['hash']):
                        raise ApiError(401, '邮箱或密码不正确')
                    row = current
                administrator=path.startswith('/api/admin/')
                if administrator and not usage.admin(row['id'],db): raise ApiError(403,'此账号没有后台访问权限')
                token=secrets.token_urlsafe(32)
                db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
                db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],time.time()+86400))
                usage.record(db,row['id'],'register' if path.endswith('register') else 'login')
                return self.reply(200,dict(user=public_user(db, row)),session_cookie(token,86400,administrator))
            bearer=wechat_media.bearer_user(db,self.headers) if path.startswith('/api/sources/wechat/') and self.headers.get('Authorization') else None
            if bearer:
                uid,token,user=bearer,None,None
            else:
                user,token=self.user(db)
                uid=user['id']
            self.usage_uid=uid
            if path=='/api/auth/verify-email' and self.command=='POST':
                if user['emailVerified']:
                    raise email_verification.VerificationError(409, 'email_already_verified', '当前邮箱已经验证')
                email_verification.consume_code(db, user['email'], 'verify_email', uid, body.get('challengeId'), body.get('code'),
                                                self.email_client_ip(), DB.parent / 'email-verification.key')
                email_verification.mark_verified(db, uid)
                return self.reply(200, dict(user=public_user(db, user)))
            if path=='/api/admin/auth/logout' and self.command=='POST':
                db.execute('DELETE FROM sessions WHERE token=?',(token,))
                return self.reply(200,{},session_cookie('',0,True))
            if usage.route(self,db,uid,path,body,ApiError): return
            if wechat_enabled() and wechat_media.route(self,db,uid,path,body,DB.parent): return
            if manual_materials.route(self,db,uid,path,body,DB.parent): return
            if source_jobs.route(self,db,uid,path,body,ApiError,DB): return
            if source_schedule.route(self,db,uid,path,body,ApiError): return
            if public_sources.route(self,db,uid,path,body,ApiError,DB): return
            if source_sync.route(self,db,uid,path,body,ApiError,DB.parent / 'credential.key'): return
            if submission_runtime.route(self,db,uid,path,body,ApiError): return
            if reprocessing.route(self,db,uid,path,body,ApiError): return
            if recognition.route(self,db,uid,path,body,ApiError): return
            if task_decomposition.route(self,db,uid,path,body,ApiError): return
            if path=='/api/auth/me' and self.command=='GET': return self.reply(200,dict(user=user))
            if path=='/api/auth/logout' and self.command=='POST':
                db.execute('DELETE FROM sessions WHERE token=?',(token,))
                return self.reply(200,{},session_cookie('',0))
            if path=='/api/auth/profile' and self.command=='POST':
                name=str(body.get('name','')).strip()
                if not name or len(name)>40: raise ApiError(400,'昵称需要 1–40 个字符')
                db.execute('UPDATE users SET name=? WHERE id=?',(name,uid))
                return self.reply(200,dict(user=dict(user,name=name)))
            if path=='/api/auth/avatar' and self.command=='POST':
                avatar=account_avatar.save(db,uid,body,ApiError)
                return self.reply(200,dict(user=dict(user,avatar=avatar)))
            if path=='/api/auth/password' and self.command=='POST':
                current=str(body.get('currentPassword',''))
                new=str(body.get('newPassword',''))
                if not 8<=len(new)<=128: raise ApiError(400,'新密码需要 8–128 个字符')
                db.execute('BEGIN IMMEDIATE')
                # A request may have passed the initial session check before a
                # recovery revoked it; never let it restore the old credentials.
                self.user(db)
                row=db.execute('SELECT salt,hash FROM users WHERE id=?',(uid,)).fetchone()
                if not hmac.compare_digest(row['hash'],password_hash(current,row['salt'])): raise ApiError(400,'当前密码不正确')
                salt=secrets.token_hex(16)
                db.execute('UPDATE users SET salt=?,hash=? WHERE id=?',(salt,password_hash(new,salt),uid))
                db.execute('DELETE FROM sessions WHERE user_id=? AND token!=?',(uid,token))
                email_verification.invalidate_account_codes(db, uid)
                return self.reply(200,{})
            if path=='/api/workspace' and self.command=='GET':
                rows=db.execute('SELECT name,value,version FROM workspace WHERE user_id=?',(uid,)).fetchall()
                return self.reply(200,{r['name']:dict(value=json.loads(r['value']),version=r['version']) for r in rows})
            if path=='/api/data/tasks' and self.command=='DELETE':
                ids=body.get('ids')
                if not isinstance(ids,list) or not ids or len(ids)>500 or any(not isinstance(item,str) or not item or len(item)>100 for item in ids) or len(ids)!=len(set(ids)): raise ApiError(400,'删除任务范围不正确')
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT value,version FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
                version=row['version'] if row else 0
                if body.get('version')!=version: raise ApiError(409,'数据已在其他窗口修改，请刷新后重试')
                tasks=json.loads(row['value']) if row else []
                existing={task['id'] for task in tasks}
                if any(item not in existing for item in ids): raise ApiError(404,'要删除的任务不存在')
                selected=set(ids);tasks=[task for task in tasks if task['id'] not in selected]
                for task in tasks:
                    if task.get('dependsOn') in selected: task['dependsOn']=''
                next_version=version+1
                db.execute('INSERT INTO workspace VALUES(?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value,version=excluded.version',(uid,'tasks',json.dumps(tasks),next_version))
                feedback_row=db.execute('SELECT value,version FROM workspace WHERE user_id=? AND name="feedback"',(uid,)).fetchone()
                feedback_version=None;feedback=None
                if feedback_row:
                    feedback=json.loads(feedback_row['value']);filtered=[item for item in feedback if item.get('taskId') not in selected]
                    feedback_version=feedback_row['version']
                    if len(filtered)!=len(feedback):
                        feedback=filtered;feedback_version+=1
                        db.execute('UPDATE workspace SET value=?,version=? WHERE user_id=? AND name="feedback"',(json.dumps(feedback),feedback_version,uid))
                return self.reply(200,dict(deletedIds=ids,version=next_version,feedback=feedback,feedbackVersion=feedback_version))
            if path.startswith('/api/data/') and self.command=='PUT':
                name=path.rsplit('/',1)[1]
                if name not in ['tasks','candidates','availability','feedback','preferences','courses','focus','materials','sources']: raise ApiError(404,'未知数据类型')
                value=body.get('value')
                if not isinstance(value,dict if name in ['availability','focus','preferences'] else list): raise ApiError(400,'数据格式不正确')
                if name=='preferences' and (set(value)!=set(['askCompletionMinutes']) or type(value['askCompletionMinutes'])!=bool): raise ApiError(400,'个性化设置格式不正确')
                if name=='sources' and (len(value)>1 or any(source!='teaching_web' for source in value)): raise ApiError(400,'来源选择不正确')
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
                        if task.get('content_type','task') not in ('task','notice'): raise ApiError(400,'内容类型必须是任务或通知')
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
                if name=='tasks':
                    old=db.execute('SELECT value FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
                    self.usage_tasks=json.loads(old['value']) if old else []
                    submission_runtime.manual_task_write(db,uid,value)
                db.execute('INSERT INTO workspace VALUES(?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value,version=excluded.version',(uid,name,json.dumps(value),version+1))
                return self.reply(200,dict(version=version+1))
            if path=='/api/planning/proposals' and self.command=='POST':
                rows={r['name']:r for r in db.execute('SELECT name,value,version FROM workspace WHERE user_id=?',(uid,))}
                tasks=json.loads(rows['tasks']['value']) if 'tasks' in rows else []
                replan=body.get('replan',False)
                if type(replan)!=bool: raise ApiError(400,'重新规划选项不正确')
                task_ids=body.get('taskIds')
                if task_ids is not None:
                    if not isinstance(task_ids,list) or not task_ids or any(not isinstance(item,str) for item in task_ids) or len(set(task_ids))!=len(task_ids): raise ApiError(400,'规划任务范围不正确')
                    if replan and any(not any(t['id']==tid and t['status'] not in ['completed','cancelled'] for t in tasks) for tid in task_ids): raise ApiError(400,'要重新规划的任务不存在或已经完成')
                pending=[t for t in tasks if t.get('content_type','task')!='notice' and t['status'] not in ['completed','cancelled'] and
                         (not t.get('planningExcluded') or replan and task_ids is not None) and (replan or not t['plans'])]
                if task_ids is not None:
                    pending=[t for t in pending if t['id'] in task_ids and t.get('due')]
                if not pending: raise ApiError(400,'没有需要规划的任务')
                now=datetime.now(TZ)
                replace_plans={t['id']:[p['id'] for p in t['plans'] if datetime.fromisoformat(p['startAt'])>now] for t in pending if replan and t.get('due')}
                existing=[dict(p,taskId=t['id']) for t in tasks if t['status']!='cancelled' for p in t['plans']
                          if p['id'] not in replace_plans.get(t['id'],[]) and (t['status']!='completed' or datetime.fromisoformat(p['startAt'])<=now)]
                pending=[dict(t,plans=[p for p in t['plans'] if p['id'] not in replace_plans.get(t['id'],[])]) for t in pending]
                payload=dict(schemaVersion='2.0',requestId=secrets.token_hex(16),requestedAt=now.isoformat(),timezone='Asia/Shanghai',tasks=pending,existingPlans=existing,availability=json.loads(rows['availability']['value']) if 'availability' in rows else dict(startTime='19:00',endTime='22:00',weekdays=[1,2,3,4,5]),completionHistory=json.loads(rows['feedback']['value']) if 'feedback' in rows else [])
                payload['completionHistory']=[dict(f,task=next(t for t in tasks if t['id']==f['taskId'])) for f in payload['completionHistory'] if any(t['id']==f['taskId'] for t in tasks)]
                for task in payload['tasks']:
                    task.pop('estimatedMinutes',None)
                result=planning.plan(payload,ApiError)
                try:
                    scheduled={block['taskId'] for block in result.get('blocks',[])}
                    payload['existingPlans'].extend(dict(plan,taskId=task['id']) for task in tasks if task['id'] not in scheduled for plan in task['plans'] if plan['id'] in replace_plans.get(task['id'],[]))
                    validate_proposal(payload,result)
                except (ValueError,KeyError,TypeError,AttributeError):
                    raise ApiError(502,'模型返回的规划格式不正确；任务未修改') from None
                result['priorityOrder']=planning.priority_explanations(payload,result['estimates'])
                result['requestId']=payload['requestId']
                replace_plans={tid:ids for tid,ids in replace_plans.items() if ids and tid in scheduled}
                result['replannedTaskIds']=list(replace_plans)
                db.execute('INSERT INTO proposals VALUES(?,?,?,?,0)',(payload['requestId'],uid,rows['tasks']['version'],json.dumps(dict(result,replacePlanIds=replace_plans))))
                return self.reply(200,result)
            if path=='/api/planning/apply' and self.command=='POST':
                db.execute('BEGIN IMMEDIATE')
                proposal=db.execute('SELECT * FROM proposals WHERE id=? AND user_id=?',(body.get('requestId'),uid)).fetchone()
                row=db.execute('SELECT * FROM workspace WHERE user_id=? AND name="tasks"',(uid,)).fetchone()
                if not proposal or not row: raise ApiError(404,'规划不存在')
                if proposal['applied']: raise ApiError(409,'规划已应用，请刷新')
                if row['version']!=proposal['version']: raise ApiError(409,'任务已变化，请重新规划')
                self.usage_tasks=json.loads(row['value'])
                tasks=json.loads(row['value'])
                result=json.loads(proposal['result'])
                for task in tasks:
                    if any(plan['id'] in result.get('replacePlanIds',{}).get(task['id'],[]) and datetime.fromisoformat(plan['startAt'])<=datetime.now(TZ) for plan in task['plans']): raise ApiError(409,'原安排已经开始，请重新生成提案')
                for task in tasks:
                    task['plans']=[plan for plan in task['plans'] if plan['id'] not in result.get('replacePlanIds',{}).get(task['id'],[])]
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
    def do_DELETE(self): self.handle_request()
    def handle_request(self):
        with operations.scope(DB):self._handle_request()

    def _handle_request(self):
        self.usage_started=time.monotonic()
        self.usage_uid=None
        self.usage_recorded=False
        self.usage_tasks=None
        self.ops_status=500
        self.ops_code='ok'
        self.retry_after=None
        try:
            with model_queue.request(self.connection): self.route()
        except wechat_media.MediaError as e:
            self.ops_code=operations.classify(e)
            self.reply(e.status,dict(error=dict(code=e.code,message=e.message,retryable=e.retryable)))
        except email_verification.VerificationError as e:
            self.ops_code=operations.classify(e)
            self.retry_after=e.retry_after
            value=dict(error=e.message, code=e.code)
            if e.retry_after is not None: value['retryAfter']=e.retry_after
            self.reply(e.status, value)
        except ApiError as e:
            self.ops_code=operations.classify(e)
            if self.path.startswith('/api/sources/submissions'):
                code={401:'unauthorized',403:'unauthorized',404:'not_found',409:'conflict',503:'unavailable'}.get(e.status,'invalid_request')
                self.reply(e.status,dict(contract_version='submission-operations/0.1',code=code,message=e.message))
            elif self.path.startswith(('/api/sources/wechat/','/api/materials/')):
                code={401:'unauthorized',403:'forbidden',404:'not_found',413:'limit_exceeded',503:'queue_busy'}.get(e.status,'invalid_request')
                self.reply(e.status,dict(error=dict(code=code,message=e.message,retryable=e.status==503)))
            else: self.reply(e.status,dict(error=e.message))
        except (ValueError,KeyError,TypeError):
            self.ops_code='http_400'
            if self.path.startswith(('/api/sources/wechat/','/api/materials/')):
                self.reply(400,dict(error=dict(code='invalid_request',message='数据格式不正确',retryable=False)))
            else: self.reply(400,dict(error='数据格式不正确'))
        except Exception as e:
            self.ops_code=operations.classify(e)
            if self.path.startswith(('/api/sources/wechat/','/api/materials/')):
                self.reply(503,dict(error=dict(code='queue_busy',message='附件服务暂不可用，请稍后重试',retryable=True)))
            else:
                self.reply(502,dict(error='服务处理失败，请稍后重试；数据未变更'))
        finally:
            if getattr(self,'db',None) is not None: self.db.close()
            operation=operations.operation(self.path,self.command)
            if operation:
                elapsed=(time.monotonic()-self.usage_started)*1000
                operations.emit('http',elapsed,operation=operation,result='ok' if self.ops_status<400 and self.ops_code=='ok' else 'failed',code=self.ops_code)
                context=operations.captured()
                print(json.dumps(dict(event='http',request_id=context['request_id'],operation=operation,
                    status=self.ops_status,code=self.ops_code,duration_ms=round(elapsed,2),environment=runtime_environment.environment(),revision=runtime_environment.revision())),flush=True)

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
        title=tasks[tid]['title']
        if totals[tid]<estimates[tid] and tasks[tid].get('due') and not any(w.startswith(title+'：') for w in result['warnings']):
            result['warnings'].append(title+'：未全部安排，请检查可用时间或截止时间')

if __name__=='__main__':
    import sys
    if '--seed' in sys.argv: seed()
    else:
        host=os.environ.get('NIUMA_HOST','127.0.0.1')
        port=runtime_environment.port()
        with ThreadingHTTPServer((host,port),Handler) as server:
            initialize()
            scheduler_stop=source_schedule.start_worker(DB,ApiError)
            print('牛马管理器 API: http://'+host+':'+str(port)+' ['+runtime_environment.environment()+']',flush=True)
            try: server.serve_forever()
            finally: scheduler_stop.set()
