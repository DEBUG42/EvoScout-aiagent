"""重定向 pytest 临时目录：D:/Temp/pytest-of-<中文用户名> 存在权限问题。"""
from __future__ import annotations

import tempfile
from pathlib import Path

_TMP = Path(__file__).resolve().parents[1] / "data" / "pytest-tmp"
_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TMP)
