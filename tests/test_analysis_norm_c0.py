# -*- coding: utf-8 -*-
"""test_analysis_norm_c0.py — GUI v2 Phase C0-a：`analysis_norm` 归一化层的 additive 补齐。

覆盖架构文档 §4.1 里落在 analysis_norm 的四项（全部只加键、不改旧键）：
  · §7-1  an["out_net"]         —— assert LHS 网名（不带探针前缀）
  · §7-2  an["status_detail"]   —— 状态八档细分（判据只来自结构化字段，不做字符串猜）
  · §7-4  an["ctrl_keys_missing"] —— used_vars 里没有对应驱动器的控制键
  · §7-5  an["dft_gate_skipped"]  —— iddq 门没钉上的原因

夹具 = 两张 mirror（btlp / wl），与 test_topout.py 同源。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                        # noqa: E402  仓库根夹具脚本
import make_mirror_excel                       # noqa: E402
from dreg_verify import analysis_norm as AN    # noqa: E402
from dreg_verify import excel_model as M       # noqa: E402
from dreg_verify import pageviews as P         # noqa: E402
from dreg_verify import resolver as R          # noqa: E402
from dreg_verify import topout as T            # noqa: E402


@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    p = tmp_path_factory.mktemp("btlp") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return M.load_workbook(str(p))


@pytest.fixture(scope="module")
def wl_wb(tmp_path_factory):
    p = tmp_path_factory.mktemp("wl") / "mirror_wl_dreg.xlsx"
    make_mirror_excel.build(str(p))
    return M.load_workbook(str(p))


def _ans(wb_, **kw):
    """该表全部 Topout 信号 → {名: an}。"""
    rr = R.Resolver(wb_)
    return {r.topo.name: AN.norm_topout_result(r, wb_, **kw)
            for r in T.analyze_all(wb_, rr, mode="max", max_tests=64)}


# ───────────────────────────── §7-1：an["out_net"] ─────────────────────────────
def test_c064_an_has_out_net(wb, wl_wb):
    """每个 an 都带 out_net = assert LHS 网名，且【不带】探针前缀（前缀由 provider 另补）。"""
    ans = _ans(wb)
    assert all(a["out_net"] for a in ans.values())
    # 与 .sv 同口径：logic/mux 根取源对象 RTL 基名（tsensor 的 RTL 网是 _to_mux 衔接网）
    assert ans["d_logic_bt_lp_tsensor"]["out_net"] == "d_logic_bt_lp_tsensor_to_mux"
    assert ans["d_logic_bt_lp_rx_en"]["out_net"] == "d_logic_bt_lp_rx_en"
    assert ans["clk_force_on"]["out_net"] == "clk_force_on"          # 直连寄存器根 → 顶层名
    assert ans["pll_lock_indicator"]["out_net"] == "pll_lock_indicator"   # RO 回读也给名
    # 配了探针前缀也不带进 out_net（它是「网名」不是「路径」）
    rr = R.Resolver(wb, wire_prefixes={"d_logic_bt_lp_tsensor_to_mux": "U_BT_LP"})
    topo = next(t for t in wb.topout if t.name == "d_logic_bt_lp_tsensor")
    a = AN.norm_topout_result(T.analyze_signal(wb, rr, topo, mode="min"), wb)
    assert a["out_net"] == "d_logic_bt_lp_tsensor_to_mux"
    assert "." not in a["out_net"]
    # 页本地视图：out_net = 本页源对象的 RTL 网基名
    pans = [AN.norm_page_result(r, wl_wb)
            for r in P.analyze_all(wl_wb, "logic", mode="min", max_tests=8)]
    assert pans and all(a["out_net"] for a in pans)
    for a in pans:
        assert "." not in a["out_net"]


# ───────────────────────────── §7-2：an["status_detail"] ─────────────────────────────
def test_c016_status_detail_keys_match_terms():
    """引擎产出的档集合 = ui/terms.STATUS_KEYS 去掉 "pending"（骨架清单专用，引擎从不产）。"""
    from dreg_verify.ui import terms
    assert set(AN.STATUS_DETAILS) == set(terms.STATUS_KEYS) - {"pending"}
    assert all(k in terms.STATUS for k in AN.STATUS_DETAILS)


def test_c016_status_detail_eight_grades(wb, wl_wb):
    """八档在两张 mirror 上各取一例——**判据全部来自结构化字段**，无一处靠 issues 文本猜。"""
    ans, wl_ans = _ans(wb), _ans(wl_wb)

    # ① clean：叶子全在 tmm/regmap，输出就是 Topout 声明的那个名字
    assert ans["d_logic_bt_lp_rx_en"]["status_detail"] == "clean"
    # ② bare-probe：assert LHS 是 _to_mux 衔接网 ≠ 声明名（out_net 判据，非 top_output）
    assert ans["d_logic_bt_lp_tsensor"]["status_detail"] == "bare-probe"
    # ③ skip：RO 回读（status=skip）
    assert ans["pll_lock_indicator"]["status_detail"] == "skip"
    # ④/⑤ needs-prefix ↔ risky-generated：同一信号、只由 include_risky 开关切换
    r = next(x for x in T.analyze_all(wl_wb, R.Resolver(wl_wb), mode="min", max_tests=8)
             if x.topo.name == "d_wl_rf_lp5g_gm_itrim")
    assert any(getattr(b, "found_in", None) in AN._RISKY_FOUND_IN for b in AN._leaf_bindings(r))
    assert AN.status_detail(r, include_risky=True) == "risky-generated"
    assert AN.status_detail(r, include_risky=False) == "needs-prefix"
    assert wl_ans["d_wl_rf_lp5g_gm_itrim"]["status_detail"] == "risky-generated"   # 默认=引擎现状
    # ⑥ spec-collision：expansion["spec_conflicts"] 非空（结构化，不读 issues 文本）
    a = wl_ans["d_wl_rf_lp5g_rxrf_lna_lctune"]
    assert a["status_detail"] == "spec-collision"
    assert a["expansion"]["spec_conflicts"]
    # ⑦ false-green：meta["override_collision"]（手填数据值撞值 → 选错路也 PASS）
    topo = next(t for t in wb.topout if t.name == "d_bt_lp_lna_itrim")
    fg = T.analyze_signal(wb, R.Resolver(wb), topo, mode="max",
                          mux_data={"d_bt_lp_lna_itrim_t1": 5, "d_bt_lp_lna_itrim_t2": 5})
    assert fg.meta.get("override_collision") is True
    assert AN.norm_topout_result(fg, wb)["status_detail"] == "false-green"
    # ⑧ wire-fallback：dft 页每行的门网都是 wire 兜底（表里查无、按名 force）
    dft = [AN.norm_page_result(x, wb) for x in P.analyze_all(wb, "dft", mode="min", max_tests=8)]
    assert dft and all(x["status_detail"] == "wire-fallback" for x in dft)
    # ⑨ unresolved：关掉 wire 兜底 → 叶子 binding.resolved=False
    rr = R.Resolver(wl_wb, wire_fallback=False)
    rr.cascade_mode = "force"
    sig = next(s for s in P.page_signals(wl_wb, "logic")
               if s.out_name.startswith("d_wl_rf_rx5g_en_line"))
    ur = P.analyze_page_signal(wl_wb, rr, sig, "logic", mode="min", max_tests=8)
    assert AN.norm_page_result(ur, wl_wb)["status_detail"] == "unresolved"
    # ⑩ parse-err：wl mux 页有表达式解析不了的组（status=error）
    mx = [AN.norm_page_result(x, wl_wb) for x in P.analyze_all(wl_wb, "mux", mode="min",
                                                               max_tests=8)]
    assert "parse-err" in {x["status_detail"] for x in mx}


def test_c016_status_detail_probe_prefix_clears_bare_probe(wb):
    """配了探针前缀 = 用户给了「这根网在哪」的证据 → 输出侧不再算裸名猜测。"""
    topo = next(t for t in wb.topout if t.name == "d_logic_bt_lp_tsensor")
    r = T.analyze_signal(wb, R.Resolver(wb), topo, mode="min")
    assert AN.status_detail(r, probe_prefix="") == "bare-probe"
    assert AN.status_detail(r, probe_prefix="U_BT_LP_PLL_DIG") == "clean"


def test_c016_status_detail_never_reads_issue_text(wb):
    """判据只认结构化字段：把 issues/note 换成任何文本都不改档（不做字符串猜）。"""
    rr = R.Resolver(wb)
    for r in T.analyze_all(wb, rr, mode="min", max_tests=8):
        before = AN.status_detail(r)
        r.issues = ["规格冲突 spec-collision 假绿 false-green 缺前缀 needs-prefix wire 兜底"]
        r.note = "unresolved parse-err bare-probe"
        assert AN.status_detail(r) == before
