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
import make_mirror_excel                       # WL 镜像（判据二硬案）
from dreg_verify import excel_model as M
from dreg_verify import expr as E
from dreg_verify import resolver as R
from dreg_verify import rtl_scan
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


# ═════════════════════════ 判据 / DoD 3：WL 硬案结构（cone 引擎扛硬案） ═════════════════════════
@pytest.fixture(scope="module")
def wl_wb(tmp_path_factory):
    p = tmp_path_factory.mktemp("wl") / "mirror_wl_dreg.xlsx"
    make_mirror_excel.build(str(p))
    return M.load_workbook(str(p))


@pytest.fixture(scope="module")
def wl_res(wl_wb):
    return R.Resolver(wl_wb)                    # 默认 cone 级联（对齐 VBA 端到端展开）


def _leaf_bases(result):
    return {b.base.lower() for b in (result.bindings or {}).values()}


def test_wl_topout_all_analyze_no_unresolved(wl_wb, wl_res):
    """Topout 新增页 → 9 个 WL 顶层硬案，引擎全部解析（无 unresolved/error）。"""
    assert len(wl_wb.topout) == 9
    results = T.analyze_all(wl_wb, wl_res, mode="min", max_tests=64)
    assert len(results) == 9
    assert all(r.status == "ok" for r in results)


def test_wl_lo2g5g_cross_boundary_expansion(wl_wb, wl_res):
    """lo2g5g_bias_en：logic←mux 跨边界展开——mux B(logen_mixer_en) 拆成 {选择+local+line}，
    端到端到源寄存器（对齐 make_mirror_excel 注释『VBA 6 输入』；iddq 门在 build 期另加）。"""
    topo = next(t for t in wl_wb.topout if t.name == "d_wl_rf_lo2g5g_bias_en")
    r = T.analyze_signal(wl_wb, wl_res, topo, mode="min")
    assert r.root.kind == T.LOGIC and r.status == "ok"
    bases = _leaf_bases(r)
    # mux B 被展成三件套（不再 force 衔接网）：
    assert "d_wl_rf_logen_lobuf_en_ctrl_mode" in bases   # 选择
    assert "d_wl_rf_logen_mixer_en_local" in bases        # mode=1 数据(RW)
    assert "d_wl_rf_linectrl_logen_mixer_en" in bases     # mode=0 数据(RO 线控)
    assert "d_wl_rf_lo2g5g_bias_test_en" in bases          # C
    # 全是源寄存器/线控叶子，不残留 *_to_mux/*_to_logic 衔接网（前缀整类问题消失）
    assert not any("_to_mux" in b or "_to_logic" in b for b in bases)


def test_wl_tx_epa_five_layer_deep_chain(wl_wb, wl_res):
    """tx_epa_2g_mixer_en：logic←mux←logic←mux←logic 五层深链，全展到源寄存器（6 叶子）。"""
    topo = next(t for t in wl_wb.topout if t.name == "d_wl_rf_tx_epa_2g_mixer_en")
    r = T.analyze_signal(wl_wb, wl_res, topo, mode="min")
    assert r.root.kind == T.LOGIC and r.status == "ok"
    bases = _leaf_bases(r)
    # 深链末端的源寄存器都到位（freq_sel 三件套 + tx2g_en 两件套 + self）
    for need in ("d_wl_rf_freq_sel_mode", "d_wl_rf_freq_sel_local", "d_wl_rf_freq_sel_line",
                 "d_wl_rf_tx2g_en_mode", "d_wl_rf_tx2g_en_local"):
        assert need in bases, need
    assert not any(b.endswith("_to_mux") or b.endswith("_to_logic") for b in bases)


def test_wl_band_trim_mode0_and_mode1_branches(wl_wb, wl_res):
    """band_trim(mux200) 控制=band_2g_sel(mode mux)——mode=0(linectrl_band_sel) 与 mode=1
    (band_2g_sel_local) 两分支都被驱动（R24『mode=0 漏生成』在 Topout 路径不复发）。"""
    topo = next(t for t in wl_wb.topout if t.name == "d_wl_rf_lo2g5g_lcbufc0_2g_pfb_band_trim")
    r = T.analyze_signal(wl_wb, wl_res, topo, mode="min")
    assert r.root.kind == T.MUX and r.status == "ok"
    bases = _leaf_bases(r)
    assert "d_wl_rf_linectrl_band_sel" in bases            # mode=0（logic→mux 线控源）
    assert "d_wl_rf_band_2g_sel_local" in bases            # mode=1（RW）
    assert "d_wl_rf_freq_sel_mode" in bases                # 上游 mode 选择


