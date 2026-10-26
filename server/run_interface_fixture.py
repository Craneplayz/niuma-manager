"""Disposable HTTP fixture for the real browser smoke; never calls external services."""
import argparse
import json
import os
import signal
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--ready-file',type=Path,required=True)
    args=parser.parse_args()
    os.environ.update(NIUMA_ENABLE_WECHAT='0',NIUMA_ENABLE_MANUAL_MATERIALS='1',NIUMA_ENABLE_SCHEDULED_SYNC='1',NIUMA_COOKIE_SECURE='0')
    # The fixture owns only a newly allocated temporary DB, regardless of shell config.
    for key in ('NIUMA_ENVIRONMENT','NIUMA_DATA_DIR','NIUMA_INSTANCE_ID','NIUMA_DB'):
        os.environ.pop(key,None)
    import app
    import teaching
    import source_jobs
    import wechat_media
    import public_sources
    import reprocessing
    import test_teaching
    from ddl_text.tests.test_teaching_pipeline import example
    from ddl_text.tests.media_cases import image_bytes,FakeMediaClient
    from ddl_text.media_pipeline import process_media_request
    from ddl_text.config import Settings
    from test_public_sources import capture
    fixture=test_teaching.TeachingIntegrationTests()
    fixture.setUp()
    request,drafts=example(('explicit-complete',)*2);fixture.drafts.update(drafts)
    captures=[]
    for entry in request['records']:
        r=entry['record']
        captures.append(dict(contract_version='teaching-capture/0.1',request=dict(request,records=[entry]),
            assignment=dict(state='active',basis='active_assignment_page',course_id=r['course_id'],content_id=r['source_item_id'])))
    attempts={}
    def collect(db,uid,ApiError,key_path,guard=None):
        if guard:guard(db)
        return teaching.register(db,uid,'synthetic-course-user',captures)
    def model(body,ApiError,**kwargs):
        identity=body['records'][0]['record']['input_id'];attempts[identity]=attempts.get(identity,0)+1
        time.sleep(.8)
        if identity==request['records'][1]['record']['input_id'] and attempts[identity]==1:
            raise ApiError(502,'合成的单次模型失败')
        return fixture.model(body,ApiError)
    material_attempts=0
    def media(body,assets,**kwargs):
        nonlocal material_attempts
        material_attempts+=1
        if material_attempts==1:return wechat_media._failed_result(body)
        record=body['records'][0];asset=record['attachments'][0]
        draft=dict(tasks=[dict(title='合成图片作业',description='阅读图片中的合成作业说明。',prerequisites=[],
            course_or_project=None,course_basis='unknown',notice_type='create',target_reference=None,
            deadline=dict(value=None,precision='unknown',timezone='Asia/Shanghai',kind='unknown',status='not_provided',raw=None,basis='图片未指定截止日期。',alternatives=[]),
            review_reasons=[],evidence=[dict(input_id=record['input_id'],field='attachment_file',asset_id=asset['asset_id'],
                quote='Synthetic assignment',supports=['title','description'],locator=dict(page=1,paragraph=None,bbox=None),verification='model_reported')])],
            image_quality=[dict(asset_id=asset['asset_id'],quality='normal',quality_notes=[])])
        result=process_media_request(body,assets,FakeMediaClient(draft),Settings(timeout_seconds=10,max_retries=0))
        if result['status']=='failed':
            import sys
            print('Synthetic media failure: '+json.dumps(result,ensure_ascii=False),file=sys.stderr,flush=True)
        return result
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    try:
        with ExitStack() as patches:
            patches.enter_context(patch.object(app.source_sync,'collect',side_effect=collect))
            patches.enter_context(patch.object(teaching,'recognize',side_effect=model))
            patches.enter_context(patch.object(public_sources,'collect',return_value=capture()))
            patches.enter_context(patch.object(wechat_media,'recognize_v03',side_effect=media))
            image=Path(fixture.tmp.name)/'synthetic.png'
            image.write_bytes(image_bytes(size=(600,150),text='Synthetic assignment'))
            args.ready_file.write_text(json.dumps(dict(port=fixture.server.server_port,image=str(image),
                email='teaching@example.test',password='Synthetic-password!')),encoding='utf-8')
            while not stop.wait(.2):
                if args.ready_file.with_suffix('.stop').exists():break
    finally:
        source_jobs.wait_for_jobs(app.DB);wechat_media.wait_for_jobs(app.DB);reprocessing.wait_for_jobs(app.DB)
        fixture.tearDown()


if __name__=='__main__':main()
