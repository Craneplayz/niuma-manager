"""Offline tests for the read-only PKU email source and its account API."""
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import email_source
from email_test_support import FakeIMAP


ADDRESS = 'student@stu.pku.edu.cn'


class EmailSourceTests(unittest.TestCase):
    def test_only_supported_pku_domains_are_accepted_and_normalized(self):
        self.assertEqual(email_source.normalize_address('  STUDENT@STU.PKU.EDU.CN '), ADDRESS)
        self.assertEqual(email_source.normalize_address('u@pku.edu.cn'), 'u@pku.edu.cn')
        for address in ('student@example.com', 'broken', 'a..b@pku.edu.cn'):
            with self.subTest(address=address), self.assertRaises(email_source.EmailSourceError):
                email_source.normalize_address(address)

    def test_mailbox_uses_fixed_ssl_host_and_logs_out(self):
        client = FakeIMAP()
        with patch.object(email_source.imaplib, 'IMAP4_SSL', return_value=client) as connect:
            with email_source._mailbox(ADDRESS, 'student', 'client-secret') as opened:
                self.assertIs(opened, client)

        self.assertEqual(connect.call_args.args, ('imaphz.qiye.163.com', 993))
        self.assertIsNotNone(connect.call_args.kwargs['ssl_context'])
        self.assertEqual(connect.call_args.kwargs['timeout'], 15)
        self.assertEqual(client.login_args, ('student', 'client-secret'))
        self.assertIsInstance(client._niuma_connect_ms, float)
        self.assertGreaterEqual(client._niuma_connect_ms, 0)
        self.assertTrue(client.logged_out)

    def test_preview_is_read_only_and_fetches_only_recent_header_metadata(self):
        client = FakeIMAP(uids=range(1, 66))
        opened = []

        @contextmanager
        def mailbox(address, username, password):
            opened.append((address, username, password))
            yield client

        with patch.object(email_source, '_mailbox', mailbox):
            result = email_source.preview(ADDRESS, 'student', 'client-secret')

        self.assertEqual(opened, [(ADDRESS, 'student', 'client-secret')])
        self.assertTrue(client.readonly)
        self.assertEqual(result['summary'], {'total': 50})
        self.assertEqual(len(client.fetches), 50)
        self.assertEqual([uid for uid, _ in client.fetches], list(range(65, 15, -1)))
        self.assertTrue(all('HEADER.FIELDS' in query for _, query in client.fetches))
        self.assertTrue(all('BODY.PEEK[TEXT]' not in query and 'BODY.PEEK[1]' not in query
                            for _, query in client.fetches))
        self.assertTrue(result['messages'][0]['input_id'].endswith(':77:65'))
        self.assertIn('作业通知', result['messages'][0]['subject'])

    def test_range_defaults_to_latest_fifty_and_paginates_larger_count(self):
        client = FakeIMAP(uids=range(1, 76))
        @contextmanager
        def mailbox(*_args, **_kwargs):
            yield client
        with patch.object(email_source, '_mailbox', mailbox):
            first = email_source.preview_range(ADDRESS, 'synthetic', 'synthetic')
            second = email_source.preview_range(ADDRESS, 'synthetic', 'synthetic', limit=75, offset=50)
        self.assertEqual(len(first['messages']), 50)
        self.assertEqual(first['summary']['total'], 50)
        self.assertIsNone(first['next_offset'])
        self.assertEqual(second['summary']['total'], 75)
        self.assertEqual(len(second['messages']), 25)
        self.assertTrue(second['messages'][0]['input_id'].endswith(':77:25'))
        self.assertTrue(all('HEADER.FIELDS' in query for _, query in client.fetches))


    def test_preview_selector_filters_before_pagination_and_fetches_only_page_headers(self):
        client = FakeIMAP(uids=range(1, 101))
        @contextmanager
        def mailbox(*_args, **_kwargs):
            yield client
        with patch.object(email_source, '_mailbox', mailbox):
            result = email_source.preview_range(ADDRESS, 'synthetic', 'synthetic', limit=100,
                offset=0, page_size=2, select_uids=lambda _validity, uids: [1, 3, 5, 7])
        self.assertEqual(result['summary'], {'total': 100, 'matched_total': 4})
        self.assertEqual(result['next_offset'], 2)
        self.assertEqual([m['input_id'].rsplit(':', 1)[1] for m in result['messages']], ['1', '3'])
        self.assertEqual(len(client.fetches), 2)
        self.assertTrue(all('HEADER.FIELDS' in query for _, query in client.fetches))

    def test_date_range_is_inclusive_in_shanghai_and_listing_matches_preview(self):
        client = FakeIMAP(uids=(1, 2, 3))
        client.dates = {1: '13-Oct-2026 15:59:59 +0000',
                        2: '13-Oct-2026 16:00:00 +0000',
                        3: '14-Oct-2026 00:00:00 +0800'}
        @contextmanager
        def mailbox(*_args, **_kwargs):
            yield client
        with patch.object(email_source, '_mailbox', mailbox):
            result = email_source.preview_range(ADDRESS, 'synthetic', 'synthetic',
                                                 limit=None, start_date='2026-10-14')
            ids = email_source.list_input_ids(ADDRESS, 'synthetic', 'synthetic',
                                               limit=None, start_date='2026-10-14')
        self.assertEqual([m['input_id'] for m in result['messages']], ids)
        self.assertEqual([x.rsplit(':', 1)[1] for x in ids], ['3', '2'])
        self.assertEqual(result['summary']['total'], 2)
        self.assertTrue(all('BODYSTRUCTURE' not in query and 'BODY.PEEK[TEXT]' not in query
                            for _, query in client.fetches))

    def test_selected_message_reads_only_plain_body_with_peek(self):
        client = FakeIMAP(uids=(7,))
        selected = email_source._identity(ADDRESS, '77', 7)

        @contextmanager
        def mailbox(*_args, **_kwargs):
            yield client

        with patch.object(email_source, '_mailbox', mailbox):
            records, summary = email_source.fetch_selected(ADDRESS, 'student', 'secret', [selected])

        self.assertTrue(client.readonly)
        self.assertEqual(summary['selected'], 1)
        self.assertEqual(summary['unavailable'], 0)
        self.assertEqual(records[0]['platform'], 'email')
        self.assertIn('Please submit the assignment.', records[0]['text'])
        self.assertTrue(any('BODY.PEEK[TEXT]' in query for _, query in client.fetches))
        self.assertTrue(all('BODY[' not in query or 'BODY.PEEK[' in query for _, query in client.fetches))

    def test_multipart_message_reads_plain_part_and_marks_omitted_attachment_partial(self):
        structure = (
            '(("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 30 1) '
            '("APPLICATION" "PDF" ("NAME" "assignment.pdf") NIL NIL "BASE64" 100 NIL '
            '("ATTACHMENT" ("FILENAME" "assignment.pdf"))))'
        )
        client = FakeIMAP(uids=(7,), structure=structure)
        selected = email_source._identity(ADDRESS, '77', 7)

        @contextmanager
        def mailbox(*_args, **_kwargs):
            yield client

        with patch.object(email_source, '_mailbox', mailbox):
            records, summary = email_source.fetch_selected(ADDRESS, 'student', 'secret', [selected])

        self.assertEqual(records[0]['text_quality'], 'partial')
        self.assertEqual(summary['attachments_or_other_parts_omitted'], 1)
        body_fetches = [query for _, query in client.fetches
                        if 'BODY.PEEK[TEXT]' in query or 'BODY.PEEK[1]' in query]
        self.assertEqual(len(body_fetches), 1)
        self.assertIn('BODY.PEEK[1]', body_fetches[0])

    def test_selected_message_rejects_foreign_or_duplicate_preview_ids(self):
        foreign_id = email_source._identity('u@pku.edu.cn', '77', 7)
        with self.assertRaises(email_source.EmailSourceError):
            email_source.fetch_selected(ADDRESS, 'student', 'secret', [foreign_id])
        valid_id = email_source._identity(ADDRESS, '77', 7)
        with self.assertRaises(email_source.EmailSourceError):
            email_source.fetch_selected(ADDRESS, 'student', 'secret', [valid_id, valid_id])

    def test_body_fetch_reuses_only_small_short_lived_batches(self):
        clients = []
        def connect(*_args, **_kwargs):
            client = FakeIMAP(uids=range(1, 8))
            clients.append(client)
            return client
        ids = [email_source._identity(ADDRESS, '77', uid) for uid in range(1, 7)]
        guards = []
        records, summary = None, None
        with patch.object(email_source.imaplib, 'IMAP4_SSL', side_effect=connect):
            records, summary = email_source.fetch_selected(
                ADDRESS, 'student', 'secret', ids, guard=lambda: guards.append(1))
        self.assertEqual(len(records), 6)
        self.assertEqual(summary['selected'], 6)
        self.assertEqual(len(clients), 2, 'six messages must use two sessions of at most five')
        self.assertTrue(all(client.logged_out for client in clients))
        self.assertGreaterEqual(len(guards), 6)
        self.assertTrue(all('BODY.PEEK[' in query for client in clients
                            for _, query in client.fetches if 'BODY.' in query))

    def test_message_exclusion_before_body_literal_skips_only_that_message(self):
        client = FakeIMAP(uids=(7, 8))
        ids = [email_source._identity(ADDRESS, '77', uid) for uid in (7, 8)]
        calls = {}
        def message_guard(input_id):
            calls[input_id] = calls.get(input_id, 0) + 1
            if input_id == ids[0] and calls[input_id] == 2:
                raise email_source.SkipEmail()
        @contextmanager
        def mailbox(*_args, **_kwargs):
            yield client
        with patch.object(email_source, '_mailbox', mailbox):
            records, summary = email_source.fetch_selected(ADDRESS, 'student', 'secret', ids,
                message_guard=message_guard)
        self.assertEqual([record['input_id'] for record in records], [ids[1]])
        self.assertEqual(summary['unavailable'], 1)
        body_fetches = [(uid, query) for uid, query in client.fetches
                        if 'BODY.PEEK[' in query and 'HEADER.FIELDS' not in query]
        self.assertEqual([uid for uid, _ in body_fetches], [8])

    def test_bodystructure_metadata_limit_prevents_body_fetch(self):
        client = FakeIMAP(structure='(' + 'x' * email_source.MAX_IMAP_METADATA_BYTES + ')')
        with self.assertRaises(email_source.EmailSourceError) as caught:
            email_source._fetch_text(client, 7)
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(client.fetches, [(7, '(UID BODYSTRUCTURE)')])

    def test_nonliteral_response_does_not_supply_header_or_body_content(self):
        client = FakeIMAP()
        with patch.object(client, 'uid', return_value=('OK', [b'7 (UID 7)', b')'])):
            self.assertEqual(email_source._fetch_tuple(client, 7, '(UID BODY.PEEK[TEXT])'),
                             (None, None))

    def test_html_decoder_discards_script_and_style_content(self):
        part = {'charset': 'utf-8', 'encoding': '7bit', 'subtype': 'html'}
        decoded = email_source._decode_part(
            b'<html><head>hidden</head><body><p>Visible</p><script>secret()</script></body></html>',
            part,
        )
        self.assertIn('Visible', decoded)
        self.assertNotIn('hidden', decoded)
        self.assertNotIn('secret()', decoded)


