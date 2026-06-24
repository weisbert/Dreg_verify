# -*- coding: utf-8 -*-
"""
diag_name_collisions.py — 审计【同名(撞名)带来的系统性解析漏洞】(2026-06-24)。

背景：d_vco_en_faston_ls 的 bug = dft 观测【输出名】恰与一个【同名 RW 输入寄存器】撞车，
resolve_root 桥接时撞上寄存器即停 → 误判直连寄存器(假绿)。本脚本把【全表】所有同类撞名
列出来，判断是不是系统性的、还有没有别的受害信号。纯只读，不改任何文件。

扫四类撞名：
  A. 输出名 ⨯ RW 寄存器：某基名【既是 logic/mux/dft/ls 的输出】又是【RW 寄存器字段】
     —— 最危险(=我们刚修的那类，RW 字段是输入、却可能被当输出)。
  B. 输出名 ⨯ RO 寄存器：输出名又是 RO 字段(次危险，RO 一般是 force 叶子)。
  C. 多页输出撞名：同一基名在【≥2 个页】都被当输出(logic/mux/dft/ls) —— 根对象有歧义。
  D. build_index 影子：≥2 个 logic/mux 信号产生【同一候选名】(setdefault 保首个、后者被悄悄盖)。

再逐个 Topout 信号 resolve_root，标出【解析结果踩到撞名】的(尤其 kind=register 但同名又是
dft/logic/mux 输出 = 刚修 bug 的签名；修后应已桥到 logic，仍是 register 的要人工看)。

用法（在能打开真表的机器上跑，把整段输出贴回给 Claude）：
    .venv\\Scripts\\python.exe diag_name_collisions.py 真表.xlsx
"""
import sys

try:
    sys.stdout.reconfigure(errors="replace")     # 防 GBK 控制台 emoji 崩
except Exception:   # noqa: BLE001
    pass

from dreg_verify import excel_model as M
from dreg_verify import topout as T


def _strip(v):
    return M._strip_width(str(v))[0].strip().lower() if v is not None else ""


