# -*- coding: utf-8 -*-
"""
audit_self_ref_suffix.py — 全 logic 页审计「自引用信号被错补 _to_logic/_to_mux 尾缀」的爆炸半径。

回答：除了 d_wl_rf_5g_tx_lodiv_en，整张真表里【还有多少】信号是同一个 bug
（X 自读 X_to_logic / X_to_mux，却因被下游引用而把输出探针补成 X_to_xxx = 探到自己的输入）。

对每条 logic 信号比较【R40 修复前】与【修复后】的输出探针网名（假设 logic 加尾缀=ON）：
  · 探针变了的 = 本次修复真正纠正的信号（修复前在读自己的输入，仿真假绿/假红）→ 需重 sim 复核。
  · 列出全部自引用信号，便于人工对照。

纯只读，不需要探针前缀。用法：
    python audit_self_ref_suffix.py 真表.xlsx
    python audit_self_ref_suffix.py 真表.xlsx --out audit.txt
"""
import argparse
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dreg_verify import excel_model as M  # noqa: E402


def _suffix_sets(signals):
    """复刻 read_logic 的 ref/self_ref 计算（与 excel_model 同口径）。"""
    ref_logic, ref_mux = set(), set()
    self_ref_logic, self_ref_mux = set(), set()
    for sig in signals:
        ob = sig.out_base.lower()
        for info in sig.inputs.values():
            rb = M._strip_width(info["raw"])[0].lower()
            if rb.endswith("_to_logic"):
                base = rb[: -len("_to_logic")]
                (ref_logic if base != ob else self_ref_logic).add(base if base != ob else ob)
            elif rb.endswith("_to_mux"):
                base = rb[: -len("_to_mux")]
                (ref_mux if base != ob else self_ref_mux).add(base if base != ob else ob)
    return ref_logic, ref_mux, self_ref_logic, self_ref_mux


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("excel")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    wb = M.load_workbook(args.excel)
    signals = wb.logic
    ref_logic, ref_mux, self_ref_logic, self_ref_mux = _suffix_sets(signals)

    lines = []
    def out(s=""):
        lines.append(s)

    def probe(sig, ref_suffix):
        """假设 logic 加尾缀=ON 时的输出探针网名（含 M=ls / top_output 规则）。"""
        return M.rtl_net_name(sig.out_base, sig.suffix, is_top=sig.is_top,
                              ref_suffix=ref_suffix)

    affected, self_ref_all = [], []
    for sig in signals:
        b = sig.out_base.lower()
        is_self = b in self_ref_logic or b in self_ref_mux
        # 旧行为（修复前）：被下游引用就补尾缀，不看自引用
        old_cand = "_to_logic" if b in ref_logic else ("_to_mux" if b in ref_mux else "")
        # 新行为（修复后，= read_logic 已写好的 sig.ref_suffix）
        new_cand = sig.ref_suffix
        old_probe, new_probe = probe(sig, old_cand), probe(sig, new_cand)
        if is_self:
            self_ref_all.append((sig, old_probe, new_probe, b in ref_logic or b in ref_mux))
        if old_probe != new_probe:
            affected.append((sig, old_probe, new_probe))

    out("=== 自引用尾缀审计（全 logic 页）===")
    out("文件: %s" % os.path.basename(args.excel))
    out("logic 信号总数: %d ；自引用(读自己 _to_logic/_to_mux): %d 条"
        % (len(signals), len(self_ref_all)))
    out("")
    out("─" * 78)
    out("【A】本次 R40 修复真正纠正的信号（探针从『自己的输入网』改回 bare 输出）= %d 条"
        % len(affected))
    out("    这些信号【修复前】仿真探到的是自己的前级输入网 → 假绿/假红；务必重 sim 复核。")
    out("")
    if affected:
        out("    %-44s %-30s %s" % ("信号(out_base)", "旧探针(BUG)", "新探针(FIXED)"))
        for sig, op, np in sorted(affected, key=lambda x: x[0].out_base):
            out("    %-44s %-30s %s" % (sig.out_base, op, np))
    else:
        out("    （无——这张表没有被此 bug 影响的信号。）")
    out("")
    out("─" * 78)
    out("【B】全部自引用信号一览（含未受影响的，便于人工对照）")
    out("    %-44s %-12s %-8s %-16s %s"
        % ("信号(out_base)", "下游被引用", "top_out", "M(suffix)", "探针(修复后)"))
    for sig, op, np, downref in sorted(self_ref_all, key=lambda x: x[0].out_base):
        mark = "★改了" if op != np else ""
        out("    %-44s %-12s %-8s %-16s %s %s"
            % (sig.out_base, "是" if downref else "否",
               sig.top_output, (sig.suffix or "")[:16], np, mark))
    out("")
    out("提示：【A】里的信号若配过探针前缀，多半配在『旧探针』(..._to_logic) 上——")
    out("     改探 bare 名后需把前缀 key 改成 bare 基名（或它本是顶层 pin 则免前缀）。")

    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            f.write(text + "\n")
        print(text)
        print("\n报告: %s" % args.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
