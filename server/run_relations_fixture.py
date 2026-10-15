"""Disposable browser fixture: real HTTP/database, synthetic extraction and relations."""
import argparse
import json
import os
from pathlib import Path
import time
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--ready-file',type=Path,required=True);args=parser.parse_args()
    for key in ('NIUMA_ENVIRONMENT','NIUMA_DATA_DIR','NIUMA_INSTANCE_ID','NIUMA_DB'):os.environ.pop(key,None)
    os.environ.update(NIUMA_ENABLE_WECHAT='0',NIUMA_COOKIE_SECURE='0')
    import app,teaching,source_jobs,task_relations
    from test_task_relations import TaskRelationsTests
    from ddl_text.tests.test_teaching_pipeline import example,RecordClient
    from ddl_text.teaching_pipeline import process_teaching_request
    from ddl_text.config import Settings
    from contracts.teaching_io import snapshot_digest
    from contracts.task_relations import VERSION
    fixture=TaskRelationsTests();fixture.setUp()
    try:
        fixture.seed('submitted')
        with app.connection() as db:
            for row in db.execute('SELECT * FROM recognition_batches').fetchall():
                request=json.loads(row['request']);record=request['records'][0]['record']
                _,drafts=example();draft=json.loads(next(iter(drafts.values())))['tasks'][0]
                draft.update(title=record['text'].split('：')[0],description=record['text'])
                draft['assessment']['action']['evidence']=[dict(part_id='body',quote=record['text'])]
                draft['assessment']['deadline']=dict(value=None,precision='unknown',derivation='not_provided',anchor=None,evidence=[])
                result=process_teaching_request(request,client=RecordClient({record['input_id']:json.dumps(dict(tasks=[draft]))}),
                    settings=Settings(timeout_seconds=10),verified_snapshot_digests={snapshot_digest(e) for e in request['records']})
                if result['status']!='success':raise RuntimeError('synthetic extraction failed')
                db.execute('UPDATE recognition_batches SET result=? WHERE request_id=?',(json.dumps(result),row['request_id']))
                card=teaching.result_view(request,result)['tasks'][0]
                db.execute('UPDATE recognition_candidates SET candidate=? WHERE request_id=?',(json.dumps(card),row['request_id']))
        def answer(request):
            time.sleep(.4)
            _,group=fixture.proposal()
            return dict(contract_version=VERSION,context_digest=request['context_digest'],groups=[group])
        with patch.object(app.source_sync,'collect',return_value=dict(requests=[],summary=dict(skipped=3))),patch.object(task_relations,'model',side_effect=answer):
            args.ready_file.write_text(json.dumps(dict(port=fixture.server.server_port,email='teaching@example.test',password='Synthetic-password!')),encoding='utf-8')
            while not args.ready_file.with_suffix('.stop').exists():time.sleep(.2)
    finally:
        source_jobs.wait_for_jobs(app.DB);task_relations.wait_for_jobs(app.DB);fixture.tearDown();fixture.doCleanups()


if __name__=='__main__':main()
