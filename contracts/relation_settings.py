"""Account-scoped permission for semantic automatic merging."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from .task_relations import obj, string

VERSION='task-relations-settings/0.1'
FIELDS=dict(contract_version=dict(const=VERSION),source_account_id=string(160,1),
    revision=dict(type='integer',minimum=0),mode=dict(enum=['manual','automatic']))
VIEW=obj(FIELDS)
UPDATE=obj(dict(FIELDS,consent=dict(type='boolean')))

def check(value,update=False):
    Draft202012Validator(UPDATE if update else VIEW).validate(value)
    return value

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for name,schema in [('task-relations-settings-v0.1',VIEW),('task-relations-settings-update-v0.1',UPDATE)]:
        target=Path(__file__).parent/'schemas'/(name+'.schema.json')
        text=json.dumps(schema,ensure_ascii=False,indent=2)+'\n'
        if args.check:
            if target.read_text(encoding='utf8')!=text:raise SystemExit('regenerate relation settings schema')
        else:target.write_text(text,encoding='utf8')
