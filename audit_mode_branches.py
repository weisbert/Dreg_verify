# -*- coding: utf-8 -*-
"""audit_mode_branches.py — 一次性列出【所有】受 *_mode(线控/功能) 二分支影响、且 mode=0 那半张表
可能漏生成的 mux 信号。专治"盲人摸象"：不靠一个个偶然发现，而是从 Excel 把它们全枚举出来。

原理（band_trim / mixer2g_cap_sw 都是同一形态）：
  某 trim/cap/sel 信号 ←(控制,可能切片) 某 mux ←…← 【mode mux】
     mode mux 的两个 case：mode=1 → *_local(RW 可写) ；mode=0 → linectrl_*/线控(RO 或 logic→mux 网)
  生成器主载体走 mode=1(RW) 没问题；mode=0 只有当线控源能 force 到时才生成：
     · RO 寄存器(tmm/regmap)            → force 信号名即可，能生成（如 tsensor）
     · 已配 scan_rtl 探针前缀(prefixed) → 能生成
     · logic→mux/级联衔接网(needs-prefix) → 必须先配探针前缀，否则 mode=0 缺失（如 linectrl_band_sel）
     · 探不到(unresolved)               → 无法生成

读 Excel 的三样（都已被 excel_model 解析，无需新读）：
  ① mux 页 A/B~E/G  → 级联图（谁的控制是另一个 mux 的输出）
  ② regmap/tmm F+描述 → 每个源是 RW 还是 RO/线控（mode 寄存器描述常含 "0: line control"）
  ③ logic 页 K+M=to_mux → 哪些源是 logic→mux 网（needs-prefix）

用法:  python audit_mode_branches.py <excel.xlsx> [--prefix-config a=ENV_RF.x,b=ENV_RF.y]
       (--prefix-config 把已知探针前缀喂进来，验证配上后是否就能生成 mode=0)
"""

import argparse
import sys

from dreg_verify import excel_model as M, resolver as R
from dreg_verify.mux_gen import MUX_CASCADE_MAX_DEPTH

# Windows 控制台常是 GBK，打印中文会崩；统一改 UTF-8（与 inspect_*.py 同口径）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def safe_print(s):
    try:
        print(s)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(s.encode(enc, "replace").decode(enc, "replace"))


def _is_rw(b):
    return b.kind == "RW" and b.address is not None


def _classify(b):
    """返回 (类别, 中文说明, mode0 能否生成)。"""
    if _is_rw(b):
        return "rw", "RW寄存器·可写", None                       # 这是 mode=1 主载体，不是 mode=0 源
    if b.found_in in ("tmm", "regmap"):
        return "ro-reg", "RO寄存器·force名即可", True
    if b.found_in == "prefixed-wire":
        return "prefixed", "已配探针前缀·可force", True
    if b.found_in in ("needs-prefix", "mux-output"):
        return "needs-prefix", "线控/级联衔接网·需scan_rtl探针前缀", False
    if b.found_in == "wire":
        return "wire", "wire兜底·按名force(未必探得到)", False
    return "unresolved", "探不到/未解析(%s)" % b.found_in, False


def _resolve_source(resolver, case):
    info = {"raw": case.input_raw, "base": case.input_base, "width": case.input_width,
            "msb": case.input_msb, "lsb": case.input_lsb}
    return resolver.resolve("audit", info)


def _mux_by_out(wb):
    idx = {}
    for g in wb.mux:
        idx.setdefault(g.out_base.lower(), g)
    return idx


def _walk_cascade(wb, idx, start, _seen=None, _depth=1):
    """从 start mux 出发，沿控制列(B~E)找上游 mux，返回 [(depth, upstream_mux, 控制基名), ...]。"""
    if _seen is None:
        _seen = set()
    out = []
    if _depth > MUX_CASCADE_MAX_DEPTH:
        return out
    for c in start.ctrls:
        up = idx.get(c.base.lower())
        if up is None or up.out_base.lower() in _seen:
            continue
        _seen.add(up.out_base.lower())
        out.append((_depth, up, c.base))
        out.extend(_walk_cascade(wb, idx, up, _seen, _depth + 1))
    return out


