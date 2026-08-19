from __future__ import annotations

import io
import re
import threading
from pathlib import Path

import torch
from defusedxml import ElementTree
from PIL import Image, ImageOps, UnidentifiedImageError
from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt5.QtGui import QGuiApplication, QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer
from torchvision import transforms

from app.captcha.model import DEVICE, OCRCNN


MODEL_PATH = (
    Path(__file__).resolve().parents[2]
    / 'resources'
    / 'models'
    / 'captcha'
    / 'captcha_login.pt'
)
MAX_SVG_BYTES = 1_000_000
MAX_SVG_DIMENSION = 4096
_LENGTH = re.compile(r'^\s*([0-9]+(?:\.[0-9]+)?)')


class CaptchaRenderError(RuntimeError):
    """Safe failure raised when source SVG cannot be rasterized."""


class CaptchaSolver:
    def __init__(self, model_path: Path = MODEL_PATH) -> None:
        checkpoint = torch.load(model_path, map_location=DEVICE, weights_only=True)

        self.charset = checkpoint['charset']
        self.image_width = checkpoint['image_width']
        self.image_height = checkpoint['image_height']
        text_length = checkpoint['fixed_text_length']

        self.model = OCRCNN(
            num_classes=len(self.charset),
            text_length=text_length,
        ).to(DEVICE)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()
        self._model_lock = threading.Lock()

        self.preprocess = transforms.Compose([
            transforms.Grayscale(num_output_channels=1),
            transforms.Resize((self.image_height, self.image_width)),
            transforms.Lambda(ImageOps.autocontrast),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.5], std=[0.5]),
        ])

    def solve(self, content: str | bytes) -> str:
        image = self._render_svg(content)
        try:
            return self._predict(image)
        finally:
            image.close()

    def _render_svg(self, content: str | bytes) -> Image.Image:
        if isinstance(content, str):
            svg_bytes = content.encode('utf-8')
        elif isinstance(content, bytes):
            svg_bytes = content
        else:
            raise TypeError('Captcha content must be str or bytes')
        if not svg_bytes.strip():
            raise CaptchaRenderError('Captcha SVG is empty')
        if len(svg_bytes) > MAX_SVG_BYTES:
            raise CaptchaRenderError('Captcha SVG exceeds the safe input limit')
        _validate_svg_dimensions(svg_bytes)

        try:
            png_bytes = _render_svg_with_qt(
                svg_bytes, self.image_width, self.image_height,
            )
        except Exception as error:
            raise CaptchaRenderError('Captcha SVG rendering failed') from error
        if not png_bytes:
            raise CaptchaRenderError('Captcha SVG renderer returned no image')

        try:
            with Image.open(io.BytesIO(png_bytes)) as rendered:
                rendered.load()
                image = rendered.convert('L')
        except (UnidentifiedImageError, OSError, ValueError) as error:
            raise CaptchaRenderError('Captcha rendered image is invalid') from error
        if image.width < 1 or image.height < 1:
            image.close()
            raise CaptchaRenderError('Captcha rendered image has invalid dimensions')
        return image


def _render_svg_with_qt(svg_bytes: bytes, width: int, height: int) -> bytes:
    """Windows-packaged SVG renderer; input was validated before this call."""
    if QGuiApplication.instance() is None:
        QGuiApplication([])
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    if not renderer.isValid():
        raise CaptchaRenderError('Captcha SVG renderer rejected input')
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    buffer = QBuffer()
    if not buffer.open(QIODevice.WriteOnly) or not image.save(buffer, 'PNG'):
        raise CaptchaRenderError('Captcha SVG renderer returned no image')
    return bytes(buffer.data())

    @torch.no_grad()
    def _predict(self, image: Image.Image) -> str:
        tensor = self.preprocess(image).unsqueeze(0).to(DEVICE)
        # Rendering/preprocessing is parallel; only shared model inference is serialized.
        with self._model_lock:
            output = self.model(tensor)
        pred_indices = output.argmax(dim=2)[0].cpu().tolist()
        return ''.join(self.charset[i] for i in pred_indices)


def _validate_svg_dimensions(svg_bytes: bytes) -> None:
    try:
        root = ElementTree.fromstring(svg_bytes)
    except Exception as error:
        raise CaptchaRenderError('Captcha SVG is malformed') from error
    if root.tag.rsplit('}', 1)[-1].casefold() != 'svg':
        raise CaptchaRenderError('Captcha content is not an SVG document')

    dimensions = [_numeric_length(root.get(name)) for name in ('width', 'height')]
    view_box = root.get('viewBox') or root.get('viewbox')
    if view_box:
        try:
            values = [float(value) for value in re.split(r'[\s,]+', view_box.strip())]
        except ValueError as error:
            raise CaptchaRenderError('Captcha SVG viewBox is invalid') from error
        if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
            raise CaptchaRenderError('Captcha SVG viewBox is invalid')
        dimensions.extend(values[2:])
    if any(value is not None and value > MAX_SVG_DIMENSION for value in dimensions):
        raise CaptchaRenderError('Captcha SVG dimensions exceed the safe limit')


def _numeric_length(value: str | None) -> float | None:
    if value is None or not value.strip() or value.strip().endswith('%'):
        return None
    match = _LENGTH.match(value)
    if match is None:
        raise CaptchaRenderError('Captcha SVG dimensions are invalid')
    number = float(match.group(1))
    if number <= 0:
        raise CaptchaRenderError('Captcha SVG dimensions are invalid')
    return number
