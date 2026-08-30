from __future__ import annotations

import base64
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

logger = logging.getLogger(__name__)

# A4 portrait expressed in CSS pixels (Chromium prints at 96 CSS px/inch).
A4_WIDTH_INCHES = 8.27
A4_HEIGHT_INCHES = 11.69
CSS_PIXELS_PER_INCH = 96.0
DEFAULT_INVOICE_ASSETS_DIR = (
    Path(__file__).resolve().parents[2] / 'resources' / 'invoice_assets'
)
PAGE_BACKGROUND_ASSET = 'viewinvoice-bg.jpg'
PAGE_SIGN_ASSET = 'sign-check.jpg'
PAGE_SIGN_ELEMENT_ID = 'mia-invoice-pdf-page-sign'


@dataclass(frozen=True)
class PdfLayoutOptions:
    """A4 page geometry used without scaling the invoice content."""

    margin_inches: float = 0.3  # Lề trắng mỗi cạnh của trang A4.

    def validate(self) -> None:
        if not 0 <= self.margin_inches < A4_WIDTH_INCHES / 2:
            raise ValueError('margin_inches must fit inside an A4 page')

    @property
    def usable_width_px(self) -> float:
        return (A4_WIDTH_INCHES - 2 * self.margin_inches) * CSS_PIXELS_PER_INCH

    @property
    def usable_height_px(self) -> float:
        return (A4_HEIGHT_INCHES - 2 * self.margin_inches) * CSS_PIXELS_PER_INCH


@dataclass(frozen=True)
class PdfLayoutPlan:
    single_page_target: bool
    scale: float
    content_width_px: int
    content_height_px: int


def plan_pdf_layout(
    content_width_px: int,
    content_height_px: int,
    options: PdfLayoutOptions | None = None,
) -> PdfLayoutPlan:
    """Use natural size and let Chromium paginate content taller than A4."""
    options = options or PdfLayoutOptions()
    options.validate()
    if content_width_px <= 0 or content_height_px <= 0:
        raise ValueError('content dimensions must be positive')

    return PdfLayoutPlan(
        # Width overflow is clipped by an A4 page; only vertical overflow
        # creates additional physical pages in Chromium.
        single_page_target=content_height_px <= options.usable_height_px,
        scale=1.0,
        content_width_px=content_width_px,
        content_height_px=content_height_px,
    )


