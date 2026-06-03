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
import re
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


def collect_mux_nets(wb):
    """mux 页 → 需要在 ENV_RF 层级核对/定位的网（2026-06-03 第十四轮：WL_RFTRX 多控制 + 非顶层输出）。

    从已装载的工作簿 wb（excel_model.DregWorkbook）的 wb.mux 取数据——不再二次打开 Excel，
    自动拿到 read_mux 的多控制 / 全局归并能力。导出三类网：
      ① 每组 G 列输出基名 —— assert 探针（WL 输出全不在顶层，scan_rtl 必须找到它们的层级）
      ② 每组每个控制信号（B/C/D/E 列）的 _to_mux 衔接网 —— 不管控制来源是什么都导
         （寄存器直出 / logic 行 / 上游 mux 输出三种来源，force 级联或 scan 核对都用得上）
      ③ 每组每个 case 数据输入的基名 + _to_mux 衔接网 —— 线控（RO）数据是 force 目标，
         本地（RW）数据虽走 RF_WRITE 不 force，但衔接网多导无害（scan 找不到只落"顶层直达/找不到"段）

    宁多勿漏：scan_rtl 扫描时找不到的网会落"顶层直达 / 找不到"段，多导一些网完全无副作用，
    漏导才会导致仿真 force 路径 CUVUNF（且无任何报错提示）。用途说明（dict 的 value）用中文写清
    每个网的角色（输出探针 / 控制衔接网 / 数据 force 网），方便用户看 nets.txt / probe_prefixes.txt。

    wb.mux 为空（纯 logic 表 / 无 mux 页）→ 返回空 dict；
    任何异常 try/except 兜底返回空 dict——绝不因 mux 页问题波及 logic 网导出。
    """
    from . import excel_model as M

    def strip_width(text):
        """只去位宽尾巴 [msb:lsb]/[bit]，保留 _to_mux 后缀（衔接网真名带后缀）。"""
        return M._strip_width(text)[0]

    def valid_net(name):
        """合法信号名才导出（过滤 '(reserved)' 等占位）。"""
        return bool(re.match(r"^[A-Za-z_]\w*$", name or ""))

    nets = {}
    try:
        groups = getattr(wb, "mux", None) or []
        for grp in groups:
            # ① 输出探针网（G 列基名，已去位宽）——WL 输出不在顶层，靠 scan_rtl 定层级
            if valid_net(grp.out_base):
                nets.setdefault(grp.out_base,
                                "mux 组%s 输出 %s 的 assert 探针（输出不在顶层，需定位层级）"
                                % (grp.group_no, grp.out_name))
            # ② 控制衔接网（B/C/D/E 各控制信号的 _to_mux 网，剥位宽，保留后缀）
            for ctrl in grp.ctrls:
                net = strip_width(ctrl.raw)
                if valid_net(net):
                    nets.setdefault(net,
                                    "mux 组%s 控制信号 %s 的衔接网（%s 列，控制 mux 选路）"
                                    % (grp.group_no, ctrl.base, ctrl.letter))
            # ③ 数据输入网（每个 case：寄存器基名 + _to_mux 衔接网）
            for case in grp.cases:
                if valid_net(case.input_base):
                    nets.setdefault(case.input_base,
                                    "mux 组%s 数据输入 %s（线控数据=force 目标；本地数据走 RF_WRITE）"
                                    % (grp.group_no, case.input_base))
                conn = strip_width(case.input_raw)
                if conn != case.input_base and valid_net(conn):
                    nets.setdefault(conn,
                                    "mux 组%s 数据衔接网 %s（数据→mux 衔接核对）"
                                    % (grp.group_no, conn))
    except Exception:  # noqa: BLE001  mux 页任何异常都不得波及 logic 网导出
        return {}
    return nets


def match_excel(wb, sigmap):
    """对照：Excel 需要的网 vs RTL 层级。返回 (prefixes, at_top, missing)，见 scan_rtl.match_nets。"""
    return match_nets(collect_excel_nets(wb), sigmap)
