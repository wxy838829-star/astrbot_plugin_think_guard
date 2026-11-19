"""用真实 astrbot 类验证过滤结果（需要环境里有 astrbot）。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PKG = "astrbot_plugin_think_guard"

if PKG not in sys.modules:
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(PLUGIN_DIR)]
    pkg.__file__ = str(PLUGIN_DIR / "__init__.py")
    sys.modules[PKG] = pkg
sys.path.insert(0, str(PLUGIN_DIR.parent))
