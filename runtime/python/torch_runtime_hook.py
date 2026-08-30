"""Prepare Windows' DLL loader before the frozen runtime imports Torch."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

if sys.platform == "win32" and getattr(sys, "frozen", False):
    torch_lib = Path(sys._MEIPASS) / "torch" / "lib"
    if torch_lib.is_dir():
        os.add_dll_directory(str(torch_lib))
        # Loading the OpenMP runtime before c10 avoids WinError 1114 when Qt's
        # runtime hook has changed the process DLL search order.
        ctypes.WinDLL(str(torch_lib / "libiomp5md.dll"))
        ctypes.WinDLL(str(torch_lib / "c10.dll"))
