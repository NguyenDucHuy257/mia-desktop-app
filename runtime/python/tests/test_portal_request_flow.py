import unittest
from pathlib import Path
import sys
from urllib.parse import quote
from uuid import UUID

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / 'vendor' / 'mia_crawl_service'),
)

from app.crawlers.invoice_crawler import build_invoice_list_headers
from app.crawlers.invoice_detail_crawler import build_detail_headers
from app.crawlers.web_client import (
    DEFAULT_USER_AGENT,
    INVOICE_LOOKUP_URL,
    PORTAL_ROOT_URL,
    WebClient,
)
from app.services.portal_session import TaxPortalSession


class PortalRequestFlowTests(unittest.TestCase):
    def test_default_headers_match_current_invoice_lookup_context(self):
        headers = WebClient().build_headers(authorization='synthetic-token')

        self.assertEqual(headers['referer'], INVOICE_LOOKUP_URL)
        self.assertEqual(headers['end-point'], '/tra-cuu/tra-cuu-hoa-don')
        self.assertEqual(headers['authorization'], 'Bearer synthetic-token')
        self.assertIn('Chrome/152.0.0.0', headers['user-agent'])
        self.assertIn('Google Chrome";v="152', headers['sec-ch-ua'])
        self.assertEqual(str(UUID(headers['request-id'])), headers['request-id'])
        self.assertEqual(DEFAULT_USER_AGENT, headers['user-agent'])

    def test_each_wire_attempt_gets_a_fresh_request_id(self):
        base = WebClient().build_headers()
        first = WebClient._fresh_request_headers(base)
        second = WebClient._fresh_request_headers(base)

        self.assertNotEqual(first['request-id'], second['request-id'])
        self.assertEqual(str(UUID(first['request-id'])), first['request-id'])
        self.assertEqual(str(UUID(second['request-id'])), second['request-id'])
        self.assertEqual(base['end-point'], first['end-point'])

    def test_authenticated_profile_uses_root_context(self):
        portal = TaxPortalSession(**{'username': 'synthetic', 'password': 'synthetic'})
        setattr(portal, 'token', 'synthetic-token')

        headers = portal.authentication_headers

        self.assertEqual(headers['referer'], PORTAL_ROOT_URL)
        self.assertEqual(headers['end-point'], '/')
        self.assertEqual(headers['action'], '')
        self.assertEqual(headers['authorization'], 'Bearer synthetic-token')

    def test_invoice_list_actions_match_har(self):
        base = WebClient().build_headers(authorization='synthetic-token')

        sold = build_invoice_list_headers(base, 'sold')
        purchase = build_invoice_list_headers(base, 'purchase')

        self.assertEqual(
            sold['Action'],
            quote('Tìm kiếm (hóa đơn bán ra)', safe='()'),
        )
        self.assertEqual(
            purchase['Action'],
            quote('Tìm kiếm (hóa đơn mua vào)', safe='()'),
        )
        self.assertEqual(sold['End-Point'], '/tra-cuu/tra-cuu-hoa-don')

    def test_detail_action_changed_from_print_to_view(self):
        headers = build_detail_headers(WebClient().build_headers(), 'purchase')

        self.assertEqual(
            headers['Action'],
            quote('Xem hóa đơn (hóa đơn mua vào)', safe='()'),
        )
        self.assertNotIn('In%20h', headers['Action'])


if __name__ == '__main__':
    unittest.main()
