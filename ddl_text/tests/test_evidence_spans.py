"""Long-input regressions through the real worker; synthetic model output only."""
import json
import unittest

from ddl_text.config import Settings
from ddl_text.evidence_spans import catalog
from ddl_text.extraction import ExtractionAdapter
from ddl_text.pipeline import process_request
from ddl_text.tests.test_extraction import message


MAX_SPAN_CHARS = 768


class ManyActionsClient:
    def generate(self, instructions, record_json, settings, attempt):
        data = json.loads(record_json)
        tasks = []
        for index in range(25):
            title = f'Submit exercise {index:02d}'
            key = next(key for key, span in data['evidence_catalog'].items() if title in span['quote'])
            tasks.append(dict(title=title,description=title,prerequisites=[],notice_type='create',target=None,
                course=None,evidence=[dict(span_id=key)],times=[],review_reasons=[],
                relevance=dict(state='current',reason='Required exercise',evidence=[])))
        return json.dumps(dict(tasks=tasks))


class EvidenceSpansTests(unittest.TestCase):
    def test_long_english_and_unpunctuated_inputs_survive_worker_output_limit(self):
        for separator in ('. ', ' '):
            text = separator.join(f'Submit exercise {index:02d} '+('background context '*110) for index in range(25))
            self.assertGreater(len(text),50000)
            request = dict(contract_version='0.2',request_id='long-input',records=[message(text)])
            result = process_request(request,ExtractionAdapter(ManyActionsClient()),Settings(mode='real',timeout_seconds=12,max_retries=0))
            self.assertEqual(result['status'],'success',result['errors'])
            self.assertEqual(len(result['tasks']),25)
            self.assertLess(len(json.dumps(result,ensure_ascii=False).encode()),100000)
            for task in result['tasks']:
                for evidence in task['evidence']:
                    self.assertIn(evidence['quote'],text)
                    self.assertLessEqual(len(evidence['quote']),MAX_SPAN_CHARS)

    def test_boundaries_preserve_words_dates_multilingual_phrases_and_context(self):
        for phrase in ('submit extraordinary_assignment before 2026-10-31T23:59:00+08:00',
                       '请于2026年10月31日23时59分提交作业', 'submit on October 31, 2026 at 23:59'):
            for offset in (MAX_SPAN_CHARS-10,MAX_SPAN_CHARS,MAX_SPAN_CHARS+10):
                text = 'x '*(offset//2)+phrase+' tail'*200
                spans = catalog(message('主通知。',context=[message(text,input_id='context')]))
                quotes = [span['quote'] for span in spans.values() if span['input_id']=='context']
                self.assertTrue(any(phrase in quote for quote in quotes),(phrase,offset))
                self.assertTrue(all(quote in text and len(quote)<=MAX_SPAN_CHARS for quote in quotes))
        # Even pathological unbroken tokens and whitespace have bounded windows.
        for text in ('x'*50000, '中'*50000, ' '*5000+'task'):
            spans = catalog(message(text))
            self.assertTrue(spans)
            self.assertTrue(all(len(span['quote'])<=MAX_SPAN_CHARS for span in spans.values()))

    def test_short_sentences_keep_stable_ids_and_exact_quotes(self):
        text = '明天18点提交。The date is 2026-10-31.\n最后一条'
        spans = catalog(message(text))
        self.assertEqual(''.join(span['quote'] for span in spans.values()),text)
        self.assertEqual(list(spans),['q0','q1','q2'])
