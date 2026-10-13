"""Session-only user uploads; never issue or accept collector credentials."""
import json
import os
import wechat_media as media


def enabled():
    return os.environ.get('NIUMA_ENABLE_MANUAL_MATERIALS','0')=='1'


def initialize(db,recover=False):
    db.execute('CREATE TABLE IF NOT EXISTS manual_material_jobs(job_id TEXT PRIMARY KEY)')
    if recover:
        for row in db.execute("SELECT * FROM wechat_jobs WHERE id IN(SELECT job_id FROM manual_material_jobs) AND status IN ('queued','running')").fetchall():
            result=json.loads(row['previous_result']) if row['previous_result'] else media._failed_result(json.loads(row['request']),'agent_unavailable','服务重启中断了处理，请手动重试；未自动重复调用模型。')
            status='partial_failed' if result['status']=='partial_success' else 'succeeded' if result['status'] in ('success','no_tasks') else 'failed'
            db.execute('UPDATE wechat_jobs SET status=?,result=?,previous_result=NULL WHERE id=?',(status,json.dumps(result),row['id']))


def route(handler,db,uid,path,body,data_dir):
    if not path.startswith('/api/materials/'):return False
    if not enabled() or handler.headers.get('Authorization'):
        raise media.MediaError(403,'forbidden','此入口仅接受已登录用户主动上传的材料')
    suffix=path[len('/api/materials/'):]
    if suffix=='limits' and handler.command=='GET':
        handler.reply(200,dict(configured=True,limits=dict(asset_bytes=media.MAX_ASSET,account_bytes=200*1024*1024,unreferenced_ttl_seconds=media._asset_ttl())))
        return True
    if not (suffix in ('assets','imports') or suffix.startswith(('assets/','imports/'))):
        raise media.MediaError(404,'not_found','材料接口不存在')
    if suffix.startswith('imports/'):
        identity=suffix.split('/')[1]
        if not db.execute('SELECT 1 FROM manual_material_jobs m JOIN wechat_jobs j ON j.id=m.job_id WHERE m.job_id=? AND j.user_id=?',(identity,uid)).fetchone():
            raise media.MediaError(404,'not_found','材料任务不存在')
    if suffix=='imports' and handler.command=='POST':
        try:media.check_request(body)
        except media.ValidationError as error:
            if error.validator in ('maxItems','maxLength','maxProperties','maximum'):
                raise media.MediaError(413,'limit_exceeded','材料超过字段或数量限制') from None
            raise media.MediaError(400,'invalid_request','材料导入请求格式不正确') from None
        except (ValueError,KeyError,TypeError,RecursionError):
            raise media.MediaError(400,'invalid_request','材料导入请求格式不正确') from None
        for record in body['records']:
            for asset in record['attachments']:
                if not asset.get('file_ref'):continue
                saved=db.execute('SELECT * FROM wechat_assets WHERE ref=? AND user_id=?',(asset['file_ref'],uid)).fetchone()
                if not saved:raise media.MediaError(404,'not_found','附件不存在或不属于当前账号')
                if any(saved[key]!=asset.get(key) for key in ('file_name','mime_type','size_bytes','sha256')):
                    raise media.MediaError(400,'asset_metadata_mismatch','附件回执与原件不一致')
    handler.user_materials=True
    try:return media.route(handler,db,uid,'/api/sources/wechat/'+suffix,body,data_dir)
    finally:del handler.user_materials
