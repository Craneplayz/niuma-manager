"""Read-only administrator benchmark reports; originals and credentials are never served."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit,parse_qs


def route(handler,path,ApiError):
    prefix='/api/admin/evaluations'
    if path!=prefix and not path.startswith(prefix+'/'):return False
    if handler.command!='GET':raise ApiError(405,'评测结果仅支持读取')
    root=Path(os.environ.get('NIUMA_BENCHMARK_DIR',handler.media_db_path.parent/'model-evaluations'))
    name=path[len(prefix):].strip('/')
    if name and not re.fullmatch(r'[0-9a-f]{64}',name):raise ApiError(400,'评测编号不正确')
    def read(file):
        if file.is_symlink()or file.stat().st_size>30_000_000:raise ApiError(400,'评测报告不可读取')
        value=json.loads(file.read_text())
        if value.get('version')!='benchmark-report/1'or value.get('id')!=file.stem:raise ApiError(400,'评测报告格式不正确')
        return value
    try:
        if name:
            file=root/(name+'.json')
            if not file.is_file():raise ApiError(404,'评测结果不存在')
            result=read(file)
            query=parse_qs(urlsplit(handler.path).query)
            if 'case' in query or 'model' in query:
                if any(len(query.get(k,[]))!=1 for k in ('case','model')):raise ApiError(400,'请指定唯一的样例和模型')
                row=next((r for r in result.get('rows',[])if r.get('id')==query['case'][0] and r.get('model')==query['model'][0]),None)
                if row is None:raise ApiError(404,'样例不存在')
                result=dict(row=row)
            elif query.get('full')!=['1']:
                result=dict(result,rows=[{k:v for k,v in r.items()if k not in ('input','expected','output','call_usage')}for r in result.get('rows',[])])
        else:
            result=dict(runs=[dict(id=r['id'],sourceVersion=r['sourceVersion'],datasetHash=r['datasetHash'],phase=r.get('phase'),runStartedAt=r.get('runStartedAt'),summary=r['summary'])for r in sorted([read(f)for f in root.glob('*.json')],key=lambda r:r.get('runStartedAt',''))])
    except (ValueError,OSError,KeyError,TypeError):raise ApiError(503,'暂时无法读取评测结果')from None
    handler.reply(200,result);return True