class InvoicePdfRenderer:
    """Render local invoice HTML files to A4 PDF with one shared Chromium."""

    MEASURE_SCRIPT = """
    () => {
        const body = document.body;
        const root = document.documentElement;
        return {
            width: Math.ceil(Math.max(
                body ? body.scrollWidth : 0,
                body ? body.offsetWidth : 0,
                1,
            )),
            height: Math.ceil(Math.max(
                body ? body.scrollHeight : 0,
                body ? body.offsetHeight : 0,
                1,
            )),
        };
    }
    """

    def __init__(
        self,
        layout_options: PdfLayoutOptions | None = None,
        *,
        block_remote_requests: bool = True,
        load_timeout_ms: int = 15000,
        assets_dir: Path | str = DEFAULT_INVOICE_ASSETS_DIR,
    ) -> None:
        self.layout_options = layout_options or PdfLayoutOptions()
        self.layout_options.validate()
        if load_timeout_ms <= 0:
            raise ValueError('load_timeout_ms must be positive')
        self.block_remote_requests = block_remote_requests
        self.load_timeout_ms = load_timeout_ms
        self.assets_dir = Path(assets_dir)
        self.page_asset_css = self._build_page_asset_css()
        self.blocked_request_count = 0
        self._allowed_resource_roots: tuple[Path, ...] = ()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self) -> InvoicePdfRenderer:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:  # pragma: no cover - environment dependent
            raise RuntimeError(
                'playwright is required for PDF export; run '
                '"pip install playwright" and "playwright install chromium"'
            ) from error
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            viewport={
                'width': round(A4_WIDTH_INCHES * CSS_PIXELS_PER_INCH),
                'height': round(A4_HEIGHT_INCHES * CSS_PIXELS_PER_INCH),
            }
        )
        # Package HTML is untrusted portal input. Route every resource through
        # a local allowlist even when no remote request is expected.
        self._context.route('**/*', self._sandbox_resource_route)
        self._page = self._context.new_page()
        # page.pdf() lays out with print CSS; measuring under the same media
        # keeps the one-page scale decision aligned with the printed result.
        self._page.emulate_media(media='print')
        return self

    def _build_page_asset_css(self) -> str:
        """Embed the fixed invoice watermark and signed mark on every PDF page."""
        encoded_assets: dict[str, str] = {}
        for asset_name in (PAGE_BACKGROUND_ASSET, PAGE_SIGN_ASSET):
            asset_path = self.assets_dir / asset_name
            if not asset_path.is_file():
                raise FileNotFoundError(f'Invoice PDF asset is missing: {asset_path}')
            encoded_assets[asset_name] = base64.b64encode(
                asset_path.read_bytes()
            ).decode('ascii')

        # @page backgrounds are painted once for every physical page. Data URIs
        # make the result independent of the source HTML directory and ensure
        # the two controlled assets are embedded in the PDF.
        return f"""
        @page {{
            size: A4 portrait;
            background-image:
                url("data:image/jpeg;base64,{encoded_assets[PAGE_BACKGROUND_ASSET]}");
            background-repeat: no-repeat;
            background-position: center center;
            background-size: 7.2in 7.2in;
        }}
        html, body {{
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
        }}
        #{PAGE_SIGN_ELEMENT_ID} {{
            position: fixed;
            right: 0;
            bottom: 0;
            width: 0.22in;
            height: 0.22in;
            background: center / contain no-repeat
                url("data:image/jpeg;base64,{encoded_assets[PAGE_SIGN_ASSET]}");
        }}
        """

    def _install_page_sign_element(self) -> None:
        self._page.evaluate(
            """
            (elementId) => {
                if (document.getElementById(elementId)) return;
                const element = document.createElement('div');
                element.id = elementId;
                element.setAttribute('aria-hidden', 'true');
                (document.body || document.documentElement).appendChild(element);
            }
            """,
            PAGE_SIGN_ELEMENT_ID,
        )

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        for closer in (self._page, self._context, self._browser):
            if closer is not None:
                try:
                    closer.close()
                except Exception:  # pragma: no cover - shutdown best effort
                    logger.exception('Error while closing the PDF renderer')
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # pragma: no cover - shutdown best effort
                logger.exception('Error while stopping Playwright')
        self._page = self._context = self._browser = self._playwright = None
        return False

    def _sandbox_resource_route(self, route) -> None:
        if self._resource_url_is_allowed(route.request.url):
            route.continue_()
            return
        self.blocked_request_count += 1
        scheme = urlsplit(route.request.url).scheme.casefold() or 'none'
        logger.debug('Blocked invoice resource scheme=%s', scheme)
        route.abort()

    def _resource_url_is_allowed(self, resource_url: str) -> bool:
        parsed = urlsplit(resource_url)
        scheme = parsed.scheme.casefold()
        if scheme == 'data':
            return True
        if scheme != 'file' or parsed.netloc not in ('', 'localhost'):
            return False
        try:
            resource_path = Path(
                url2pathname(unquote(parsed.path))
            ).resolve(strict=False)
        except (OSError, ValueError):
            return False
        return any(
            resource_path == root or resource_path.is_relative_to(root)
            for root in self._allowed_resource_roots
        )

    def render_pdf(self, html_path: Path, pdf_path: Path) -> dict[str, Any]:
        if self._page is None:
            raise RuntimeError('InvoicePdfRenderer must be used as a context manager')
        html_path = Path(html_path)
        if not html_path.is_file():
            raise FileNotFoundError(f'Invoice HTML is missing: {html_path}')

        package_root = html_path.resolve().parent
        asset_root = self.assets_dir.resolve()
        self._allowed_resource_roots = (package_root, asset_root)

        self._page.goto(
            html_path.resolve().as_uri(),
            wait_until='load',
            timeout=self.load_timeout_ms,
        )
        self._page.add_style_tag(content=self.page_asset_css)
        self._install_page_sign_element()
        metrics = self._page.evaluate(self.MEASURE_SCRIPT)
        plan = plan_pdf_layout(
            int(metrics['width']), int(metrics['height']), self.layout_options
        )
        margin = f'{self.layout_options.margin_inches}in'
        pdf_path = Path(pdf_path)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        self._render_pdf_atomically(
            pdf_path,
            format='A4',
            print_background=True,
            scale=plan.scale,
            margin={'top': margin, 'bottom': margin, 'left': margin, 'right': margin},
            prefer_css_page_size=False,
        )
        result = {
            'pdf_path': str(pdf_path),
            'single_page_target': plan.single_page_target,
            'scale': plan.scale,
            'content_width_px': plan.content_width_px,
            'content_height_px': plan.content_height_px,
        }
        logger.debug(
            'Rendered invoice PDF html=%s single_page=%s scale=%.3f height_px=%d',
            html_path.name, plan.single_page_target, plan.scale,
            plan.content_height_px,
        )
        return result

    def _render_pdf_atomically(self, pdf_path: Path, **options: Any) -> None:
        pdf_path = Path(pdf_path)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f'.{pdf_path.name}.', suffix='.tmp', dir=pdf_path.parent
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            self._page.pdf(path=str(temporary_path), **options)
            with temporary_path.open('rb') as stream:
                header = stream.read(5)
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - 1024))
                trailer = stream.read()
                if header != b'%PDF-' or b'%%EOF' not in trailer:
                    raise RuntimeError('Rendered PDF failed structural validation')
                os.fsync(stream.fileno())
            os.replace(temporary_path, pdf_path)
            directory_fd = os.open(pdf_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary_path.unlink(missing_ok=True)
