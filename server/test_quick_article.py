import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

import quick_article as article


class ArticleReadingTests(unittest.TestCase):
    def test_visible_article_and_challenge_page(self):
        page = article.ArticleText()
        page.feed('<title>平台</title><h1 id="activity-name">春季讲座</h1><div id="js_content"><p>周五三点</p><script>window.fake = 123</script><p>一号厅</p></div><p>推荐阅读</p>')
        self.assertEqual(page.text(), '春季讲座\n周五三点\n一号厅')
        empty = article.ArticleText()
        empty.feed('<h1>微信公众平台</h1><p>请完成验证</p>')
        self.assertEqual(empty.text(), '')

    def test_unreadable_provider_result_is_never_accepted_as_article(self):
        url = 'https://www.example.org/events'
        metadata = {'retrievedUrl': url, 'urlRetrievalStatus': 'URL_RETRIEVAL_STATUS_ERROR'}
        candidate = {'urlContextMetadata': {'urlMetadata': [metadata]},
                     'content': {'parts': [{'text': '{"text":"活动安排"}'}]}}
        def opening(*args, **kwargs):
            return BytesIO(json.dumps({'candidates': [candidate]}).encode())
        opener = MagicMock()
        opener.open.side_effect = opening
        with patch.object(article, 'live_options', return_value=dict(provider='qiniu', endpoint='https://api.qnaigc.com/v1', key='synthetic')), patch.object(article, 'build_opener', return_value=opener):
            self.assertEqual(article.provider_text(url), '')
            metadata['urlRetrievalStatus'] = 'URL_RETRIEVAL_STATUS_SUCCESS'
            self.assertEqual(article.provider_text(url), '活动安排')
            metadata['retrievedUrl'] = 'https://www.example.org/other'
            self.assertEqual(article.provider_text(url), '')

    def test_internal_and_non_https_links_are_skipped(self):
        for url in ['http://www.example.org/', 'https://127.0.0.1/', 'https://[::1]/',
                    'https://user:secret@public.org/', 'https://intranet.local/', 'https://localhost/']:
            self.assertFalse(article.public_url(url))
