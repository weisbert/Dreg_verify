# -*- coding: utf-8 -*-
"""
audit_cross_boundary.py — 量化 logic↔mux「跨引擎边界」缺口的规模。

背景：工具有两套"展开上游"引擎，各自只在本域递归：
  · cone.py        展开 logic→logic 上游（EXPANDABLE = logic-internal/logic-computed）
  · mux_gen 级联    展开 mux→mux 上游（resolve_upstream_recipe）
两个【跨域】边界【不展开】、只 force 衔接网（要 scan_rtl 探针前缀）：
  ① logic ← mux 输出   logic 表达式的输入是某 mux 组的输出
                        （2026-06-15 d_wl_rf_lo2g5g_bias_en 实证：输入 d_wl_rf_logen_mixer_en
                         是 mux 组输出，工具 force 衔接网=4 输入；VBA 端到端展开=6 输入）
  ② mux  ← logic 输出  mux 的数据/控制输入是某 logic 行的输出（X_to_mux 衔接网）

VBA 源头数据把这两类都【端到端展开】（驱真寄存器，不 force 内部网）。本脚本数一数
每类边界各影响多少信号、各属哪个 owner，给"对齐 VBA"的改动定规模。

纯 Excel 解析，不需要探针前缀。用法：
    python audit_cross_boundary.py 真表.xlsx
    python audit_cross_boundary.py 真表.xlsx --out cross.txt
"""
import argparse
import sys
from collections import defaultdict

# Windows 控制台常是 GBK，打印中文会崩；统一 UTF-8（文件输出本就 UTF-8）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from dreg_verify import excel_model, generator, resolver as R


def main():
    ap = argparse.ArgumentParser(description="量化 logic↔mux 跨边界缺口规模（按 owner 归类）")
    ap.add_argument("excel")
    ap.add_argument("--out", default=None, help="完整报告写文件（UTF-8 带 BOM，防乱码）")
    args = ap.parse_args()

    wb = excel_model.load_workbook(args.excel)
    res = R.Resolver(wb)                     # 默认 cascade=cone：logic 链最大化展开，mux 输出仍是叶子
    mux_out = dict(res._mux_outputs)         # mux 输出基名(小写) -> (..., group_no)
    logic_out = set(res._logic_outputs)      # logic 输出基名(小写) 集合

    lines = []
    def out(s=""):
        lines.append(s)

    # ── ① logic ← mux 输出：logic 信号 cone 展开后的叶子里有 mux 组输出 ──
    gap1, cone_err = [], []
    for sig in wb.logic:
        try:
            _node, bindings, _exp = generator.expand_signal(wb, res, sig)
        except Exception as ex:              # noqa: BLE001  cone 成环/超深/解析失败 → 单列
            cone_err.append((sig.out_name, (getattr(sig, "owner", "") or "").strip(), str(ex)))
            continue
        hits = sorted({b.base for b in bindings.values()
                       if b is not None and getattr(b, "base", None)
                       and b.base.lower() in mux_out})
        if hits:
            gap1.append((sig.out_name, (getattr(sig, "owner", "") or "").strip(), hits))

    # ── ② mux ← logic 输出：mux 组的数据/控制输入里有 logic 行输出 ──
    gap2 = []
    for g in wb.mux:
        ins = []
        for c in g.cases:
            if c.input_base and c.input_base.lower() in logic_out:
                ins.append(("数据", c.input_base))
        for ct in g.ctrls:
            if ct.base and ct.base.lower() in logic_out:
                ins.append(("控制", ct.base))
        if ins:
            gap2.append((g.out_name, (getattr(g, "owner", "") or "").strip(), sorted(set(ins))))

    def _byowner(rows):
        d = defaultdict(int)
        for r in rows:
            d[r[1] or "(空)"] += 1
        return " | ".join("%s=%d" % (o, c) for o, c in sorted(d.items(), key=lambda kv: -kv[1]))

    out("=== logic↔mux 跨边界缺口审计 (audit_cross_boundary.py) ===")
    out("文件: %s" % args.excel)
    out("logic 信号 %d 个；mux 组 %d 个。" % (len(wb.logic), len(wb.mux)))
    out("")
    out("【① logic ← mux 输出】%d 个 logic 信号" % len(gap1))
    out("   现状=force mux 输出衔接网(要探针前缀)；VBA=端到端拆 mux(选择+数据)。这是本轮要对齐的主缺口。")
    out("   owner 分布: " + (_byowner(gap1) or "(无)"))
    out("")
    for s, o, hits in gap1:
        out("    %-46s [%s]  ← mux 输出: %s" % (s[:46], o, ", ".join(hits)))
    out("")
    out("=" * 90)
    out("【② mux ← logic 输出】%d 个 mux 组（对称缺口：mux 数据/控制是 logic 输出，force X_to_mux 网）" % len(gap2))
    out("   owner 分布: " + (_byowner(gap2) or "(无)"))
    out("")
    for s, o, ins in gap2:
        out("    %-46s [%s]  ← %s" % (s[:46], o, ", ".join("%s:%s" % (k, v) for k, v in ins)))
    out("")
    if cone_err:
        out("=" * 90)
        out("【cone 展开失败】%d 个 logic 信号（自引用环/超深等，单列不计入①）：" % len(cone_err))
        for s, o, e in cone_err:
            out("    %-46s [%s]  %s" % (s[:46], o, e[:80]))

    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            f.write(text)
        print(text if len(text) < 6000 else text[:6000] + "\n...(详见文件)")
        print("\n完整报告: %s" % args.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