def audit(wb, resolver):
    """返回受 mode 二分支影响的下游 mux 列表（每条 = 一个 (下游信号, mode mux, mode=0 源, 状态)）。"""
    idx = _mux_by_out(wb)
    rows = []
    for g in wb.mux:
        chain = _walk_cascade(wb, idx, g)
        for depth, up, via in chain:
            # 这一层是不是 mode mux？= 数据源里既有 RW(local) 又有 非RW(线控/logic)
            srcs = [(_resolve_source(resolver, c), c) for c in up.cases]
            rw_src = [(b, c) for b, c in srcs if _is_rw(b)]
            line_src = [(b, c) for b, c in srcs if not _is_rw(b)]
            if not (rw_src and line_src):
                continue                                  # 不是功能/线控二分支，跳过
            for b, c in line_src:
                kind, desc, coverable = _classify(b)
                # 深层级联(mode mux 在第2+层)已由生成器递归传播 use_alt 支持(第二十四轮)，深度仅信息标注。
                # mode=0 源是 logic→mux 线控网(needs-prefix)也【照样生成】(裸名 force,bare-probe 同口径)，
                # 只是 force 层级前缀待 scan_rtl 补——标 [!] 警告(已生成,可能要前缀),不再当 [XX] 缺失。
                deep = "（深层级联）" if depth > 1 else ""
                if coverable:
                    status = "[OK] mode=0 已生成" + deep
                elif kind == "needs-prefix":
                    status = "[!] mode=0 已生成(裸名 force)·仿真若 CUVUNF 跑 scan_rtl 配前缀" + deep
                else:
                    status = "[XX] mode=0 无法生成·%s%s" % (desc, deep)
                rows.append({
                    "signal": g.out_name, "owner": g.owner or "",
                    "mode_mux": "mux%s %s" % (up.group_no, up.out_base),
                    "depth": depth, "via": via,
                    "mode0_src": b.base, "mode0_kind": kind, "mode0_desc": desc,
                    "status": status,
                })
    return rows


def main():
    ap = argparse.ArgumentParser(description="一次性枚举 mode=0 漏生成的 mux")
    ap.add_argument("excel")
    ap.add_argument("--prefix-config", default="",
                    help="已知探针前缀: base=ENV_RF.x,base2=ENV_RF.y（验证配上后能否生成 mode=0）")
    ap.add_argument("--out", default=None, help="同时写报告到此 txt（默认 <excel名>_mode_audit.txt）")
    args = ap.parse_args()

    wb = M.load_workbook(args.excel)
    prefixes = {}
    for kv in args.prefix_config.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            prefixes[k.strip().lower()] = v.strip()
    resolver = R.Resolver(wb, wire_prefixes=prefixes)

    out = []
    rows = audit(wb, resolver)
    if not rows:
        out.append("未发现受 *_mode 二分支影响的下游 mux（或全部 mode=0 已可生成）。")
    else:
        order = {"[X": 0, "[!": 1, "[O": 2}
        rows.sort(key=lambda r: (order.get(r["status"][:2], 9), r["signal"]))
        n_gap = sum(1 for r in rows if r["status"].startswith("[XX]"))
        n_bare = sum(1 for r in rows if r["status"].startswith("[!]"))
        n_ok = sum(1 for r in rows if r["status"].startswith("[OK]"))
        out.append("受 *_mode 二分支影响的下游 mux：%d 条  |  [XX]无法生成 %d  [!]已生成(裸名,可能要前缀) %d  [OK]已生成 %d\n"
                   % (len(rows), n_gap, n_bare, n_ok))
        for r in rows:
            out.append("%s" % r["status"])
            out.append("    信号: %s   owner: %s" % (r["signal"], r["owner"]))
            out.append("    mode mux: %s   (下游经 %s 第%d层级联)" % (r["mode_mux"], r["via"], r["depth"]))
            out.append("    mode=0 源: %s   [%s]\n" % (r["mode0_src"], r["mode0_desc"]))
        need_prefix = sorted({r["mode0_src"] for r in rows if r["mode0_kind"] == "needs-prefix"})
        if need_prefix:
            out.append("──> 这些 mode=0 已按【裸名 force】生成 testcase；若仿真在对应 force 报 CUVUNF，")
            out.append("    给下面的线控源配 scan_rtl 探针前缀(--probe-prefix 名=层级)重生成即为正确层级：")
            out.extend("      %s" % s for s in need_prefix)

    text = "\n".join(out)
    out_path = args.out or (args.excel.rsplit(".", 1)[0] + "_mode_audit.txt")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        out_path = None
    safe_print(text)
    if out_path:
        safe_print("\n报告已写: %s" % out_path)


if __name__ == "__main__":
    main()