def test_wl_mixer2g_trim_dedup(wl_wb, wl_res):
    """mixer2g_trim(mux157) 含重复行(t0-t4 两遍)——used_vars 按物理寄存器去重(13 case → 8 源)。"""
    from dreg_verify import mux_gen
    grp = next(g for g in wl_wb.mux if g.out_base == "d_wl_rf_lo2g5g_mixer2g_trim")
    exp = mux_gen.expand_mux_group(wl_wb, wl_res, grp)
    assert len(exp["data_keys"]) == 13                     # 逐 case 保留(含死分支占位)
    distinct = {exp["bindings"][k].base.lower() for k in exp["data_keys"]}
    assert len(distinct) == 8                              # 物理寄存器去重 t0..t7


def test_wl_conflict_surfaced_not_false_green(wl_wb, wl_res):
    """宽 select / 18 行挤一名【冲突案】(lctune/slna)——冲突被 issues 透出，不静默假绿。"""
    for name in ("d_wl_rf_lp5g_rxrf_lna_lctune", "d_bt_rx_slna_1st_bias_trim_gain_cal_wl"):
        topo = next(t for t in wl_wb.topout if t.name == name)
        r = T.analyze_signal(wl_wb, wl_res, topo, mode="min")
        assert r.issues, "%s 冲突应被 issues 透出" % name


# ───────────── WL 缺的形态：多后缀扇出（一个源被多消费方按各自页后缀引用）─────────────
def test_multi_suffix_fanout_resolves_same_source():
    """Q3 后缀规则：同一寄存器源被 logic 以 _to_logic、被 mux 以 _to_mux 引用（多后缀扇出），
    两条 cone 都剥后缀溯到【同一源寄存器】。inline 夹具（不碰两张 mirror 的 logic/mux）。"""
    from dreg_verify.excel_model import (LogicSignal, MuxGroup, MuxCase, MuxCtrl,
                                         RegmapEntry, DregWorkbook)
    # 源寄存器 d_src @ addr 5 bit0（RW）
    reg = {
        "d_src": RegmapEntry("d_src", "SRC", "RW", "0", 0, 0, "WL", address=5),
        "d_other": RegmapEntry("d_other", "OTH", "RW", "0", 0, 0, "WL", address=6),
        "d_ctl": RegmapEntry("d_ctl", "CTL", "RW", "0", 0, 0, "WL", address=7),
    }
    # top_a：logic，引用 d_src_to_logic
    top_a = LogicSignal(row=3, out_name="top_a", out_width=1, expr="A", suffix="to_dft",
                        top_output="0", notes="", owner="WL", assert_id="1",
                        inputs={"A": {"raw": "d_src_to_logic", "base": "d_src",
                                      "width": 1, "msb": None, "lsb": None}})
    # top_b：mux，case0 数据 = d_src_to_mux（同源、另一后缀），case1 = d_other
    cases = [MuxCase(4, "1'b0", "d_src_to_mux", "d_src", 1, None, None),
             MuxCase(5, "1'b1", "d_other_to_mux", "d_other", 1, None, None)]
    grp = MuxGroup(group_no=9, out_name="top_b", out_width=1, ctrl_raw="", ctrl_base="",
                   ctrl_width=1, owner="WL", top_output="0", cases=cases,
                   ctrls=[MuxCtrl("B", "d_ctl_to_mux", "d_ctl", 1)])
    wb = DregWorkbook(logic=[top_a], regmap=reg, tmm={}, sheet_names=[], mux=[grp])
    res = R.Resolver(wb)
    ra = T.analyze_signal(wb, res, M.TopoutSignal(3, "top_a", 1, "WL"))
    rb = T.analyze_signal(wb, res, M.TopoutSignal(4, "top_b", 1, "WL"))
    # top_a 的叶子源 = d_src @5；top_b case0 数据源也 = d_src @5（两后缀剥到同一源）
    a_src = next(b for b in ra.bindings.values() if b.base.lower() == "d_src")
    b_src = next(b for b in rb.bindings.values() if b.base.lower() == "d_src")
    assert a_src.address == 5 and b_src.address == 5
    assert a_src.kind == "RW" and b_src.kind == "RW"


