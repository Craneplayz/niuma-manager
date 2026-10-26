"""Review regressions: pure contract checks, no network, files or account data."""
import copy
import json
import unittest

from jsonschema import Draft202012Validator, ValidationError
from contracts import check_v031_examples as check
from contracts.build_v031_bundle import build, ENTRIES


def read(path): return json.loads((check.ROOT / path).read_text(encoding='utf-8'))


CASES = read('examples/text-attachments-v031-cases.json')
BY_NAME = {c['name']: c for c in CASES}
MAIN = dict(request=read('source-to-text/text-attachments-v031-request.json'),
            expected=read('text-to-app/text-attachments-v031-output.json'))
JOBS = read('source-to-app/wechat-import-v031-examples.json')
ASSET = read('source-to-app/wechat-asset-v031-example.json')
ERRORS = (ValidationError, ValueError)


def changed(value, path, replacement):
    value = copy.deepcopy(value); node = value
    for key in path[:-1]: node = node[key]
    node[path[-1]] = replacement
    return value


def timeout_case():
    c = copy.deepcopy(MAIN); out = c['expected']; rid = c['request']['records'][0]['input_id']
    out.update(status='failed', tasks=[], errors=[dict(input_id=rid, asset_id=None, block_id=None,
               stage='recognize', code='agent_timeout', message='模型调用超时。', retryable=True)])
    out['record_results'][0].update(status='failed', task_ids=[], coverage=dict(complete=False, uncertain=False))
    return c


def as_job(case):
    job = copy.deepcopy(JOBS[-1]); result = case['expected']
    job.update(request_id=case['request']['request_id'], result=result,
               status={'success':'succeeded', 'no_tasks':'succeeded', 'partial_success':'partial_failed', 'failed':'failed'}[result['status']])
    total = len(case['request']['records'])
    job['progress'] = dict(total_records=total, finished_records=total,
                          failed_records=sum(r['status'] in ('failed', 'partial_success') for r in result['record_results']))
    return job


