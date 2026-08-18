from app.config.crawl_config import CATEGORY_TO_QUERY_TYPE, VALID_DIRECTIONS

GET_CAPTCHA_API = 'https://hoadondientu.gdt.gov.vn/api/captcha'
LOGIN_API = 'https://hoadondientu.gdt.gov.vn/api/security-taxpayer/authenticate'
GET_COMPANY_INFO_API = 'https://hoadondientu.gdt.gov.vn/api/security-taxpayer/profile'

API_BASE_URL = 'https://hoadondientu.gdt.gov.vn/api'
INVOICE_CATEGORIES = CATEGORY_TO_QUERY_TYPE
INVOICE_DIRECTIONS = VALID_DIRECTIONS


def invoice_list_url(category: str, direction: str) -> str:
    if category not in INVOICE_CATEGORIES:
        raise ValueError(f'Unsupported invoice category: {category}')
    if direction not in INVOICE_DIRECTIONS:
        raise ValueError(f'Unsupported invoice direction: {direction}')
    return f"{API_BASE_URL}/{INVOICE_CATEGORIES[category]}/invoices/{direction}"


def invoice_export_url(category: str, direction: str) -> str:
    if category not in INVOICE_CATEGORIES:
        raise ValueError(f'Unsupported invoice category: {category}')
    if direction not in INVOICE_DIRECTIONS:
        raise ValueError(f'Unsupported invoice direction: {direction}')

    # The portal uses the unfortunately named export-excel-sold endpoint for
    # purchase invoices. This is confirmed by both its current JavaScript and
    # the supplied HAR recording.
    suffix = 'export-excel-sold' if direction == 'purchase' else 'export-excel'
    return f"{API_BASE_URL}/{INVOICE_CATEGORIES[category]}/invoices/{suffix}"
