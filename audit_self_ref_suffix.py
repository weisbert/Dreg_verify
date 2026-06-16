# -*- coding: utf-8 -*-
"""
audit_self_ref_suffix.py — 全 logic 页审计「自引用信号被错补 _to_logic/_to_mux 尾缀」的爆炸半径。

回答：除了 d_wl_rf_5g_tx_lodiv_en，整张真表里【还有多少】信号是同一个 bug
（X 自读 X_后缀，却把输出探针补成 X_后缀 = 探到自己的输入网，仿真假绿/假红）。

对每条 logic 信号比较【R40 修复前】与【修复后】的输出探针网名（假设 logic 加尾缀=ON）：
  · 探针变了的 = 本次修复真正纠正的信号 → 需重 sim 复核；
  · 第二段是【残留撞名自检】：修复后探针仍 == 自己的某条输入前级网 → 还有漏（应为 0）。

⚠ 旧探针(OLD)按【完整管线】算（logic 页尾缀 + mux 跨页 _to_mux 回填都算），不是只看 logic 页——
  否则会把『本就因 mux 跨页回填带 _to_mux』的信号误报成『本次改的』(早期版本的坑)。

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


def _logic_refs(signals):
    """复刻 read_logic：logic 页 _to_logic/_to_mux 的【下游引用】基名集合（排除自引用）。"""
    ref_logic, ref_mux = set(), set()
    for sig in signals:
        ob = sig.out_base.lower()
        for info in sig.inputs.values():
            rb = M._strip_width(info["raw"])[0].lower()
            if rb.endswith("_to_logic"):
                base = rb[: -len("_to_logic")]
                if base != ob:
                    ref_logic.add(base)
            elif rb.endswith("_to_mux"):
                base = rb[: -len("_to_mux")]
                if base != ob:
                    ref_mux.add(base)
    return ref_logic, ref_mux


def _mux_page_refs(mux):
    """复刻 _apply_mux_ref_suffix：mux 页(数据/控制)以 _to_mux 引用到的基名集合。"""
    refs = set()
    for g in (mux or []):
        for c in g.cases:
            if M._strip_width(c.input_raw)[0].lower().endswith("_to_mux"):
                refs.add(c.input_base.lower())
        for ct in g.ctrls:
            if M._strip_width(ct.raw)[0].lower().endswith("_to_mux"):
                refs.add(ct.base.lower())
    return refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("excel")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    wb = M.load_workbook(args.excel)
    signals = wb.logic
    ref_logic, ref_mux = _logic_refs(signals)
    mux_refs = _mux_page_refs(getattr(wb, "mux", None))

    lines = []
    def out(s=""):
        lines.append(s)

    def probe(sig, ref_suffix):
        """假设 logic 加尾缀=ON 时的输出探针网名（含 M=ls / top_output 规则）。"""
        return M.rtl_net_name(sig.out_base, sig.suffix, is_top=sig.is_top, ref_suffix=ref_suffix)

    def old_ref_suffix(sig):
        """R40 之前的完整管线结果：logic 页尾缀(无抑制) + mux 跨页回填(无抑制)。"""
        b = sig.out_base.lower()
        cand = "_to_logic" if b in ref_logic else ("_to_mux" if b in ref_mux else "")
        if cand == "" and b in mux_refs:          # 旧 mux 跨页回填
            cand = "_to_mux"
        return cand

    affected, self_ref_all, residual = [], [], []
    for sig in signals:
        srs = getattr(sig, "_self_ref_suffixes", set())
        is_self = bool(srs)
        old_p, new_p = probe(sig, old_ref_suffix(sig)), probe(sig, sig.ref_suffix)
        if is_self:
            self_ref_all.append((sig, srs, new_p, old_p != new_p))
        if old_p != new_p:
            affected.append((sig, srs, old_p, new_p))
        # 残留撞名自检：修复后探针仍 == 自己某条输入前级网（base+自引用后缀）→ 漏网
        for s in srs:
            if new_p.lower() == (sig.out_base + s).lower():
                residual.append((sig, new_p))

    out("=== 自引用尾缀审计（全 logic 页，完整管线）===")
    out("文件: %s" % os.path.basename(args.excel))
    out("logic 信号总数: %d ；自引用(读自己 _to_logic/_to_mux): %d 条"
        % (len(signals), len(self_ref_all)))
    out("")
    out("─" * 80)
    out("【A】本次 R40 修复真正纠正的信号（输出探针从『自己的输入网』改回真·输出）= %d 条" % len(affected))
    out("    这些信号【修复前】仿真探到的是自己的前级输入网 → 假绿/假红；务必重 sim 复核。")
    out("")
    if affected:
        out("    %-44s %-12s %-30s %s" % ("信号", "自读后缀", "旧探针(BUG)", "新探针(FIXED)"))
        for sig, srs, op, np in sorted(affected, key=lambda x: x[0].out_base):
            out("    %-44s %-12s %-30s %s" % (sig.out_base, ",".join(sorted(srs)), op, np))
    else:
        out("    （无）")
    out("")
    out("─" * 80)
    out("【B】残留撞名自检（修复后探针仍 == 自己的输入前级网）= %d 条  —— 应为 0" % len(residual))
    if residual:
        for sig, np in residual:
            out("    ⚠ %-44s 探针=%s  仍在读自己的输入！（修复没覆盖到，需排查）" % (sig.out_base, np))
    else:
        out("    ✓ 0 条——所有自引用信号的输出探针都不再撞自己的输入网，窟窿封住。")
    out("")
    out("─" * 80)
    out("【C】全部自引用信号一览（自读后缀 / 是否本次改了 / 探针）")
    out("    %-44s %-12s %-6s %-16s %s"
        % ("信号", "自读后缀", "本次改", "M(suffix)", "探针(修复后)"))
    for sig, srs, np, changed in sorted(self_ref_all, key=lambda x: x[0].out_base):
        out("    %-44s %-12s %-6s %-16s %s"
            % (sig.out_base, ",".join(sorted(srs)), "★是" if changed else "",
               (sig.suffix or "")[:16], np))
    out("")
    out("提示：【A】里改成 bare 的信号若配过探针前缀，多半配在旧探针(..._to_logic)上——")
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
