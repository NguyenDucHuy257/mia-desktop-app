import torch
import torch.nn as nn

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class OCRCNN(nn.Module):
    def __init__(self, num_classes: int, text_length: int) -> None:
        super().__init__()
        self.text_length = text_length
        self.num_classes = num_classes

        self.features = nn.Sequential(
            nn.Conv2d(1, 32, kernel_size=3, padding=1),      # 0
            nn.BatchNorm2d(32),                              # 1
            nn.ReLU(inplace=True),                           # 2
            nn.Conv2d(32, 32, kernel_size=3, padding=1),     # 3
            nn.BatchNorm2d(32),                              # 4
            nn.ReLU(inplace=True),                           # 5
            nn.MaxPool2d(2),                                 # 6
            nn.Conv2d(32, 64, kernel_size=3, padding=1),     # 7
            nn.BatchNorm2d(64),                              # 8
            nn.ReLU(inplace=True),                           # 9
            nn.Conv2d(64, 64, kernel_size=3, padding=1),     # 10
            nn.BatchNorm2d(64),                              # 11
            nn.ReLU(inplace=True),                           # 12
            nn.MaxPool2d(2),                                 # 13
            nn.Conv2d(64, 128, kernel_size=3, padding=1),    # 14
            nn.BatchNorm2d(128),                             # 15
            nn.ReLU(inplace=True),                           # 16
            nn.Conv2d(128, 128, kernel_size=3, padding=1),   # 17
            nn.BatchNorm2d(128),                             # 18
            nn.ReLU(inplace=True),                           # 19
            nn.MaxPool2d(2),                                 # 20
            nn.Conv2d(128, 256, kernel_size=3, padding=1),   # 21
            nn.BatchNorm2d(256),                             # 22
            nn.ReLU(inplace=True),                           # 23
            nn.Conv2d(256, 512, kernel_size=3, padding=1),   # 24
            nn.BatchNorm2d(512),                             # 25
            nn.ReLU(inplace=True),                           # 26
        )

        self.pool = nn.AdaptiveAvgPool2d((1, text_length))

        self.classifier = nn.Sequential(
            nn.Dropout(0.3),                                 # 0
            nn.Linear(512, 256),                             # 1
            nn.ReLU(inplace=True),                           # 2
            nn.Linear(256, num_classes),                     # 3
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.pool(x)                       # (batch, 512, 1, text_length)
        x = x.squeeze(2).permute(0, 2, 1)      # (batch, text_length, 512)
        x = self.classifier(x)                 # (batch, text_length, num_classes)
        return x