@pytest.mark.xfail(reason="cone 暂不递归进 iddq/ISO 页(同 logic schema 但需 additive 扩展 "
                          "read_logic 后缀剥离 + cone._find_logic 搜索 + resolver._logic_outputs 索引)；"
                          "属【改公共行为】，按护栏留作单开 additive 扩展，先 xfail 标清", strict=False)
def test_iddq_as_a_cone_level():
    """ISO/iddq 当一级：top 信号输入引用 X_to_iddq（X 定义在 iddq 页、组合级）。
    理想 = cone 展进 iddq 页把 X 代入、溯到源寄存器；当前 = 不识别 _to_iddq 后缀 → force 衔接网。"""
    from dreg_verify.excel_model import LogicSignal, RegmapEntry, DregWorkbook
    reg = {"d_iddq_src": RegmapEntry("d_iddq_src", "IS", "RW", "0", 0, 0, "WL", address=8)}
    # iddq 页（同 logic schema）：x = d_iddq_src_to_iddq 的恒等
    x = LogicSignal(row=3, out_name="x", out_width=1, expr="A", suffix="", top_output="0",
                    notes="", owner="WL", assert_id="1",
                    inputs={"A": {"raw": "d_iddq_src_to_iddq", "base": "d_iddq_src",
                                  "width": 1, "msb": None, "lsb": None}})
    top = LogicSignal(row=4, out_name="top_i", out_width=1, expr="A", suffix="to_dft",
                      top_output="0", notes="", owner="WL", assert_id="2",
                      inputs={"A": {"raw": "x_to_iddq", "base": "x_to_iddq",
                                    "width": 1, "msb": None, "lsb": None}})
    wb = DregWorkbook(logic=[top], regmap=reg, tmm={}, sheet_names=[])
    wb.iddq_logic = [x]                        # additive 字段（默认无；此处手挂以表达理想形态）
    res = R.Resolver(wb)
    r = T.analyze_signal(wb, res, M.TopoutSignal(4, "top_i", 1, "WL"))
    bases = _leaf_bases(r)
    assert "d_iddq_src" in bases               # 理想：展进 iddq 页溯到源寄存器（当前会 xfail）


# ═════════════════════════ DoD 5：claim 干净名 provenance + nets 可选过滤 ═════════════════════════
def test_claim_provenance_topout():
    """C1：Topout 路径 collect_claims(is_topout=True) → 探针 provenance='topout'(顶层真名权威)。"""
    from dreg_verify import generator as G
    from dreg_verify.excel_model import LogicSignal
    sig = LogicSignal(row=3, out_name="d_top_x[3:0]", out_width=4, expr="A", suffix="to_dft",
                      top_output="0", notes="", owner="WL", assert_id="1",
                      inputs={"A": {"raw": "a_in[3:0]", "base": "a_in", "width": 4,
                                    "msb": 3, "lsb": 0}})
    # 旧路径(默认)：provenance 'bare'；Topout 路径：'topout'
    old = G.collect_claims(sig, {}, "", is_mux=False)
    new = G.collect_claims(sig, {}, "", is_mux=False, is_topout=True)
    assert old[0]["found_in"] == "bare"
    assert new[0]["found_in"] == "topout"


def test_nets_filter_by_signal(wl_wb):
    """C3：collect_excel_nets(signals=...) 只导指定信号的网；无参全导(逐字节不变)。"""
    all_nets = rtl_scan.collect_excel_nets(wl_wb)
    one = rtl_scan.collect_excel_nets(wl_wb, signals=["d_wl_rf_lo2g5g_bias_en"])
    assert len(one) <= len(all_nets)
    assert "d_wl_rf_lo2g5g_bias_en" in one     # 该信号探针网在
    # 无参 = 旧全导，与不传 signals 一致
    assert rtl_scan.collect_excel_nets(wl_wb) == all_nets


