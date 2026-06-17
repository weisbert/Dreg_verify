# -*- coding: utf-8 -*-
"""pytest 全局 path 设置。

红区脚本独立成 redzone_tools/ 后，集中把【仓库根】(dreg_verify 包) 和 【redzone_tools】
(scan_rtl / diag_rtl_binding 单文件脚本) 都加进 sys.path，使 `import dreg_verify` /
`import scan_rtl` / `import diag_rtl_binding` 在所有测试里都可用，省去每个测试文件各自改 path。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 仓库根
for _p in (_ROOT, os.path.join(_ROOT, "redzone_tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
