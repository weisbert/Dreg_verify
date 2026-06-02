# -*- coding: utf-8 -*-
"""
rtl_scan.py — Excel 侧的 RTL 扫描配套：把 Excel 信号转换成"需要在 ENV_RF 层级存在的网"清单。

RTL 解析的全部实现在仓库根目录的 scan_rtl.py（单文件、零第三方依赖，可直接拷到仿真服务器）。
本模块复用其实现，并补充需要 dreg_verify/openpyxl 的部分：
    collect_excel_nets(wb)  Excel → {网名: 用途}
    match_excel(wb, sigmap) 对照 Excel 与 RTL 层级

跨机器两段式工作流（Excel 在 Windows、RTL 在 Linux 服务器）见 scan_rtl.py 文件头。
"""

import os
import sys

# 仓库根目录加入 path，导入单文件版 scan_rtl 的解析实现
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scan_rtl import (KEYWORDS, build_signal_map, find_verilog_files,            # noqa: F401,E402
                      match_nets, parse_modules, parse_nets_text,
                      render_nets_text, render_prefix_file, scan_files,
                      strip_comments)


def collect_excel_nets(wb):
    """Excel → 需要在 ENV_RF 层级存在的网名集合：

    ① 每个输出的 RTL 网名 —— assert 探针：
         top_output=1 → K 列名（ls 行带 _ls 后缀）
         top_output=0 → K 列名 + _to_logic（在 sig_logic 模块内部，需要探针前缀才能验）
    ② 它们的 force 输入（RO/wire 兜底/级联）—— force 路径
    RW 寄存器输入走 RF_WRITE，不需要层级。返回 {网名: 用途说明}。

    ⭐ 两种级联模式(cone 展开上游 / force 级联网)需要的网都导出——
    一次 RTL 扫描同时覆盖两种模式，之后在 GUI/CLI 里切换模式不用重新扫。
    """
    from . import expr as E
    from . import generator
    from . import resolver as R

    nets = {}
    for sig in wb.logic:
        kind = "" if sig.is_top else "（内部信号）"
        nets.setdefault(sig.rtl_base, "输出 %s 的 assert 探针%s" % (sig.out_name, kind))
    # 两种级联模式各跑一遍解析，导出网取并集
    for mode in ("cone", "force"):
        resolver = R.Resolver(wb, cascade_mode=mode)
        tag = "" if mode == "cone" else "（force级联网模式）"
        for sig in wb.logic:
            try:
                node, bindings, _ = generator.expand_signal(wb, resolver, sig)
            except Exception:  # noqa: BLE001  cone 失败时退回原始绑定
                try:
                    node = E.parse(sig.expr)
                    bindings = resolver.resolve_signal_inputs(sig)
                except Exception:  # noqa: BLE001
                    continue
            for b in bindings.values():
                if b.kind == "RO":
                    base = b.wire.split(".")[-1]
                    nets.setdefault(base, "输出 %s 的输入 %s (force)%s"
                                    % (sig.out_name, b.base, tag))
    return nets


def match_excel(wb, sigmap):
    """对照：Excel 需要的网 vs RTL 层级。返回 (prefixes, at_top, missing)，见 scan_rtl.match_nets。"""
    return match_nets(collect_excel_nets(wb), sigmap)
