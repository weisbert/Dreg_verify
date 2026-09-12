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