class V031ContractTests(unittest.TestCase):
    def test_all_examples_and_generated_bundle(self):
        self.assertEqual(build(), check.BUNDLE)
        check.main()

    def test_seven_root_schemas_need_no_registry_and_reject_empty_objects(self):
        examples = dict(request=MAIN['request'], result=MAIN['expected'], asset_ack=ASSET, job=JOBS[-1],
                        ack={k:JOBS[0][k] for k in ('contract_version','job_id','request_id','status')},
                        error={'error':dict(code='asset_metadata_mismatch',message='附件元数据不一致。',retryable=False)},
                        retry={'retry_id':'retry-001'})
        for entry, example in examples.items():
            with self.subTest(entry=entry):
                validator = Draft202012Validator(check.root_schema(entry), format_checker=check.FORMATS)
                validator.validate(example)
                Draft202012Validator(check.BUNDLE, format_checker=check.FORMATS).validate(example)
                with self.assertRaises(ValidationError): validator.validate({})
        def refs(v):
            if isinstance(v,dict):
                for k,x in v.items():
                    if k=='$ref': self.assertTrue(x.startswith('#/'))
                    refs(x)
            elif isinstance(v,list):
                for x in v: refs(x)
        refs(check.BUNDLE)

    def test_version_is_explicit_and_original_examples_are_unchanged(self):
        with self.assertRaises(ValidationError): check.check_request(changed(MAIN['request'],('contract_version',),'0.3'))
        with self.assertRaises(ValidationError): check.check_pair(MAIN['request'],changed(MAIN['expected'],('contract_version',),'0.3'))
        self.assertEqual(read('source-to-text/text-attachments-v03-request.json')['contract_version'],'0.3')
        self.assertFalse(read('text-to-app/text-attachments-v03-output.json')['tasks'][0]['needs_review'])

    def test_ids_are_unique_in_their_declared_scope(self):
        source = BY_NAME['preextracted-image-with-context']['request']
        r = ('records',0); a = r+('attachments',0)
        changes = [(('records',),source['records']*2), (r+('attachments',),source['records'][0]['attachments']*2),
                   (a+('blocks',),source['records'][0]['attachments'][0]['blocks']*2),
                   (r+('context',),source['records'][0]['context']*2)]
        for path,value in changes:
            with self.subTest(path=path),self.assertRaises(ERRORS): check.check_request(changed(source,path,value))
        req=copy.deepcopy(source); second=copy.deepcopy(req['records'][0]);second['input_id']='second'
        req['records'].append(second)
        check.check_request(req)  # Reusing asset/block IDs under another record is valid.
        second['context'][0]['text']='conflicting content'
        with self.assertRaisesRegex(ValueError,'conflicting_context'): check.check_request(req)

    def test_foreign_or_missing_evidence_is_rejected(self):
        e=('tasks',0,'evidence',1)
        for key,value in [('asset_id','foreign'),('input_id','foreign'),('field','attachment')]:
            with self.subTest(key=key),self.assertRaises(ERRORS):
                check.check_pair(MAIN['request'],changed(MAIN['expected'],e+(key,),value))
        c=BY_NAME['preextracted-image-with-context']
        with self.assertRaisesRegex(ValueError,'unknown_block'):
            check.check_pair(c['request'],changed(c['expected'],e+('block_id',),'foreign'))

    def test_model_reported_evidence_always_requires_review_even_when_clear(self):
        result=copy.deepcopy(MAIN['expected']);result['tasks'][0].update(needs_review=False,review_reasons=[])
        # Also enforced by the independent result schema, before semantic checks.
        with self.assertRaises(ValidationError):check.schema('result',result)
        with self.assertRaises(ERRORS):check.check_pair(MAIN['request'],result)
        for case in CASES:
            if any(not r['coverage']['complete'] or r['coverage']['uncertain'] for r in case['expected']['record_results']) and case['expected']['tasks']:
                result=copy.deepcopy(case['expected']);result['tasks'][0].update(needs_review=False,review_reasons=[])
                with self.subTest(case=case['name']),self.assertRaises(ERRORS):check.check_pair(case['request'],result)

    def test_public_stable_urls_only(self):
        good=['https://course.pku.edu.cn/webapps/blackboard/execute/announcement?method=search&context=course_entry&course_id=_1&handle=announcements_entry&mode=view',
              'https://example.test/notice/123','http://8.8.8.8/notice','https://[2001:4860:4860::8888]/notice']
        for value in good:check.check_source_url(value)
        bad=['file:///tmp/a','http://localhost/a','http://printer/a','http://x.local/a',
             'http://127.0.0.1/a','http://10.0.0.1/a','http://172.16.1.1/a','http://192.168.0.1/a',
             'http://169.254.169.254/a','http://100.64.0.1/a','http://[::1]/a','http://[fc00::1]/a',
             'http://[::ffff:127.0.0.1]/a','http://2130706433/a','http://127.1/a','http://0x7f.0.0.1/a',
             'https://user:pass@example.test/a','https://@example.test/a','https://example.test:8443/a',
             'https://example.test/a?token=secret','https://example.test/a?X-Amz-Signature=secret',
             'https://example.test/a?%73ig=secret','https://example.test/a?expires=123',
             'https://example.test/a?unknown=secret','https://example.test/a#token=secret',
             'https://example.test/a?id=1&id=2','https://example.test/a%0d%0aHeader',
             'https://example.test/\na','https://example.test\\@localhost/a']
        for value in bad:
            with self.subTest(url=value),self.assertRaises(ValueError):check.check_source_url(value)
        with self.assertRaises(ERRORS):check.check_request(changed(MAIN['request'],('records',0,'context',0,'source_url'),bad[1]))

    def test_filename_boundaries_apply_to_requests_and_upload_receipts(self):
        for name in ['../a.png','..','.',r'C:\private\a.png','a/b.png','a\x00.png','a\n.png',
                     'a\u202eexe.png','%2e%2e%2fa.png','%252e%252e.png','a.png:stream','CON.png','LPT1','a.','a ','a'*256]:
            with self.subTest(name=name),self.assertRaises(ERRORS):check.check_asset_ack(dict(ASSET,file_name=name))
            with self.subTest(request=name),self.assertRaises(ERRORS):check.check_request(changed(MAIN['request'],('records',0,'attachments',0,'file_name'),name))
        check.check_asset_ack(dict(ASSET,file_name='课程说明（最终版）.png'))

    def test_public_looking_host_cannot_be_fetched_using_private_dns_answers(self):
        check.check_resolved_source_url('https://example.test/a',['8.8.8.8','2001:4860:4860::8888'])
        for answers in [[],['10.0.0.1'],['8.8.8.8','127.0.0.1'],['::ffff:192.168.1.1'],['bad-address']]:
            with self.subTest(answers=answers),self.assertRaises(ValueError):
                check.check_resolved_source_url('https://example.test/a',answers)

    def test_metadata_and_error_bounds(self):
        for key,value in [('source_label','a'*513),('source_item_id','a'*257),('source_url','https://example.test/'+'a'*2048)]:
            with self.subTest(key=key),self.assertRaises(ERRORS):check.check_request(changed(MAIN['request'],('records',0,key),value))
        check.check_request(changed(MAIN['request'],('records',0,'source_label'),'a'*512))
        failed=timeout_case()
        for path,value in [(('errors',),failed['expected']['errors']*201),(('errors',0,'message'),'a'*1025),
                           (('record_results',0,'warnings'),['a']*21)]:
            with self.subTest(path=path),self.assertRaises(ERRORS):check.check_pair(failed['request'],changed(failed['expected'],path,value))

    def test_hint_depth_size_width_and_nodes(self):
        good={'raw':{'message':'真实原文'},'other':[1,False,None]}
        check.check_request(changed(MAIN['request'],('records',0,'upstream_hints'),good))
        bad=[{'a':{'b':{'c':{'d':{}}}}},{'a'*65:1},{str(i):i for i in range(33)},
             {'a':'x'*4097},{str(i):'x'*4096 for i in range(5)},
             {str(i):list(range(32)) for i in range(8)},{'a':float('nan')}]
        for value in bad:
            with self.subTest(value=str(value)[:50]),self.assertRaises(ERRORS):
                check.check_request(changed(MAIN['request'],('records',0,'upstream_hints'),value))

    def test_iana_timezone_and_basis(self):
        for value in ['CST','+08:00','Asia/NotAPlace']:
            with self.subTest(zone=value),self.assertRaises((ValueError,KeyError)):
                check.check_request(changed(MAIN['request'],('records',0,'timezone'),value))
        with self.assertRaises(ERRORS):check.check_request(changed(MAIN['request'],('records',0,'timezone_basis'),'unknown'))

    def test_job_record_batch_and_progress_must_agree(self):
        for path,value in [(('status',),'failed'),(('progress','finished_records'),2),
                           (('progress','failed_records'),2),(('progress','finished_records'),0),
                           (('result','status'),'no_tasks'),(('result','record_results',0,'status'),'no_tasks')]:
            with self.subTest(path=path),self.assertRaises(ERRORS):check.check_job(MAIN['request'],changed(JOBS[-1],path,value))
        with self.assertRaises(ERRORS):check.check_job(MAIN['request'],changed(JOBS[0],('result',),MAIN['expected']))

    def test_unsupported_formats_are_retained_and_never_no_tasks(self):
        for ext in ['gif','webp','mp3','mp4','pptx','xlsx','zip']:
            case=BY_NAME['unsupported-'+ext];check.check_pair(case['request'],case['expected'])
            bad=copy.deepcopy(case['request']);bad['records'][0]['attachments'][0]['status']='failed'
            with self.subTest(ext=ext),self.assertRaisesRegex(ValueError,'unsupported_content_not_reported'):check.check_request(bad)

    def test_idempotency_compares_canonical_content_without_mutating_it(self):
        request=copy.deepcopy(MAIN['request'])
        reordered=dict(reversed(list(request.items())))
        check.check_replay(request,reordered)
        with self.assertRaisesRegex(ValueError,'request_conflict'):
            check.check_replay(request,changed(request,('records',0,'source_label'),'另一个来源'))
        self.assertEqual(request,MAIN['request'])

    def test_hash_mime_size_mismatch_has_one_standard_error(self):
        asset=MAIN['request']['records'][0]['attachments'][0]
        check.check_asset_metadata(asset,ASSET)
        for key,value in [('sha256','b'*64),('mime_type','image/jpeg'),('size_bytes',99),('file_ref','asset:other_reference_001')]:
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'asset_metadata_mismatch'):
                check.check_asset_metadata(dict(asset,**{key:value}),ASSET)

    def test_job_lifecycle_and_retry_requires_eligible_failure(self):
        req=MAIN['request'];check.check_job_transition(req,JOBS[0],JOBS[1]);check.check_job_transition(req,JOBS[1],JOBS[-1])
        check.check_job_transition(req,JOBS[-1],copy.deepcopy(JOBS[-1]))
        with self.assertRaisesRegex(ValueError,'terminal_result_changed'):check.check_job_transition(req,JOBS[-1],JOBS[0])
        failure=as_job(timeout_case())
        check.check_job_transition(req,failure,JOBS[0],retry_id='retry-001',retry_record_ids=[req['records'][0]['input_id']])
        with self.assertRaisesRegex(ValueError,'invalid_job_state'):
            check.check_job_transition(req,JOBS[1],JOBS[0],retry_id='retry-001',retry_record_ids=[req['records'][0]['input_id']])
        missing=as_job(BY_NAME['file-reference-unavailable'])
        with self.assertRaisesRegex(ValueError,'invalid_job_state'):
            check.check_job_transition(req,missing,JOBS[0],retry_id='retry-001',retry_record_ids=[req['records'][0]['input_id']])

    def test_retry_preserves_successful_records_and_candidates(self):
        def rename(value):
            if isinstance(value,str):return 'second' if value=='wechat:message:002' else value
            if isinstance(value,list):return [rename(v) for v in value]
            if isinstance(value,dict):return {k:rename(v) for k,v in value.items()}
            return value
        failed=rename(timeout_case()); mixed=copy.deepcopy(MAIN)
        mixed['request']['records']+=failed['request']['records']
        for key in ('record_results','errors'):mixed['expected'][key]+=failed['expected'][key]
        mixed['expected']['status']='partial_success'
        before=as_job(mixed);queued=copy.deepcopy(JOBS[0]);queued['progress'].update(total_records=2,finished_records=1)
        check.check_job_transition(mixed['request'],before,queued,retry_id='retry-001',retry_record_ids=['second'])
        bad=copy.deepcopy(queued);bad['progress']['finished_records']=0
        with self.assertRaisesRegex(ValueError,'retry_progress_mismatch'):
            check.check_job_transition(mixed['request'],before,bad,retry_id='retry-001',retry_record_ids=['second'])
        recovered=rename(copy.deepcopy(MAIN['expected']));recovered['tasks'][0]['candidate_id']='second-task'
        recovered['record_results'][0]['task_ids']=['second-task']
        after=copy.deepcopy(MAIN['expected'])
        for key in ('record_results','tasks'):after[key]+=recovered[key]
        check.check_retry_result(mixed['request'],mixed['expected'],after,['second'])
        after['tasks'][0]['description']='偷偷覆盖旧候选'
        with self.assertRaisesRegex(ValueError,'successful_candidate_changed'):
            check.check_retry_result(mixed['request'],mixed['expected'],after,['second'])


if __name__=='__main__':unittest.main()
