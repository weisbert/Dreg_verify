# -*- coding: utf-8 -*-
"""
diag_signal_expand.py — 单信号「为什么真值表是 N 个输入」诊断（R38 跨 logic↔mux 边界展开）。

回答：某 logic 信号在【展开上游(cone)】模式下，真值表到底有几个输入、上游 mux 是否被
端到端展开（对齐 VBA）；若没展开，给出【具体原因】（宽 select / case 位宽不符 / 有 hole /
超深 / 成环），而不是泛泛的「需探针前缀」。

纯 Excel 解析，不需要探针前缀（cone 展开 mux 靠源寄存器，本就不需要前缀）。

用法：
    python tools/diag_signal_expand.py 真表.xlsx d_wl_rf_lo2g5g_bias_en
    python tools/diag_signal_expand.py 真表.xlsx d_wl_rf_lo2g5g_bias_en --out diag.txt
"""
import argparse
import sys

for _s in (sys.stdout, sys.stderr):          # Windows 控制台常是 GBK，中文会崩
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

import os, sys  # noqa: E402,F811 —— path bootstrap：tools/ 下脚本上溯仓库根、找到 dreg_verify 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dreg_verify import excel_model, expr as E, generator, mux_gen, resolver as R


def _expand_reason(group):
    """mux_expandable 为 False 时的人话原因（与 mux_gen.mux_expandable 三条门同口径）。"""
    ctrls = getattr(group, "ctrls", None) or []
    cases = getattr(group, "cases", None) or []
    if not ctrls:
        return "无控制信号"
    if not cases:
        return "无 case"
    wide = [c.base for c in ctrls if (c.width or 1) > 2]
    if wide:
        return "宽 select(>2bit)：%s —— 会假绿，本轮保守不展(下轮按真实 case 值枚举)" % ", ".join(wide)
    total_w = group.ctrl_total_width
    covered = set()
    for case in cases:
        try:
            cv, cw, dc = E.parse_case_literal(case.case_raw)
        except E.ExprError:
            return "case 值 %r 解析失败" % case.case_raw
        if cw != total_w:
            return "case 位宽 %d ≠ 控制拼接总宽 %d（case=%r）" % (cw, total_w, case.case_raw)
        covered.update(E.expand_case_values(cv, cw, dc))
    miss = sorted(set(range(1 << total_w)) - covered)
    if miss:
        return "有 hole：控制取值 %s 不命中任何 case —— 会假红(真 RTL 是 latch/x)，本轮保守不展" % miss
    return "（可展开，不该走到这里）"


def _inputs(wb, mode, signame):
    res = R.Resolver(wb)
    res.cascade_mode = mode
    sig = next((s for s in wb.logic if s.out_name.lower() == signame.lower()
                or s.out_base.lower() == signame.lower()), None)
    if sig is None:
        return None, None, None
    node, bindings, _exp = generator.expand_signal(wb, res, sig)
    bases = sorted({b.base for b in bindings.values()
                    if b is not None and getattr(b, "base", None)})
    return sig, bases, bindings


def main():
    ap = argparse.ArgumentParser(description="单 logic 信号真值表输入数 + 上游 mux 是否展开诊断")
    ap.add_argument("excel")
    ap.add_argument("signal", help="信号名（带不带 d_wl_rf_ 前缀都行，logic 页）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    wb = excel_model.load_workbook(args.excel)
    lines = []

    def out(s=""):
        lines.append(s)

    sig, cone_bases, _cb = _inputs(wb, "cone", args.signal)
    if sig is None:
        out("✗ 在 logic 页找不到信号：%s" % args.signal)
        out("  （它可能是 mux 信号或拼写不同；本诊断只针对 logic 信号。）")
        print("\n".join(lines))
        return
    _s2, force_bases, force_bind = _inputs(wb, "force", args.signal)

    out("=== 单信号展开诊断 (diag_signal_expand.py) ===")
    out("文件 : %s" % args.excel)
    out("信号 : %s" % sig.out_name)
    out("")
    out("【展开上游(cone)＝对齐 VBA】真值表输入 %d 个：" % len(cone_bases))
    for b in cone_bases:
        out("    %s" % b)
    out("")
    out("【force级联网＝旧行为】真值表输入 %d 个：" % len(force_bases))
    for b in force_bases:
        out("    %s" % b)
    out("")

    # force 模式下哪些输入是 mux 输出（未展开时残留的衔接网）→ 逐个判可否展开
    res = R.Resolver(wb)
    res.cascade_mode = "force"
    mux_out = dict(res._mux_outputs)
    mux_hits = sorted({b.base for b in force_bind.values()
                       if b is not None and getattr(b, "base", None)
                       and b.base.lower() in mux_out})
    out("=" * 70)
    if not mux_hits:
        out("本信号的输入里【没有 mux 输出】——4/6 之差与跨边界展开无关，")
        out("请检查 logic 行表达式本身 / dft 门 / for_test 行序。")
    else:
        out("本信号引用了 %d 个 mux 输出，cone 模式下逐个判定：" % len(mux_hits))
        for base in mux_hits:
            grp = next((g for g in wb.mux if g.out_base.lower() == base.lower()), None)
            if grp is None:
                out("    %-40s  ✗ 找不到对应 mux 组" % base)
                continue
            if mux_gen.mux_expandable(grp):
                out("    %-40s  ✓ 可展开（cone 应已拆成源寄存器；若真表仍只 1 输入＝级联模式没生效，"
                    "查 logic 级联下拉是否＝展开上游）" % base)
            else:
                out("    %-40s  ✗ 不展开：%s" % (base, _expand_reason(grp)))
    out("")
    out("提示：lo2g5g 类应在【logic 级联＝展开上游】下出 6 输入。若 GUI 仍 4：")
    out("  · 确认是【logic 级联】下拉＝展开上游（不是 mux 级联）——logic 信号由 logic 级联管；")
    out("  · 上面若标 ✗ 不展开，按原因看是宽 select / hole（本轮保守边界，需后续支持或配前缀）。")
    out("  · 注：受 iddq/dft 门控的信号，GUI/生成 的真值表会比上面【多 1 行门网】(本诊断只数 cone")
    out("    展开的输入、不含 dft 门)；故上面 cone=5 对应 GUI 6、force=3 对应 GUI 4。")

    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            f.write(text)
        print(text)
        print("\n报告: %s" % args.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