def main(path):
    wb = M.load_workbook(path)
    print("装载: %s" % path)
    print("sheets: %s\n" % wb.sheet_names)

    # ── 收集各角色的【基名】集合 ──
    logic_outs = {}     # base -> [行号]
    for s in wb.logic:
        logic_outs.setdefault(s.out_base.lower(), []).append(getattr(s, "row", "?"))
    mux_outs = {}
    for g in (getattr(wb, "mux", None) or []):
        mux_outs.setdefault(g.out_base.lower(), []).append(getattr(g, "group_no", "?"))
    dft_outs = {}
    for d in (getattr(wb, "dft_rows", None) or []):
        dft_outs.setdefault(_strip(d.out_name), []).append(getattr(d, "row", "?"))
    ls_outs = {}
    for in_base, info in (getattr(wb, "level_shift", None) or {}).items():
        ob = str((info or {}).get("out", "")).strip().lower()
        if ob:
            ls_outs.setdefault(ob, []).append(in_base)

    # 寄存器字段(基名 -> 类型)：tmm 优先(与 _register_kind 同序)，再 regmap
    reg_type = {}
    for name, fld in (wb.tmm or {}).items():
        reg_type.setdefault(name.lower(), (fld.reg_type or "?", "tmm"))
    for name, ent in (wb.regmap or {}).items():
        from dreg_verify.excel_model import _normalize_type
        reg_type.setdefault(name.lower(), (_normalize_type(ent.reg_type) or "?", "regmap"))

    out_pages = {"logic": logic_outs, "mux": mux_outs, "dft": dft_outs, "level_shift": ls_outs}
    all_out = set(logic_outs) | set(mux_outs) | set(dft_outs) | set(ls_outs)
    # dft 观测分两类：【计算输出】(改名/带门，源≠输出 或 有 iddq 门)=危险；【恒等观测】(E=A 透传)=合法直连寄存器模式
    dft_renamed = set(T._dft_rename_map(wb))          # 源≠输出名(改名)
    dft_gated = set(getattr(wb, "dft", None) or {})   # 有 iddq 门(B?0:A)
    dft_computed = dft_renamed | dft_gated
    dft_identity = set(dft_outs) - dft_computed       # 恒等观测=合法直连寄存器
    # 「计算输出」= 真正代表一个被算出来的值的名字（不是裸寄存器）：logic/mux/改名或带门 dft/level_shift
    computed_out = set(logic_outs) | set(mux_outs) | dft_computed | set(ls_outs)

    def _where_out(b):
        return ", ".join("%s%s" % (pg, locs.get(b)) for pg, locs in out_pages.items() if b in locs)

    # ── A：计算输出 ⨯ RW 寄存器（最危险：RW 是输入、却与一个计算输出同名 = 刚修 bug 那类）──
    print("=== A. 计算输出 ⨯ RW 寄存器（最危险：同名 RW 是输入、易被当输出 = d_vco_en_faston 那类）===")
    a_hits = [b for b in sorted(computed_out) if reg_type.get(b, ("", ""))[0] == "RW"]
    if not a_hits:
        print("   （无）")
    for b in a_hits:
        tag = "改名" if b in dft_renamed else ""
        tag += ("+门" if b in dft_gated else "")
        print("   %-42s RW@%s | 又是计算输出: %s %s"
              % (b, reg_type[b][1], _where_out(b), ("[%s]" % tag) if tag else ""))

    # ── A2：dft 恒等观测 ⨯ RW 寄存器（=合法直连寄存器模式，列出仅供安心，非 bug）──
    print("\n=== A2. dft 恒等观测(E=A) ⨯ RW 寄存器（=合法直连寄存器，非 bug，仅列出确认）===")
    a2 = [b for b in sorted(dft_identity) if reg_type.get(b, ("", ""))[0] == "RW"]
    print("   " + (", ".join(a2) if a2 else "（无）"))

    # ── B：计算输出 ⨯ RO 寄存器（次危险：RO 多是 force 叶子，撞名可能误绑）──
    print("\n=== B. 计算输出 ⨯ RO 寄存器（次危险：RO 多是 force 叶子，撞名可能误绑）===")
    b_hits = [b for b in sorted(computed_out) if reg_type.get(b, ("", ""))[0] == "RO"]
    print("   " + (", ".join(b_hits) if b_hits else "（无）"))

    # ── C：多页【计算输出】撞名（排除 dft 恒等观测——它本就是 logic/mux 输出的合法观测，非歧义）──
    print("\n=== C. 多页计算输出撞名（≥2 页都当【计算】输出 → 根对象真歧义；已排除 dft 恒等观测）===")
    comp_pages = {"logic": logic_outs, "mux": mux_outs,
                  "dft(计算)": {b: dft_outs[b] for b in dft_computed if b in dft_outs},
                  "level_shift": ls_outs}
    multi = [b for b in sorted(all_out)
             if sum(b in locs for locs in comp_pages.values()) >= 2]
    if not multi:
        print("   （无真歧义）")
    for b in multi:
        print("   %-44s %s" % (b, ", ".join("%s%s" % (pg, locs.get(b))
                                             for pg, locs in comp_pages.items() if b in locs)))

    # ── D：build_index 影子（同候选名多个 logic/mux 源，setdefault 保首个）──
    print("\n=== D. build_index 影子（≥2 个 logic/mux 信号产生同候选名，后者被悄悄盖）===")
    cand = {}
    for s in wb.logic:
        for nm in T._logic_candidates(s):
            cand.setdefault(nm, []).append(("logic", getattr(s, "row", "?"), s.out_base))
    for g in (getattr(wb, "mux", None) or []):
        for nm in T._mux_candidates(g):
            cand.setdefault(nm, []).append(("mux", getattr(g, "group_no", "?"), g.out_base))
    shadow = {nm: v for nm, v in cand.items() if len({(t, o) for t, _r, o in v}) >= 2}
    if not shadow:
        print("   （无）")
    for nm, v in sorted(shadow.items()):
        print("   候选名 %-40s ← %s" % (nm, "; ".join("%s/%s(%s)" % (t, r, o) for t, r, o in v)))

    # ── 逐个 Topout 信号：解析结果 + 是否踩撞名（真问题检测）──
    print("\n=== Topout 逐信号解析（⚠=判 register 但同名是【计算输出】=可能漏门/选路；★=判 logic/mux 但同名也撞，已正确）===")
    logic_idx, mux_idx = T.build_index(wb)
    rename = T._rename_map(wb)
    n_warn = n_star = 0
    for topo in wb.topout:
        root = T.resolve_root(wb, topo.name, logic_idx, mux_idx, rename=rename)
        src = (root.source_name or root.matched_name or "").lower()
        if root.kind == T.REGISTER and src in computed_out:
            # 真问题：判直连寄存器，但同名是计算输出（带门 dft→漏 iddq / logic·mux→漏选路）
            n_warn += 1
            why = "带门 dft(漏 iddq!)" if src in dft_gated else (
                  "改名 dft" if src in dft_renamed else "logic/mux 输出")
            print("   ⚠ %-38s kind=register src=%-30s 同名是%s: %s"
                  % (topo.name, src, why, _where_out(src)))
        elif root.kind in (T.LOGIC, T.MUX) and src in reg_type:
            n_star += 1
            print("   ★ %-38s kind=%-5s src=%-30s 同名也是 %s 寄存器(已正确桥到计算源)"
                  % (topo.name, root.kind, src, reg_type[src][0]))
    if n_warn == 0:
        print("   ✅ 没有『判 register 但同名是计算输出』的 Topout 信号 —— 撞名解析无残留真问题。")
    else:
        print("   （上面 %d 个 ⚠ 需逐个 diag_one_topout.py 核对，可能还有同类 bug）" % n_warn)

    print("\n=== 怎么看 ===")
    print(" · A 段非空 = 还有同类撞名信号；逐个跑 diag_one_topout.py 看 resolve 是否已桥到 logic。")
    print(" · ⚠ 行 = 仍被判 register 但同名是输出 = 可能还有漏的，重点核对。")
    print(" · C/D 段 = 别的歧义类（多页输出/影子覆盖），按行号去真表核对哪个才是真源。")
    print(" · 把整段贴回给 Claude 一起判要不要再修。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python diag_name_collisions.py 真表.xlsx")
        sys.exit(1)
    main(sys.argv[1])
