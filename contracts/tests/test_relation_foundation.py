"""Independent semantic expectations; these are not live model evaluations."""
from copy import deepcopy
import unittest
from contracts import relation_foundation as c


def example():
    sources = []
    for sid, course, text in [('s6', 'lecture', '第6讲：提交书面作业题。'),
                               ('s7', 'lecture', '第7讲：提交书面作业题。'),
                               ('s67', 'seminar', '第6—7讲作业：只提交作业题，不交研讨题。')]:
        p = dict(part_id='body', label='正文', text=text, read_state='complete', sha256=None)
        p['revision'] = c.digest(p)
        s = dict(id=sid, input_id=sid, course_id=course, content_id=sid, term_id='2026-fall',
                 course_name=course, url=None, starts_on='2026-09-01', ends_on='2027-02-28',
                 label=sid, snapshot_id='snapshot:' + sid, parts=[p], submission=None)
        if sid == 's67':
            s['submission'] = dict(state='submitted', basis='review_submission_history_title',
                snapshot_id='native:1', sequence=1, control_revision=0, review_pending=False,
                scope='unknown', control_digest='0'*64)
        s['revision'] = c.digest(s)
        sources.append(s)
    ctx = dict(contract_version=c.VERSION, scope_id='scope:fixture', source_account_id='account:fixture',
               term_id='2026-fall', sources=sources, actions=[], transitions=[], warnings=[])
    ctx['revision'] = c.digest(ctx)
    def q(source):
        p = source['parts'][0]
        return dict(source_id=source['id'], part_id=p['part_id'], part_revision=p['revision'], offset=0, quote=p['text'])
    rel = dict(contract_version=c.VERSION, context_revision=ctx['revision'], kind='bundle',
        source_ids=[s['id'] for s in sources],
        roles=[dict(source_id=s['id'], role='submission' if s['submission'] else 'content', evidence=[q(s)]) for s in sources],
        units=[dict(id='unit:6', label='第6讲作业', channel='书面', action_ids=[], evidence=[q(sources[0])]),
               dict(id='unit:7', label='第7讲作业', channel='书面', action_ids=[], evidence=[q(sources[1])])],
        evidence=[q(s) for s in sources], coverage=[dict(source_id='s67', unit_ids=['unit:6', 'unit:7'],
            extent='complete', evidence=[q(sources[2])])], deadline=None)
    return ctx, rel


class ContractTests(unittest.TestCase):
    def test_checked_in_schema_and_example_are_exact(self):
        from pathlib import Path
        import json
        root = Path(__file__).parents[1]
        self.assertEqual(json.loads((root/'schemas/relation-foundation-v0.1.schema.json').read_text()), c.schema())
        saved = json.loads((root/'examples/relation-foundation-v01.json').read_text())
        c.validate_relation(saved['context'], saved['relation'])
    def test_bundle_submitted_without_material_submission_never_authorizes_execution(self):
        ctx, rel = example()
        result = c.coverage_summary(ctx, rel)[0]
        self.assertEqual(result['observed_state'], 'submitted')
        self.assertEqual(result['uncovered_unit_ids'], [])
        self.assertFalse(result['application_allowed'])
        self.assertIsNone(ctx['sources'][0]['submission'])

    def test_partial_coverage_keeps_other_lecture_outstanding(self):
        ctx, rel = example()
        rel['coverage'][0]['unit_ids'] = ['unit:6']
        rel['coverage'][0]['extent'] = 'partial'
        self.assertEqual(c.coverage_summary(ctx, rel)[0]['uncovered_unit_ids'], ['unit:7'])

    def test_independent_channels_have_separate_units(self):
        ctx, rel = example()
        rel['kind'] = 'independent'
        rel['units'][0]['channel'] = 'POJ'
        rel['units'][1]['channel'] = 'MOOC'
        rel['coverage'] = []
        c.validate_relation(ctx, rel)

    def test_duplicate_and_complement_are_single_delivery(self):
        for kind in ('duplicate', 'complement'):
            ctx, rel = example()
            rel['kind'] = kind
            with self.assertRaisesRegex(ValueError, 'cardinality'):
                c.validate_relation(ctx, rel)
            rel['units'] = rel['units'][:1]
            rel['coverage'][0]['unit_ids'] = ['unit:6']
            c.validate_relation(ctx, rel)

    def test_exact_quotes_offsets_hashes_and_member_provenance(self):
        mutations = [lambda r: r['evidence'][0].update(quote='模型改写的内容'),
                     lambda r: r['evidence'][0].update(offset=1),
                     lambda r: r['evidence'][0].update(part_revision='0'*64),
                     lambda r: r['evidence'][0].update(source_id='foreign'),
                     lambda r: r['evidence'].pop()]
        for mutate in mutations:
            ctx, rel = example()
            mutate(rel)
            with self.assertRaises(ValueError):
                c.validate_relation(ctx, rel)

    def test_non_bmp_offsets_are_codepoints(self):
        ctx, rel = example()
        p = ctx['sources'][0]['parts'][0]
        p['text'] = '📚' + p['text']
        p['revision'] = c.digest({k:v for k,v in p.items() if k != 'revision'})
        ctx['sources'][0]['revision'] = c.digest({k:v for k,v in ctx['sources'][0].items() if k != 'revision'})
        ctx['revision'] = c.digest({k:v for k,v in ctx.items() if k != 'revision'})
        rel['context_revision'] = ctx['revision']
        for qs in [rel['evidence'], rel['roles'][0]['evidence'], rel['units'][0]['evidence']]:
            qs[0].update(offset=1, part_revision=p['revision'])
        c.validate_relation(ctx, rel)

    def test_unknown_semester_cannot_be_stored_as_relation(self):
        ctx, rel = example()
        ctx['term_id'] = None
        ctx['revision'] = c.digest({k:v for k,v in ctx.items() if k != 'revision'})
        rel['context_revision'] = ctx['revision']
        with self.assertRaisesRegex(ValueError, 'semester'):
            c.validate_relation(ctx, rel)

    def test_coverage_requires_own_native_submission_evidence(self):
        for change in ('unknown_unit', 'material_page', 'wrong_quote', 'duplicate_coverage'):
            ctx, rel = example()
            cov = rel['coverage'][0]
            if change == 'unknown_unit': cov['unit_ids'] = ['absent']
            elif change == 'material_page': cov['source_id'] = 's6'
            elif change == 'wrong_quote': cov['evidence'] = [rel['evidence'][0]]
            else: rel['coverage'].append(deepcopy(cov))
            with self.assertRaises(ValueError): c.validate_relation(ctx, rel)

    def test_invalid_calendar_and_unread_evidence(self):
        ctx, rel = example()
        rel['deadline'] = dict(date='2026-02-30', time=None, evidence=rel['evidence'])
        with self.assertRaises(Exception): c.validate_relation(ctx, rel)
        ctx, rel = example()
        p = ctx['sources'][0]['parts'][0]
        p['read_state'] = 'unsupported'
        p['revision'] = c.digest({k:v for k,v in p.items() if k != 'revision'})
        s = ctx['sources'][0]
        s['revision'] = c.digest({k:v for k,v in s.items() if k != 'revision'})
        ctx['revision'] = c.digest({k:v for k,v in ctx.items() if k != 'revision'})
        rel['context_revision'] = ctx['revision']
        with self.assertRaisesRegex(ValueError, 'unread'): c.validate_relation(ctx, rel)

    def test_schema_rejects_model_claimed_apply_or_completed(self):
        ctx, rel = example()
        rel['apply'] = True
        with self.assertRaises(Exception): c.validate_relation(ctx, rel)
