# -*- coding: utf-8 -*-
"""
rtl_scan.py — Excel 侧的 RTL 扫描配套：把 Excel 信号转换成"需要在 ENV_RF 层级存在的网"清单。

RTL 解析的全部实现在 redzone_tools/scan_rtl.py（单文件、零第三方依赖，可直接拷到仿真服务器/红区）。
本模块复用其实现，并补充需要 dreg_verify/openpyxl 的部分：
    collect_excel_nets(wb)  Excel → {网名: 用途}
    match_excel(wb, sigmap) 对照 Excel 与 RTL 层级

跨机器两段式工作流（Excel 在 Windows、RTL 在 Linux 服务器）见 scan_rtl.py 文件头。
"""

import os
import re
import sys

# redzone_tools/ 加入 path，导入单文件版 scan_rtl 的解析实现（红区脚本已独立成该文件夹）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "redzone_tools"))
from scan_rtl import (KEYWORDS, build_signal_map, find_verilog_files,            # noqa: F401,E402
                      match_nets, parse_modules, parse_nets_text,
                      render_nets_text, render_prefix_file, scan_files,
                      strip_comments)


def _sig_filter(signals):
    """signals=None → 收全部；否则归一成小写集合，只收 out_base 命中的信号(C3 可选过滤)。"""
    if signals is None:
        return None
    return {str(s).strip().lower() for s in signals}


def collect_excel_nets(wb, signals=None):
    """Excel → 需要在 ENV_RF 层级存在的网名集合：

    ① 每个输出的 RTL 网名 —— assert 探针：
         top_output=1 → K 列名（ls 行带 _ls 后缀）
         top_output=0 → K 列名 + _to_logic（在 sig_logic 模块内部，需要探针前缀才能验）
    ② 它们的 force 输入（RO/wire 兜底/级联）—— force 路径
    RW 寄存器输入走 RF_WRITE，不需要层级。返回 {网名: 用途说明}。

    ⭐ 两种级联模式(cone 展开上游 / force 级联网)需要的网都导出——
    一次 RTL 扫描同时覆盖两种模式，之后在 GUI/CLI 里切换模式不用重新扫。

    signals(C3 可选过滤,2026-06-23)：只导这些信号(按 out_base)的网；None=全导(逐字节不变)。
    新 Topout 主流程基本不需 nets(顶层无前缀)，唯一需要=单独测某页/某信号(块D2)→按需过滤。
    """
    from . import expr as E
    from . import generator
    from . import resolver as R

    want = _sig_filter(signals)
    nets = {}
    for sig in wb.logic:
        if want is not None and sig.out_base.lower() not in want:
            continue
        # 探针网名用默认尾缀(LPBT 约定的真实网名)，显式设定使其不受同进程内先前 Resolver 的
        # append_to_logic 开关残留影响（scan 找的是 RTL 真实网名；--no-to-logic-suffix 不经此路）。
        sig._append_to_logic = True
        kind = "" if sig.is_top else "（内部信号）"
        nets.setdefault(sig.rtl_base, "输出 %s 的 assert 探针%s" % (sig.out_name, kind))
    # 两种级联模式各跑一遍解析，导出网取并集
    for mode in ("cone", "force"):
        resolver = R.Resolver(wb, cascade_mode=mode)
        tag = "" if mode == "cone" else "（force级联网模式）"
        for sig in wb.logic:
            if want is not None and sig.out_base.lower() not in want:
                continue
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


