"""Synthetic policy fixtures derived from the single shared draft contract."""
import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_CASES = json.loads((ROOT / 'contracts/examples/teaching-review-v0.4-draft-cases.json').read_text(
    encoding='utf-8'))['cases']


def policy_case(name='unread-supplement'):
    return deepcopy(next(c for c in POLICY_CASES if c['name'] == name))


def model_task(pair, recommendation='task', reason=''):
    assessment = deepcopy(pair['assessment'])
    for key in ('candidate_id', 'input_id'):
        assessment.pop(key)
    return dict(title='完成第二次作业', description='按通知完成练习。', content_type='task', notice_type='create', target=None,
                relevance=dict(state='current', reason='通知中的行动要求。', evidence=[]), assessment=assessment,
                recommendation=recommendation, reason=reason)


def empty_reply(record, status='no_tasks'):
    evidence=[dict(part_id='body',quote=record['text'])] if record['text'] else []
    return json.dumps(dict(tasks=[],empty_decision=dict(status=status,
        reason='只有课程资料，没有行动要求。' if status=='no_tasks' else '没有取得决定行动要求的内容。',evidence=evidence)),ensure_ascii=False)


def raw_task(pair):
    return json.dumps(dict(tasks=[model_task(pair)]), ensure_ascii=False)


def routing_cases():
    """Incident-shaped synthetic inputs; expectations are independent of Python routing."""
    cases=[]
    def add(name,text,route,value=None,*,partial=False,reference=None,collected='2026-10-09T12:00:00+08:00'):
        record=policy_case('explicit-complete')['pair']['record']
        record.update(input_id='synthetic:routing:'+name,text=text,body_status='partial' if partial else 'complete',
            published_at=None,course_term=None,source_metadata_text='当前课程：算法导论，2026秋季学期。',
            collected_at=collected,references=[reference] if reference else [])
        cases.append(dict(name=name,record=record,expected=dict(recommendation=route,value=value)))
    add('explicit-urgency','请于2026.9.29 9:00前交一份课程内容回顾，内容范围为目前学过的全部内容（包括当次课），请大家尽快完成，会按比例计入总评。',
        'task','2026-09-29T09:00:00+08:00',collected='2026-09-25T12:00:00+08:00')
    add('partial-clear-deadline','MOOC平台第五、六、七章作业已发布，请完成，截止时间为2026-11-17 23:30。',
        'task','2026-11-17T23:30:00+08:00',partial=True)
    add('yearless-no-publication','请完成Assignment 2并通过邮件提交。本次作业deadline为10.23。','task','2026-10-23')
    supplementary=dict(ref_id='extra',kind='attachment',label='参考资料.pdf',read_state='unread',text=None)
    add('supplementary-unread','请于2026年10月23日前交实验报告，未读附件只是可选参考资料，不改变要求。',
        'task','2026-10-23',partial=True,reference=supplementary)
    required=dict(supplementary,label='截止更正.pdf')
    add('required-date-unread','请提交实验报告。最终提交日期见附件《截止更正.pdf》，以附件为准。','task',reference=required)
    add('expired-partial','请完成Assignment 1并邮件提交，本次作业deadline为9.29。','task','2026-09-29',partial=True)
    add('recently-overdue','请于10月11日23:59前提交本次实验报告。','task','2026-10-11T23:59:00+08:00',collected='2026-10-16T12:00:00+08:00')
    add('old-event-preparation','明晚18:40首次实验课，请查收分组并加入本次活动群。','task','2026-09-16T18:40:00+08:00',collected='2026-10-16T12:00:00+08:00')
    cases[-1]['record']['published_at']=dict(value='2026-09-15T10:00:00+08:00',basis='source')
    add('non-action-partial','10月9日上午课程因故暂停一次。','no_tasks',partial=True)
    add('no-deadline-with-reference','请阅读下一章作为课前准备，不设完成期限。附件是可选参考资料。','task',reference=supplementary)
    add('notice-location','本学期《算法导论》的上课地点调整为理教408，其余安排不变。','task')
    cases[-1]['expected']['content_type']='notice'
    add('notice-event-time','2026年10月16日上午课程暂停一次。无需办理任何手续。','task')
    cases[-1]['expected']['content_type']='notice'
    add('task-join-group','请扫码加入课程微信群并填写分组表，本项不设完成期限。','task')
    cases[-1]['expected']['content_type']='task'
    add('mixed-notice-and-task','本学期上课地点为理教408。请扫码加入课程群，不设完成期限。','task')
    cases[-1]['expected']['content_types']=['notice','task']
    add('notice-deadline','选课结果公示至2026年10月20日18:00。','task','2026-10-20T18:00:00+08:00')
    cases[-1]['expected']['content_type']='notice'
    # Inclusion depends on concrete consequences, not the source section or
    # whether an ordinary lecture file happened to be downloaded.
    for name, text, label in (
        ('courseware-chapter', '第四章 字符串\n第四章 字符串.pdf', '第四章 字符串.pdf'),
        ('courseware-upload', '发布课件：generative-04-Transformer-2', 'generative-04-Transformer-2.pdf'),
        ('courseware-slides', '函数PPT', '函数.pptx'),
    ):
        add(name,text,'no_tasks',partial=True,reference=dict(ref_id='lecture',kind='attachment',
            label=label,read_state='unsupported',text=None),collected='2026-10-14T12:00:00+08:00')
        cases[-1]['record']['source_metadata_text']='课程：合成课程（26-27学年第1学期）\n栏目：教学内容'
    add('assessment-rules','本学期考核说明\n详见附件。','task',reference=dict(ref_id='assessment',kind='attachment',
        label='考核说明.pdf',read_state='complete',text='本学期出勤占20%，两次作业占30%，期末考试占50%。'),
        collected='2026-10-14T12:00:00+08:00')
    cases[-1]['expected']['content_type']='notice'
    add('assessment-unread','本学期考核说明，考核要求详见附件。','incomplete',reference=dict(ref_id='assessment',kind='attachment',
        label='考核说明.pdf',read_state='unread',text=None),collected='2026-10-14T12:00:00+08:00')
    add('courseware-required-reading','请在2026年10月18日前阅读第四章课件，下次课据此签到测验。','task','2026-10-18',
        reference=dict(ref_id='lecture',kind='attachment',label='第四章课件.pdf',read_state='unsupported',text=None),
        collected='2026-10-14T12:00:00+08:00')
    add('assignment-in-content','第五章课件已发布。课后作业：完成例题改编题并于2026年10月20日前提交。','task','2026-10-20',
        collected='2026-10-14T12:00:00+08:00')
    cases[-1]['record']['source_metadata_text']='课程：合成课程\n栏目：教学内容'
    add('deadline-change','练习平台第四章作业题截止日期修改为2026-11-03 23:59:00。请大家合理安排时间完成。',
        'candidate','2026-11-03T23:59:00+08:00',collected='2026-10-14T12:00:00+08:00')
    cases[-1]['expected']['notice_types']=['update','postpone']
    return cases
