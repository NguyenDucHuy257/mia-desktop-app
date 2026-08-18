import io
from pathlib import Path

import torch
from PIL import Image, ImageOps
from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt5.QtGui import QGuiApplication, QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer
from torchvision import transforms

from app.captcha.model import DEVICE, OCRCNN

MODEL_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "models"
    / "captcha"
    / "captcha_login.pt"
)


class CaptchaSolver:
    def __init__(self, model_path: Path = MODEL_PATH) -> None:
        checkpoint = torch.load(model_path, map_location=DEVICE, weights_only=False)

        self.charset = checkpoint["charset"]
        self.image_width = checkpoint["image_width"]
        self.image_height = checkpoint["image_height"]
        text_length = checkpoint["fixed_text_length"]

        self.model = OCRCNN(
            num_classes=len(self.charset),
            text_length=text_length,
        ).to(DEVICE)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.eval()

        self.preprocess = transforms.Compose([
            transforms.Grayscale(num_output_channels=1),
            transforms.Resize((self.image_height, self.image_width)),
            transforms.Lambda(ImageOps.autocontrast),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.5], std=[0.5]),
        ])

    def solve(self, content: str | bytes) -> str:
        image = self._render_svg(content)
        return self._predict(image)

    def _render_svg(self, content: str | bytes) -> Image.Image:
        if isinstance(content, str):
            svg_bytes = content.encode("utf-8")
        elif isinstance(content, bytes):
            svg_bytes = content
        else:
            raise TypeError("Captcha content must be str or bytes")

        # QPainter on a QImage needs a running QGuiApplication instance.
        if QGuiApplication.instance() is None:
            QGuiApplication([])

        renderer = QSvgRenderer(QByteArray(svg_bytes))
        size = renderer.defaultSize()
        width = size.width() or self.image_width
        height = size.height() or self.image_height

        qimage = QImage(width, height, QImage.Format_ARGB32)
        qimage.fill(Qt.white)

        painter = QPainter(qimage)
        renderer.render(painter)
        painter.end()

        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        qimage.save(buffer, "PNG")
        png_bytes = bytes(buffer.data())

        return Image.open(io.BytesIO(png_bytes)).convert("L")

    @torch.no_grad()
    def _predict(self, image: Image.Image) -> str:
        tensor = self.preprocess(image).unsqueeze(0).to(DEVICE)
        output = self.model(tensor)
        pred_indices = output.argmax(dim=2)[0].cpu().tolist()
        return "".join(self.charset[i] for i in pred_indices)
