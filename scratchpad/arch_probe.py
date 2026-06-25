# -*- coding: utf-8 -*-
"""架构重构测绘探针：实跑 .venv 验证 (1)金标准向量 (2)缝A 展开层分歧 (3)缝B 渲染层分歧。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dreg_verify.excel_model import load_workbook
from dreg_verify import topout as T, pageviews as P, generator as G
from dreg_verify import resolver as R, vectors as V, cone, mux_gen
import make_mirror_btlp as MB

OUT = "scratchpad/_arch"
os.makedirs(OUT, exist_ok=True)

def hdr(s): print("\n" + "=" * 70 + "\n" + s + "\n" + "=" * 70)

# ============ (1) 金标准向量基线：mirror_btlp 7真族 ============
hdr("(1) 金标准基线 mirror_btlp_dreg.xlsx")
wb = load_workbook("mirror_btlp_dreg.xlsx")
print("logic rows:", len(wb.logic), " mux:", len(getattr(wb,'mux',[]) or []),
      " topout:", len(wb.topout or []), " dft:", sorted((wb.dft or {}).keys()))
res = R.Resolver(wb)
all_r = T.analyze_all(wb, res)
for r in all_r:
    print("  %-30s kind=%-9s status=%-6s nvec=%2d nleaf=%d"
          % (r.topo.name[:30], r.root.kind, r.status, len(r.vectors), r.n_leaves))

# Topout .sv 文本，存盘做 byte-diff 基线
txt, b = T.render_topout_sv(wb)
open(OUT + "/topout_baseline.sv", "w", encoding="utf-8").write(txt)
print("Topout .sv bytes:", len(txt.encode("utf-8")),
      " blocks:", len(b["blocks"]), " nvec:", b["summary"]["n_vectors"])

# 旧 logic-rooted build 基线（证逐字节）
built = G.build(wb, G.GenOptions(include_risky=True))
print("旧 build blocks:", len(built["blocks"]), " nvec:", built["summary"]["n_vectors"])

# ============ (2) 缝A：dft/iddq 不是 cone 一级 → 各造向量路漏 pin ============
hdr("(2) 缝A 展开层：iddq 门作为旁路 pin，三处对不上")
PATH2 = OUT + "/_dft_gated_ls.xlsx"
MB.build_dft_gated_ls(PATH2)
wb2 = load_workbook(PATH2)
print("dft keys:", sorted((wb2.dft or {}).keys()))
topo = next(t for t in wb2.topout if t.name == "d_en_vco_fc_ls")
print("topout:", topo.name)

# 2a. logic 根 analyze_signal（GUI 真值表）—— 修复后应含 iddq
res2 = R.Resolver(wb2)
a = T.analyze_signal(wb2, res2, topo)
n_dft = sum(1 for v in a.vectors if getattr(v, "dft_pitch", False))
print("[analyze logic 根] kind=%s nvec=%d dft_gate=%r DFT拍=%d"
      % (a.root.kind, len(a.vectors), a.dft_gate is not None, n_dft))

# 2b. .sv
txt2, b2 = T.render_topout_sv(wb2, only=[topo.name])
nv_sv = sum(s.get("n_vectors", 0) for _l, s in b2["blocks"])
print("[.sv build_for_topout] nvec=%d  含IDDQ=%s" % (nv_sv, "IDDQ" in txt2))

# 2c. pageviews logic 页（排查旧）—— 同 logic 信号 d_en_vco_fc 走 page
pv = P.page_view_models(wb2, "logic")
pvm = next((m for m in pv if m["name"].lower().startswith("d_en_vco_fc")), None)
if pvm:
    has_gate = any("iddq" in str(i).lower() for i in pvm.get("inputs", []))
    print("[pageviews logic] %s ntests=%d 有iddq输入行=%s"
          % (pvm["name"], len(pvm.get("tests", [])), has_gate))
# pageviews PageResult 有无 dft_gate 属性？
pr_all = P.analyze_all(wb2, "logic")
pr = next((r for r in pr_all if r.name.lower().startswith("d_en_vco_fc")), None)
print("[pageviews PageResult] hasattr dft_gate =", hasattr(pr, "dft_gate"))

# ============ (3) 缝B：register passthrough 绕过 build 注入层 ============
hdr("(3) 缝B 渲染层：register/dft改名根裸渲染，绕过警告/owner/claims")
# 找一个 register 根
reg_r = next((r for r in all_r if r.root.kind == T.REGISTER), None)
if reg_r:
    print("register 根:", reg_r.topo.name)
# build_for_topout 返回字典 keys（看有无 selfaudit/regmap/supplement/claims 通道）
print("build_for_topout dict keys:", sorted(b["blocks"] and b.keys()))
print("  → 有 claims 通道?", "claims" in b)
print("  → 有 selfaudit_warnings?", "selfaudit_warnings" in b)
print("  → 有 regmap_warnings?", "regmap_warnings" in b)
print("旧 build dict keys:", sorted(built.keys()))
# compose_topout_account n_with_issues vs build selfaudit
acc = T.compose_topout_account(wb, res)
print("topout account n_with_issues:", acc["summary"]["n_with_issues"])
print("旧 build n_selfaudit_warnings:", built["summary"]["n_selfaudit_warnings"],
      " n_regmap_warnings:", built["summary"]["n_regmap_warnings"])

# owner_in_msg：register 根 .sv 是否带 owner？
txt_ow, _ = T.render_topout_sv(wb, owner_in_msg=True)
print("Topout owner_in_msg .sv 含 owner= 次数:", txt_ow.count("owner="))
print("旧 build owner_in_msg .sv 含 owner= 次数:",
      G.build(wb, G.GenOptions(include_risky=True, owner_in_msg=True))
      and "(see render)")

# ============ (4) 入口 opts 矩阵：哪些 opts 各入口接/丢 ============
hdr("(4) 入口 opts 接纳矩阵（签名形参）")
import inspect
def params(fn): return list(inspect.signature(fn).parameters.keys())
for nm, fn in [("topout.build_for_topout", T.build_for_topout),
               ("topout.topout_view_models", T.topout_view_models),
               ("topout.report_for_topout", T.report_for_topout),
               ("topout.topout_fortest_rows", T.topout_fortest_rows),
               ("pageviews.build_page_sv", P.build_page_sv),
               ("pageviews.page_view_models", P.page_view_models),
               ("generator.build(GenOptions)", G.GenOptions.__init__)]:
    print("  %-34s : %s" % (nm, params(fn)))
print("\nDONE")