def collect_mux_nets(wb, signals=None):
    """mux 页 → 需要在 ENV_RF 层级核对/定位的网（2026-06-03 第十四轮：WL_RFTRX 多控制 + 非顶层输出）。

    signals(C3 可选过滤)：只导这些 mux 组(按 G 列 out_base)的网；None=全导(逐字节不变)。

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

    want = _sig_filter(signals)
    nets = {}
    try:
        groups = getattr(wb, "mux", None) or []
        for grp in groups:
            if want is not None and grp.out_base.lower() not in want:
                continue
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


def collect_dft_nets(wb, signals=None):
    """dft 页 → iddq 门网（2026-06-10 Hi1108：IDDQ 漏电态拍要 force 门网，此前不导出——
    门若埋在子模块，scan_rtl 定不到层级 → force CUVUNF 且无提示）。

    导出门基名（force 目标）+ _to_dft 衔接网（核对用，宁多勿漏，同 mux 网原则）。
    无 dft 页 / 任何异常 → 返回空 dict，绝不波及 logic/mux 网导出。

    signals(C3 可选过滤)：只导这些被门控输出(dft D 列输出基名)的门网；None=全导(逐字节不变)。
    """
    want = _sig_filter(signals)
    nets = {}
    try:
        for ob, g in sorted((getattr(wb, "dft", None) or {}).items()):
            if want is not None and str(ob).lower() not in want:
                continue
            gb, graw = g.get("gate_base"), g.get("gate_raw")
            if gb and re.match(r"^[A-Za-z_]\w*$", gb):
                nets.setdefault(gb, "dft 页 iddq 门（IDDQ 漏电态拍的 force 目标；门控输出 %s 等）" % ob)
            if graw and graw != gb and re.match(r"^[A-Za-z_]\w*$", graw):
                nets.setdefault(graw, "dft 页 iddq 门的 _to_dft 衔接网（核对 DFT mux 接线用）")
    except Exception:  # noqa: BLE001
        return {}
    return nets


def collect_topout_nets(wb, signals=None):
    """Topout 页 → 每个【可验证】Topout 信号的 assert 探针网（2026-06-25）。

    ⭐ 这是 Topout-rooted 主流程真正需要的网清单。以前 collect_excel_nets/mux/dft 只遍历
    logic/mux/dft 页 —— **直连寄存器(RW)根 / dft 改名根** 的探针网（断言贴 topo 名 / probe_name，
    不在 wb.logic）整类漏导 → scan_rtl 不检查它们 → 这些信号埋子模块时仿真 CUVUNF 且无提示
    （aac_ctf_bit_sel 正是此类：register 直连根，断言探 `ENV_RF.aac_ctf_bit_sel`，net 埋在
    U_BT_LP_PLL_DIG 里 → 老 nets.txt 根本没它 → 跑 scan_rtl 也不会帮它找前缀）。

    按 resolve_root 分类取断言 LHS 网名（与 build_for_topout 的 .sv 同口径）：
      · logic / mux 根（未改名）→ 源对象 rtl_base（generator.build 探它）
      · 直连寄存器(RW)根       → topo 名（_register_passthrough_block 探它）
      · dft 改名根             → probe_name（= 顶层真名 = topo 名）
      · RO 回读 / 未解析 / error → 不产断言、无探针 → 跳过

    signals(可选过滤)：只导这些 Topout 名(按基名)的网；None=全导。
    无 Topout 页 / 任何异常 → 返回空 dict，绝不波及其它页导出。
    """
    nets = {}
    want = _sig_filter(signals)
    try:
        from . import topout as T
        from .excel_model import _strip_width
        logic_idx, mux_idx = T.build_index(wb)
        for t in (getattr(wb, "topout", None) or []):
            tb = _strip_width(t.name)[0]
            if want is not None and tb.lower() not in want:
                continue
            try:
                root = T.resolve_root(wb, t.name, logic_idx, mux_idx)
            except Exception:  # noqa: BLE001  解析失败的单信号跳过，不连累整批
                continue
            if root is None:
                continue
            net, why = None, None
            if root.renamed:                       # dft 改名根：断言贴顶层真名 probe_name(=topo 名)
                net = _strip_width(root.probe_name or t.name)[0]
                why = "Topout 改名根 %s 的 assert 探针（顶层真名）" % t.name
            elif root.kind == T.REGISTER:          # 直连寄存器(RW)根：断言探 topo 名
                net, why = tb, "Topout 直连寄存器根 %s 的 assert 探针" % t.name
            elif root.kind in (T.LOGIC, T.MUX) and root.obj is not None:
                try:
                    if root.kind == T.LOGIC:
                        root.obj._append_to_logic = True   # 找 RTL 真名(与 collect_excel_nets 同口径)
                    net = root.obj.rtl_base
                except Exception:  # noqa: BLE001
                    net = None
                why = "Topout %s 根 %s 的 assert 探针" % (root.kind, t.name)
            # RO_READBACK / UNRESOLVED → 不产断言，不导
            if net and re.match(r"^[A-Za-z_]\w*$", net):
                nets.setdefault(net, why)
    except Exception:  # noqa: BLE001  无 Topout 页 / 解析层异常 → 空，绝不波及其它页
        return {}
    return nets


def filter_nets_by_dest(nets, dest_suffix):
    """按【目的地后缀】过滤网集合（C3，Q3 后缀规则）：只保留网名以 `_<dest_suffix>` 结尾的。

    Q3：同一源 A 可扇出多消费方(A_to_dft / A_to_iddq)——单独测某一页时，用 dest_suffix='to_dft'
    只导 *_to_dft 那批衔接网(而非 A_to_iddq)。dest_suffix 容『to_dft』或『_to_dft』两种写法。
    dest_suffix 为空 → 原样返回。"""
    if not dest_suffix:
        return dict(nets)
    suf = str(dest_suffix).strip().lower()
    if not suf.startswith("_"):
        suf = "_" + suf
    return {n: u for n, u in nets.items() if n.lower().endswith(suf)}


def collect_nets(wb, signals=None, pages=None, dest_suffix=None):
    """统一【可选过滤】网导出（C3 块D2，2026-06-23）：合并 logic/mux/dft 三页网，按需过滤。

    signals    — 只导这些信号(out_base/G 列基名/dft 输出基名/Topout 名)的网；None=全部。
    pages      — 取这些页：{'topout','logic','mux','dft'} 的子集；None=四页全取。
    dest_suffix— 只留以 `_<dest_suffix>` 结尾的衔接网(同源多目的地时选一页)；None=不按后缀过滤。

    返回 {网名: 用途}。Topout 页(2026-06-25)= 每个可验证 Topout 信号的 assert 探针网，含
    寄存器/dft 直连根(logic/mux/dft 三页都遍历不到的那批) —— 勾选『仅 Topout』即只导这批。
    旧全导调用方(collect_excel_nets() 无参)逐字节不变；本函数是新增叠加层。"""
    want_pages = None if pages is None else {str(p).strip().lower() for p in pages}

    def _on(p):
        return want_pages is None or p in want_pages

    nets = {}
    if _on("logic"):
        nets.update(collect_excel_nets(wb, signals=signals))
    if _on("mux"):
        for k, v in collect_mux_nets(wb, signals=signals).items():
            nets.setdefault(k, v)
    if _on("dft"):
        for k, v in collect_dft_nets(wb, signals=signals).items():
            nets.setdefault(k, v)
    if _on("topout"):                    # Topout 探针网(寄存器/dft 直连根的探针在此补齐)
        for k, v in collect_topout_nets(wb, signals=signals).items():
            nets.setdefault(k, v)
    return filter_nets_by_dest(nets, dest_suffix)


def match_excel(wb, sigmap):
    """对照：Excel 需要的网 vs RTL 层级。返回 (prefixes, at_top, missing)，见 scan_rtl.match_nets。"""
    return match_nets(collect_excel_nets(wb), sigmap)
