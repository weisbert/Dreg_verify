# -*- coding: utf-8 -*-
"""test_topout.py — 【Topout-rooted】新引擎（dreg_verify/topout.py）+ BT_LP 等价性对照（判据一）。

夹具 = make_mirror_btlp.build 现搭的 mirror_btlp_dreg.xlsx（14 页新模型，7 条真族 for_test 金标准，
值逐字来自 refactor_notes/extracted_validation_examples.md 真表 dump）。
本测试只验【新增】Topout 路径，不碰旧 logic-rooted 语义。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_mirror_btlp                       # 仓库根夹具脚本（conftest 已加 path）
from dreg_verify import excel_model as M
from dreg_verify import resolver as R
from dreg_verify import topout as T


@pytest.fixture(scope="module")
def mirror_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("btlp") / "mirror_btlp_dreg.xlsx"
    make_mirror_btlp.build(str(p))
    return str(p)


@pytest.fixture(scope="module")
def wb(mirror_path):
    return M.load_workbook(mirror_path)


@pytest.fixture(scope="module")
def res(wb):
    return R.Resolver(wb)


# ───────────────────────────── 判据 / DoD 1：引擎 ─────────────────────────────
def test_read_topout_count_and_metadata(wb):
    """Topout B 列 → 要验信号清单；A 列 = owner；位宽从 B 列切片剥出。"""
    assert len(wb.topout) == 12
    by = {t.name: t for t in wb.topout}
    assert "d_logic_bt_lp_rx_en" in by and by["d_logic_bt_lp_rx_en"].width == 1
    assert by["d_bt_lp_lna_itrim"].width == 4          # [3:0] 剥出位宽 4
    assert by["d_logic_bt_lp_rx_dcoc_i"].width == 7
    # owner 来自 Topout A 列（报告/账目直接取，免 join）
    assert all(t.owner for t in wb.topout)


def test_resolve_root_classification(wb):
    """每个 Topout 名解析到正确的源对象类别（logic/mux/直连寄存器/RO 回读）。"""
    logic_idx, mux_idx = T.build_index(wb)
    kind = {t.name: T.resolve_root(wb, t.name, logic_idx, mux_idx).kind for t in wb.topout}
    assert kind["d_logic_bt_lp_rx_en"] == T.LOGIC
    assert kind["d_logic_bt_lp_tsensor"] == T.LOGIC
    assert kind["d_en_refbuf_ls"] == T.LOGIC           # 经 _ls 顶层口命中 logic d_en_refbuf
    assert kind["d_bt_lp_lna_itrim"] == T.MUX
    assert kind["clk_force_on"] == T.REGISTER          # 直连 RW 寄存器(dft 恒等观测)
    assert kind["en_dig_clk"] == T.REGISTER
    assert kind["pll_lock_indicator"] == T.RO_READBACK # RO 回读，无 cone


def test_unresolved_name(wb):
    root = T.resolve_root(wb, "d_does_not_exist_anywhere")
    assert root.kind == T.UNRESOLVED


def test_analyze_logic_builds_cone_and_truth_table(wb, res):
    topo = next(t for t in wb.topout if t.name == "d_logic_bt_lp_rx_en")
    r = T.analyze_signal(wb, res, topo, mode="max")
    assert r.status == "ok"
    assert r.node is not None and r.bindings is not None
    assert len(r.vectors) > 0
    bases = {b.base.lower() for b in r.bindings.values()}
    assert "d_bt_lp_linelocal_mode_ctrl" in bases
    assert "d_bt_lp_rx_en_local" in bases


def test_analyze_mux_root(wb, res):
    topo = next(t for t in wb.topout if t.name == "d_bt_lp_lna_itrim")
    r = T.analyze_signal(wb, res, topo, mode="min")
    assert r.root.kind == T.MUX
    assert r.status == "ok"
    assert len(r.vectors) > 0                          # mux case 枚举出真值表


def test_analyze_register_root_passthrough(wb, res):
    topo = next(t for t in wb.topout if t.name == "clk_force_on")
    r = T.analyze_signal(wb, res, topo, mode="max")
    assert r.root.kind == T.REGISTER
    assert r.status == "ok"
    assert r.n_leaves == 1                             # 叶子=该寄存器自身
    # 平凡 pass-through：输出 == 输入写值（逐向量 exp == 驱动值）
    for v in r.vectors:
        assert v.exp_value == v.assignments[list(v.assignments)[0]]


def test_ro_readback_skipped_and_accounted(wb, res):
    topo = next(t for t in wb.topout if t.name == "pll_lock_indicator")
    r = T.analyze_signal(wb, res, topo)
    assert r.status == "skip"
    assert r.root.kind == T.RO_READBACK
    assert r.vectors == []                             # 无 cone → 不出真值表
    assert "回读" in r.note                              # 记账原因可读


def test_analyze_all_covers_full_topout(wb, res):
    results = T.analyze_all(wb, res, mode="min")
    assert len(results) == len(wb.topout)
    by_status = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    # 10 可建(7 logic + 1 mux + 2 register) + 1 skip(RO) ；无 unresolved/error
    assert by_status.get("ok", 0) == 11
    assert by_status.get("skip", 0) == 1
    assert by_status.get("unresolved", 0) == 0
    assert by_status.get("error", 0) == 0


# ───────────────────────────── 判据 / DoD 2：BT_LP 值对照 ─────────────────────────────
def test_value_validation_all_seven_families(wb, res, mirror_path):
    """引擎生成的期望值 == for_test 金标准（独立解析 for_test 页，非循环对照）。"""
    golden = T.load_fortest_golden(mirror_path)
    assert len(golden) == 7                            # 7 条真族
    reps = T.validate_against_golden(wb, res, golden)
    checked = [r for r in reps if r["status"] == "checked"]
    assert len(checked) == 7                           # 全部是节点根、可逐列对照
    total_bad = sum(r["n_bad"] for r in reps)
    total_ok = sum(r["n_ok"] for r in reps)
    assert total_bad == 0
    assert total_ok >= 20                              # 选路+iddq门+多 bit 覆盖


def test_rx_en_structure_matches_signalpath(wb, res):
    """rx_en cone 叶子 == SignalPath 实证：(A?C:B)&(~D) + 4 叶子寄存器(地址/切片/RW-RO)。"""
    sig = next(s for s in wb.logic if s.out_base == "d_logic_bt_lp_rx_en")
    assert sig.expr.replace(" ", "") == "(A?C:B)&(~D)"
    b = res.resolve_signal_inputs(sig)
    by_base = {bd.base.lower(): bd for bd in b.values()}
    # A = 选路位 linelocal_mode_ctrl：RW @ 10'h2D(=45) bit0
    a = by_base["d_bt_lp_linelocal_mode_ctrl"]
    assert a.kind == "RW" and a.address == 45 and a.reg_lsb == 0
    # C = rx_en_local：RW @ 10'h2D bit4
    c = by_base["d_bt_lp_rx_en_local"]
    assert c.kind == "RW" and c.address == 45 and c.reg_lsb == 4
    # B = linectrl_rx_en：RO → force（线控回读）
    assert by_base["d_bt_lp_linectrl_rx_en"].kind == "RO"
    # D = iddq 门：RO → force
    assert by_base["d_bt_lp_pll_dig_dft_iddq_mode"].kind == "RO"


def test_pll_lock_indicator_no_cone_in_golden_path(wb, res):
    """RO 回读在等价性对照里被识别为 no-cone（不误判成可验、不假绿）。"""
    root = T.resolve_root(wb, "pll_lock_indicator")
    reps = T.validate_against_golden(
        wb, res, [{"out": "pll_lock_indicator", "out_width": 1,
                   "inputs": [], "expected": [1], "labels": [], "ncol": 1}])
    assert reps[0]["status"] == "no-cone"
    assert reps[0]["n_ok"] == 0 and reps[0]["n_bad"] == 0
