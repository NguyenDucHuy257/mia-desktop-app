import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor' / 'mia_crawl_service'))

import requests
from app.crawlers.diagnostics import job_diagnostics
from app.crawlers.web_client import WebClient
from mia_logging import configure_logging, close_logging


class CrawlDiagnosticsTests(unittest.TestCase):
    def test_failed_login_is_correlated_and_redacted_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            configure_logging(Path(directory))
            client = WebClient()
            response = requests.Response()
            response.status_code = 401
            response._content = json.dumps({'message': 'Captcha invalid; password=private-password; token=private-token',
                                             'token': 'private-token', 'cookie': 'private-cookie'}).encode()
            response.headers['Set-Cookie'] = 'private-cookie'
            client.session.post = Mock(return_value=response)

            class Handler:
                @job_diagnostics('auth')
                def authenticate(self, job):
                    return client.post('https://hoadondientu.gdt.gov.vn/api/security-taxpayer/authenticate',
                                       headers=client.build_headers(),
                                       json_payload={'username': '0100000000', 'password': 'private-password',
                                                     'ckey': 'private-key', 'cvalue': 'private-answer'})
            try:
                with self.assertRaises(requests.HTTPError):
                    Handler().authenticate(SimpleNamespace(job_id='job-test'))
            finally:
                close_logging()
            text = (Path(directory) / 'crawl-diagnostics.log').read_text(encoding='utf8')
            for secret in ('private-password', 'private-token', 'private-cookie', 'private-key', 'private-answer', '0100000000'):
                self.assertNotIn(secret, text)
            records = [json.loads(line.split(' crawl_diagnostic ', 1)[1]) for line in text.splitlines()]
            failed = next(item for item in records if item['event'] == 'http_finished')
            self.assertEqual(failed['http_status'], 401)
            self.assertEqual(failed['job_id'], 'job-test')
            self.assertIn('Captcha invalid', failed['upstream_error']['message'])
            self.assertEqual(records[-1]['event'], 'unit_failed')
            self.assertEqual(records[-1]['exception_chain'][0]['type'], 'HTTPError')
            self.assertTrue(records[-1]['frames'])

    def test_retry_has_distinct_ids_and_cursor_is_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            configure_logging(Path(directory))
            client = WebClient()
            response = requests.Response()
            response.status_code = 200
            response._content = b'{"datas":[]}'
            client.session.get = Mock(side_effect=[requests.Timeout('private-network-text'), response])
            try:
                from unittest.mock import patch
                with patch('app.crawlers.web_client.time.sleep'):
                    client.get('https://hoadondientu.gdt.gov.vn/api/query/invoices/purchase',
                               params={'state': 'private-cursor', 'nbmst': '0100000000', 'size': '50'}, retry_attempts=2)
            finally:
                close_logging()
            text = (Path(directory) / 'crawl-diagnostics.log').read_text(encoding='utf8')
            self.assertNotIn('private-', text)
            self.assertNotIn('0100000000', text)
            records = [json.loads(line.split(' crawl_diagnostic ', 1)[1]) for line in text.splitlines()]
            starts = [item for item in records if item['event'] == 'http_started']
            self.assertEqual(len(starts), 2)
            self.assertNotEqual(starts[0]['request_id'], starts[1]['request_id'])
            self.assertEqual(starts[0]['params']['state_sha256'], starts[1]['params']['state_sha256'])


if __name__ == '__main__':
    unittest.main()
