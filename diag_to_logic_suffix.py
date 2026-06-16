# -*- coding: utf-8 -*-
"""
diag_to_logic_suffix.py — 诊断「某 logic 信号被错误加上 _to_logic 尾缀 / 自引用撞名」。

针对：勾选『logic 加尾缀』后，某信号的【输出探针网名】被补成 X_to_logic，结果与它【自己的
输入网】撞名 → 等于直接读输入信号（assert 变恒真/读回 force 值）。本工具用真实
read_logic + Resolver 把尾缀判定、各输入解析、以及【输出探针是否与某输入网撞名】全打印出来。

用法：
    python diag_to_logic_suffix.py 真表.xlsx d_wl_rf_5g_tx_lodiv_en
    python diag_to_logic_suffix.py 真表.xlsx d_wl_rf_5g_tx_lodiv_en --prefixes probe_prefixes.txt

把输出整段贴回来即可。纯只读，不改任何文件。
"""
import argparse
import os
import sys

for _s in (sys.stdout, sys.stderr):          # Windows 控制台常 GBK，中文/emoji 会崩
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dreg_verify import excel_model, generator, resolver as R  # noqa: E402


def _strip(name):
    base, _w, _m, _l = excel_model._strip_width(name or "")
    return excel_model.strip_to_logic(base)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("excel")
    ap.add_argument("signal", help="logic 输出基名，如 d_wl_rf_5g_tx_lodiv_en")
    ap.add_argument("--prefixes", default=None, help="probe_prefixes.txt（可选，看前缀是否参与）")
    args = ap.parse_args()

    wb = excel_model.load_workbook(args.excel)
    target = args.signal.strip().lower()
    tbase = _strip(args.signal).lower()

    print("=== _to_logic 尾缀 / 自引用撞名诊断 ===")
    print("文件   : %s" % os.path.basename(args.excel))
    print("目标信号: %s  (基名 %s)" % (args.signal, tbase))
    print()

    # ── 1. 所有触及该信号的 logic 行（定义行 + 引用行）──
    print("─" * 70)
    print("【1】触及该信号的 logic 行（K=输出 / A-J=输入，原文）")
    def_rows, ref_rows = [], []
    for s in wb.logic:
        ob = s.out_base.lower()
        in_items = [(L, info["raw"]) for L, info in s.inputs.items()]
        is_def = (ob == tbase)
        # 引用行：某输入基名（剥 _to_logic/_to_mux/位宽后）== 目标基名
        refs_target = [(L, raw) for L, raw in in_items
                       if _strip(raw).lower() == tbase]
        if is_def:
            def_rows.append(s)
        if refs_target and not is_def:
            ref_rows.append((s, refs_target))
        if is_def or refs_target:
            tag = "★定义行" if is_def else "  引用行"
            print("  %s row%-4s K=%-40s M=%-8s N(top)=%s"
                  % (tag, s.row, s.out_name, s.suffix, s.top_output))
            for L, raw in in_items:
                mark = " ←引用目标" if _strip(raw).lower() == tbase else ""
                print("        %s = %s%s" % (L, raw, mark))
    if not def_rows:
        print("  ⚠ 没找到 out_base==%s 的 logic 定义行（核对名字/位宽）。" % tbase)

    # ── 2. ref_suffix 判定追溯（复刻 read_logic 的 ref_logic 计算）──
    print()
    print("─" * 70)
    print("【2】ref_suffix 判定追溯（谁把 %s 标成『被下游 _to_logic 引用』）" % tbase)
    setters, self_excluded = [], []
    for s in wb.logic:
        ob = s.out_base.lower()
        for L, info in s.inputs.items():
            rb = excel_model._strip_width(info["raw"])[0].lower()
            if rb.endswith("_to_logic"):
                base = rb[: -len("_to_logic")]
                if base != tbase:
                    continue
                if base != ob:
                    setters.append((s, L, info["raw"]))      # 真·下游引用
                else:
                    self_excluded.append((s, L, info["raw"]))  # 自引用，已排除
    sig = def_rows[0] if def_rows else None
    if sig is not None:
        print("  ref_suffix       = %r" % sig.ref_suffix)
        print("  is_top(top_out=1)= %s" % sig.is_top)
    if setters:
        print("  → 被这些【别的】行以 %s_to_logic 引用（故补尾缀）：" % tbase)
        for s, L, raw in setters:
            print("       row%-4s K=%-38s 输入 %s=%s" % (s.row, s.out_name, L, raw))
    if self_excluded:
        print("  → 这些是【自引用】(K 基名==输入基名)，read_logic 已排除、不该据此补尾缀：")
        for s, L, raw in self_excluded:
            print("       row%-4s K=%-38s 输入 %s=%s" % (s.row, s.out_name, L, raw))
    if not setters and not self_excluded:
        print("  （没有任何行以 %s_to_logic 引用它。若 ref_suffix 仍非空，另有来源。）" % tbase)

    # ── 3. rtl_base / 探针网名（开关 on/off）──
    print()
    print("─" * 70)
    print("【3】输出探针网名（rtl_base）随『logic 加尾缀』开关")
    if sig is not None:
        for atl in (True, False):
            sig._append_to_logic = True   # reset
            saved = sig._append_to_logic
            sig._append_to_logic = atl
            print("  append_to_logic=%-5s → rtl_base=%-44s rtl_name=%s"
                  % (atl, sig.rtl_base, sig.rtl_name))
            sig._append_to_logic = saved
        print("  rtl_base_full(总带尾缀，仅供前缀匹配) = %s" % sig.rtl_base_full)

    # ── 4. 各输入的 Resolver 解析 + 撞名检查（两种级联）──
    if args.prefixes and os.path.isfile(args.prefixes):
        with open(args.prefixes, encoding="utf-8") as f:
            prefixes = generator.parse_probe_prefix_lines(f.read())
    else:
        prefixes = {}
    print()
    print("─" * 70)
    print("【4】定义行各输入解析 + 撞名检查（append_to_logic=ON）")
    if sig is not None:
        for mode in ("cone", "force"):
            res = R.Resolver(wb, wire_prefixes=prefixes, append_to_logic=True)
            res.cascade_mode = mode
            # 输出探针网名（开关 ON）
            for s in wb.logic:
                s._append_to_logic = True
            out_probe = sig.rtl_base.lower()
            print("  ── 级联=%s ──  输出探针网=%s" % (mode, sig.rtl_base))
            try:
                node, bindings, _exp = generator.expand_signal(wb, res, sig)
            except Exception as ex:  # noqa: BLE001
                print("       expand_signal 异常: %s" % ex)
                continue
            collided = False
            for L, b in sorted(bindings.items()):
                if b is None:
                    continue
                net = (getattr(b, "wire", None) or getattr(b, "base", "") or "")
                hit = "  ⚠⚠ 与输出探针撞名!" if net and net.lower() == out_probe else ""
                if hit:
                    collided = True
                print("       %-3s base=%-34s force网=%-40s found_in=%-14s%s"
                      % (L, getattr(b, "base", ""), net, getattr(b, "found_in", ""), hit))
                if getattr(b, "note", ""):
                    print("              note: %s" % b.note)
            print("       → 撞名: %s" % ("是 ⚠（assert 读回 force 值=恒真）" if collided else "否"))
            print()

    print("提示：若【4】里某输入的 force 网 == 输出探针网，就是这个 bug——")
    print("     输出探针被加尾缀后等于在读自己的输入。把本段全文贴回来。")


if __name__ == "__main__":
    main()