class BatchHeaderSafetyTests(unittest.TestCase):
    def test_response_uid_mapping_is_independent_of_response_order(self):
        client=FakeIMAP(uids=(1,2))
        original=client.uid
        def reversed_response(*args):
            status,data=original(*args)
            return status,list(reversed(data))
        with patch.object(client,'uid',side_effect=reversed_response):
            rows=email_source._fetch_headers(client,[1,2])
        self.assertEqual(set(rows),{1,2})
        self.assertIn(b'UID 1 ',rows[1][0]); self.assertIn(b'UID 2 ',rows[2][0])

    def test_unrequested_duplicate_missing_uid_and_oversize_literals_are_rejected(self):
        client=FakeIMAP()
        cases=[([(b'(UID 999 INTERNALDATE "14-Oct-2026 10:00:00 +0800")',b'Subject: x')],502),
               ([(b'(UID 1)',b'Subject: x'),(b'(UID 1)',b'Subject: x')],502),
               ([(b'(INTERNALDATE "14-Oct-2026 10:00:00 +0800")',b'Subject: x')],502),
               ([(b'(UID 1)',b'x'*(email_source.MAX_HEADER_BYTES+1))],422)]
        for data,expected in cases:
            with self.subTest(expected=expected),patch.object(client,'uid',return_value=('OK',data)):
                with self.assertRaises(email_source.EmailSourceError) as caught:
                    email_source._fetch_headers(client,[1])
                self.assertEqual(caught.exception.status,expected)


if __name__ == '__main__':
    unittest.main()