def test_collect_nets_pages_and_dest_suffix(wl_wb):
    """C3：collect_nets 按页过滤 + 按目的地后缀过滤(Q3 同源多消费方选一页)。"""
    only_mux = rtl_scan.collect_nets(wl_wb, pages=["mux"])
    only_dft = rtl_scan.collect_nets(wl_wb, pages=["dft"])
    assert only_mux and only_dft
    # dft 页网都是门/衔接网，不含 mux 输出探针 d_wl_rf_lo2g5g_mixer2g_trim
    assert "d_wl_rf_lo2g5g_mixer2g_trim" in rtl_scan.collect_nets(wl_wb, pages=["mux"])
    # 目的地后缀过滤：只留 *_to_mux 衔接网
    to_mux = rtl_scan.collect_nets(wl_wb, pages=["mux"], dest_suffix="to_mux")
    assert to_mux
    assert all(n.lower().endswith("_to_mux") for n in to_mux)
    # 全页全导（无过滤）≥ 单页
    assert len(rtl_scan.collect_nets(wl_wb)) >= len(only_mux)


def test_filter_nets_by_dest_helper():
    """C3：filter_nets_by_dest 容 'to_dft' 与 '_to_dft' 两种写法，空后缀原样返回。"""
    nets = {"a_to_dft": "x", "b_to_iddq": "y", "c": "z"}
    assert set(rtl_scan.filter_nets_by_dest(nets, "to_dft")) == {"a_to_dft"}
    assert set(rtl_scan.filter_nets_by_dest(nets, "_to_iddq")) == {"b_to_iddq"}
    assert rtl_scan.filter_nets_by_dest(nets, None) == nets


# ═════════════════════════ DoD 6：Topout 报告/账目路径（块B,只新增,堵3陷阱）═════════════════════════
def test_topout_account_default_not_empty_and_owner(wb, res):
    """陷阱①默认不空：以 Topout B 列为外层，不套 top_output_only → 12 行全在(真表 N/I 全0 也不空)。
    owner 直接取 Topout A 列(免 join)。"""
    acc = T.compose_topout_account(wb, res, mode="min")
    assert acc["summary"]["n_total"] == 12               # 默认不空（旧 top_output_only 会选 0）
    assert acc["summary"]["n_ok"] == 11 and acc["summary"]["n_skip"] == 1
    assert all(r["owner"] for r in acc["rows"])           # owner 来自 Topout A 列
    assert acc["summary"]["n_unresolved"] == 0 and acc["summary"]["n_error"] == 0


def test_topout_account_no_false_top_out0_warnings(wb, res):
    """陷阱②不刷 top_out=0 假警告：BT_LP 全干净(无冲突)→ n_with_issues==0、ok 行无 issues。"""
    acc = T.compose_topout_account(wb, res, mode="min")
    assert acc["summary"]["n_with_issues"] == 0
    assert all(not r["issues"] for r in acc["rows"] if r["status"] == "ok")
    # provenance = topout（顶层真名权威）
    assert all(r["provenance"] == "topout" for r in acc["rows"] if r["status"] != "unresolved")


def test_topout_account_surfaces_real_conflicts(wl_wb, wl_res):
    """对照：WL 的真冲突(lctune/slna)被账目如实列出(真 issues，不是 top_out=0 噪声)。"""
    acc = T.compose_topout_account(wl_wb, wl_res, mode="min", max_tests=64)
    assert acc["summary"]["n_unresolved"] == 0
    assert acc["summary"]["n_with_issues"] >= 2           # lctune + slna 冲突
    bad = {r["name"] for r in acc["rows"] if r["issues"]}
    assert "d_wl_rf_lp5g_rxrf_lna_lctune" in bad
    assert "d_bt_rx_slna_1st_bias_trim_gain_cal_wl" in bad


def test_topout_fortest_backfill_includes_mux(wb, res):
    """陷阱③ for_test 回填含 mux：topout_fortest_rows 含 mux 真值表(lna_itrim)；
    旧 build_fortest_rows(默认 include_mux=False) 仍只回填 logic(逐字节不变)。"""
    from dreg_verify import fortest_writer as F
    rep = T.report_for_topout(wb, res, mode="min")
    old = F.build_fortest_rows(rep)                        # 默认 = 旧行为，丢 mux
    new = F.build_fortest_rows(rep, include_mux=True)      # 块B = 含 mux
    assert len(new) == len(old) + 1                        # 多出 lna_itrim mux 表
    assert any("lna_itrim" in g["name"] for g in new)
    assert not any("lna_itrim" in g["name"] for g in old)  # 旧路径确实丢了 mux
    # mux 回填表有输出行 + 输入行（不是空壳）
    g = next(x for x in new if "lna_itrim" in x["name"])
    assert any(r["kind"] == "output" for r in g["rows"])
    assert any(r["kind"] == "input" for r in g["rows"])
