"""Curate captured teaching-web originals without inventing gold labels or copying them to Git."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from .runner import digest,write_json


def source_group(entry):
    record=entry['record']
    return digest([record.get('course_id'),record.get('source_item_id')])


def purpose(expected):
    if expected.get('incomplete'):return '附件未读时是否保留未知、避免假阴性'
    if expected.get('reading'):return '保留明确阅读行动，且不虚构提交义务或DDL'
    if expected.get('no_action'):return '资料列表不能仅凭标题变成待办'
    if expected.get('notice_change'):return '保留改期用途和新时间，避免重复创建'
    if expected.get('time_role')=='event':return '考试活动时间不能误标为作业DDL'
    if expected.get('deadline'):return '作业DDL的日期、时刻和精度完整正确'
    raise ValueError('a case needs an explicit business failure target')


def original_case(value):
    entry=copy.deepcopy(value['entry']);expected=copy.deepcopy(value['expected'])
    identifier='REAL-'+str(value['id'])
    return dict(id=identifier,module='recognition',input_contract='0.4',origin='real_original',
                family_id=source_group(entry),shareable=False,label_status='historical_reviewed',
                scenario=purpose(expected),purpose=purpose(expected),expected=expected,
                provenance=dict(original_sha256=digest(entry),modifications=[],original_id=value['id']),
                request=dict(contract_version='0.4',request_id='eval:'+identifier,records=[entry]))


def modified_case(original,identifier,replacements,expected,reason):
    """A meaningful counterfactual requires an explicit oracle and an auditable edit."""
    if not reason.strip() or not replacements:raise ValueError('a modification needs a failure hypothesis and edits')
    case=copy.deepcopy(original);record=case['request']['records'][0]['record']
    fields=[record]+record.get('references',[])
    changes=[]
    for old,new in replacements:
        if not old or old==new:raise ValueError('no-op modifications are not evaluation cases')
        hits=0
        for field in fields:
            for key in ['text','source_metadata_text']:
                if isinstance(field.get(key),str):
                    hits+=field[key].count(old);field[key]=field[key].replace(old,new)
        if not hits:raise ValueError('replacement does not occur in the original material')
        changes.append(dict(before=old,after=new,occurrences=hits))
    case.update(id=identifier,origin='real_modified',expected=copy.deepcopy(expected),purpose=reason,
                label_status='requires_review',pair_with=original['id'])
    case['request']['request_id']='eval:'+identifier
    case['provenance']['modifications']=changes
    # The source family stays unchanged: derivatives are not independent original notices.
    return case


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection',type=Path,required=True)
    parser.add_argument('--reviewed',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    batches=[json.loads(line)for line in args.collection.read_text().splitlines()if line.strip()]
    entries=[entry for batch in batches for entry in batch['records']]
    reviewed=json.loads(args.reviewed.read_text());cases=[original_case(value)for value in reviewed]
    reviewed_hashes={c['provenance']['original_sha256']for c in cases}
    index=[]
    for entry in entries:
        record=entry['record'];fingerprint=digest(entry)
        index.append(dict(family_id=source_group(entry),original_sha256=fingerprint,
                          label_status='historical_reviewed'if fingerprint in reviewed_hashes else 'requires_review',
                          label=entry.get('source',{}).get('label'),body_status=record.get('body_status'),
                          text=record.get('text',''),references=record.get('references',[]),entry=entry))
    write_json(args.out/'review-queue.private.json',index)
    write_json(args.out/'original-cases.private.json',cases)
    write_json(args.out/'cohort-summary.json',dict(captured=len(entries),families=len({r['family_id']for r in index}),
               historically_reviewed=len(cases),requires_review=sum(r['label_status']=='requires_review'for r in index),
               purposes=sorted({c['purpose']for c in cases}),paid_calls=0))
    print((args.out/'cohort-summary.json').read_text())

if __name__=='__main__':main()
