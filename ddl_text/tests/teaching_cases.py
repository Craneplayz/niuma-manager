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
    add('required-date-unread','请提交实验报告。最终提交日期见附件《截止更正.pdf》，以附件为准。','candidate',reference=required)
    add('expired-partial','请完成Assignment 1并邮件提交，本次作业deadline为9.29。','filtered','2026-09-29',partial=True)
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
    return cases
