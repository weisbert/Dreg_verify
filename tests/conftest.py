# -*- coding: utf-8 -*-
"""pytest 全局 path 设置 + 契约 marker。

红区脚本独立成 redzone_tools/ 后，集中把【仓库根】(dreg_verify 包) 和 【redzone_tools】
(scan_rtl / diag_rtl_binding 单文件脚本) 都加进 sys.path，使 `import dreg_verify` /
`import scan_rtl` / `import diag_rtl_binding` 在所有测试里都可用，省去每个测试文件各自改 path。

`@pytest.mark.contract("C-042", ...)` —— GUI v2 能力契约 ID（执行计划 §2 L3）。
marker 里的 ID 会写进 junit 的 `<properties>`，供 `tools/contract_check.py` 读；
另一条挂钩方式是把 ID 写进测试函数名（`test_c042_xxx`），不需要 marker。
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 仓库根
for _p in (_ROOT, os.path.join(_ROOT, "redzone_tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


_CONTRACT_ID = re.compile(r"[Cc][-_ ]?(\d{3,})")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        'contract(*ids): 关联 docs/GUI_v2_能力契约.csv 的能力 ID，如 contract("C-042", "C-043")')


def pytest_runtest_makereport(item, call):
    """把 contract marker 里的 ID 塞进 user_properties → junit `<properties>`。

    返回 None（不接管报告生成），只借这个钩子在报告成形前挂上属性。"""
    ids = []
    for mark in item.iter_markers("contract"):
        for a in mark.args:
            m = _CONTRACT_ID.fullmatch(str(a).strip())
            if m:
                cid = "C-%s" % m.group(1)
                if cid not in ids:
                    ids.append(cid)
    if ids:
        prop = ("contract_ids", ";".join(ids))
        if prop not in item.user_properties:
            item.user_properties.append(prop)
    return None
