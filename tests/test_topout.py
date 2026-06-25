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


def test_iddq_folded_into_expansion_chain(wl_wb, wl_res):
    """#1/#4-6：门控 logic 根把 iddq 折进展开链【最外层】——跨页 cone 展开页能看到 iddq_mode 这一级
    (此前 iddq 是 cone 出来后旁路 pin、展开链里看不见)。纯展开链显示、不入 .sv(byte-safe)。"""
    topo = next(t for t in wl_wb.topout if t.name == "d_wl_rf_lo2g5g_bias_en")
    r = T.analyze_signal(wl_wb, wl_res, topo, mode="max", exhaustive=True)
    assert r.dft_gate is not None and r.chain
    assert "iddq" in r.chain[0]["subst"].lower() and " ? 0 : " in r.chain[0]["subst"]  # 链首=门控级
    assert len(r.chain) >= 2                                  # 门控级之上还有原 cone 展开级
    topo2 = next(t for t in wl_wb.topout if t.name == "d_wl_rf_lp5g_gm_itrim")
    r2 = T.analyze_signal(wl_wb, wl_res, topo2, mode="min")
    assert not r2.chain or "iddq" not in (r2.chain[0].get("subst") or "").lower()


def test_view_models_carry_form_label(wb):
    """#2：视图模型带 form/form_label(展开后表达式形态 F0-F4)——信号清单『逻辑类型』列。"""
    ms = {m["name"]: m for m in T.topout_view_models(wb, mode="max")}
    assert ms["clk_force_on"]["form"] == "register"                  # F0 直连寄存器
    assert ms["clk_force_on"]["form_label"] == "直连寄存器"
    assert ms["d_logic_bt_lp_lna_agc"]["form_label"] == "选路"        # F2 (A?C:B)
    assert ms["d_logic_bt_lp_rx_en"]["form_label"] == "布尔/位运算"   # F1 (A?C:B)&(~D)
    assert ms["d_bt_lp_lna_itrim"]["form_label"].startswith("选路")   # F2 mux


def test_form_cov_overrides_per_form(wb):
    """#3：per-form 覆盖度 form_cov={形态:档} 只压【该形态】信号，非该形态跟随全局。"""
    base = {m["name"]: (m["form"], m["n_vectors"]) for m in T.topout_view_models(wb, mode="min")}
    fc = {m["name"]: m["n_vectors"]
          for m in T.topout_view_models(wb, mode="min", form_cov={"select": "exhaustive"})}
    for n, (form, nv0) in base.items():
        if form == "select":
            assert fc[n] >= nv0                       # select 形态升档(穷举)
        else:
            assert fc[n] == nv0                       # 其余不受影响
    assert any(fc[n] > base[n][1] for n, (f, _) in base.items() if f == "select")  # 确有变化


def test_form_cov_default_byte_identical(wb):
    """#3：form_cov=None/空(默认)→ 与无 form_cov 完全一致(opt-in，无开销、逐字节不变)。"""
    a = {m["name"]: m["n_vectors"] for m in T.topout_view_models(wb, mode="min")}
    b = {m["name"]: m["n_vectors"] for m in T.topout_view_models(wb, mode="min", form_cov={})}
    assert a == b


def test_form_cov_precedence_sig_over_form(wb):
    """#3：优先级 单点 sig_cov > per-form form_cov > 全局。单点 min 压过 form select=exhaustive。"""
    target = "d_logic_bt_lp_lna_agc"                  # select 形态
    base_min = {m["name"]: m["n_vectors"] for m in T.topout_view_models(wb, mode="min")}
    fc = {m["name"]: m["n_vectors"] for m in T.topout_view_models(
        wb, mode="max", form_cov={"select": "exhaustive"}, sig_cov={target.lower(): "min"})}
    assert fc[target] == base_min[target]             # 单点 min 压过 form exhaustive


def test_form_cov_sv_matches_view(wb):
    """#3：.sv 与 view 同档（gen_sig_cov 映了 form_cov）——select=exhaustive 时 .sv 块向量数==view。"""
    vm = {m["name"]: m["n_vectors"]
          for m in T.topout_view_models(wb, mode="min", form_cov={"select": "exhaustive"})}
    b = T.build_for_topout(wb, mode="min", form_cov={"select": "exhaustive"})
    sv = {st.get("topout_name"): st.get("n_vectors") for ln, st in b["blocks"]}
    assert sv["d_logic_bt_lp_lna_agc"] == vm["d_logic_bt_lp_lna_agc"]


def test_view_models_gated_form_label(wl_wb):
    """#2：门控信号 form_label=门控·<内层>(F3/F4)。"""
    ms = {m["name"]: m for m in T.topout_view_models(wl_wb, mode="min", max_tests=8)}
    assert ms["d_wl_rf_lo2g5g_mixer2g_trim"]["form_label"] == "门控·选路"     # F4 门控套选路
    assert ms["d_wl_rf_lo2g5g_bias_en"]["form_label"] == "门控·布尔/位运算"   # F3 门控套布尔


def test_wl_gated_mux_analyze_pins_iddq_matches_sv(wl_wb, wl_res):
    """M1（缝A 第三分支）：dft 门控的 mux 根 analyze 补 iddq DFT 拍 + dft_gate——res.vectors 与 .sv
    的 mux 块逐数对齐（此前 MUX 分支从不 pin → GUI 真表少一列、n_vectors 与 .sv 差 1）。"""
    name = "d_wl_rf_lo2g5g_mixer2g_trim"
    topo = next(t for t in wl_wb.topout if t.name == name)
    r = T.analyze_signal(wl_wb, wl_res, topo, mode="max", exhaustive=True)
    assert r.root.kind == T.MUX and r.dft_gate is not None      # 门已钉、供 GUI 门输入行显示
    # analyze res.vectors 数 == build 出的 .sv mux 块 n_vectors
    b = T.build_for_topout(wl_wb, mode="max", exhaustive=True)
    sv_n = next(st["n_vectors"] for ln, st in b["blocks"] if st.get("topout_name") == name)
    assert len(r.vectors) == sv_n
    assert any(getattr(v, "dft_pitch", False) for v in r.vectors)  # DFT 拍在内
    # 视图模型 n_vectors 同样对上（GUI 左清单不再与 .sv 矛盾）
    vm = next(m for m in T.topout_view_models(wl_wb, mode="max", exhaustive=True) if m["name"] == name)
    assert vm["n_vectors"] == sv_n


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


def test_collect_topout_nets_covers_register_roots(wb):
    """⭐2026-06-25：collect_topout_nets 收每个可验证 Topout 信号的 assert 探针网，
    含【寄存器/dft 直连根】——这正是 collect_excel_nets/mux/dft 三页都遍历不到、老 nets.txt
    整类漏掉的那批(aac_ctf_bit_sel 类：埋子模块时仿真 CUVUNF 且无提示)。"""
    topo = rtl_scan.collect_topout_nets(wb)
    assert topo                                    # 非空
    assert "clk_force_on" in topo                  # 直连寄存器(RW)根的探针网
    assert "en_dig_clk" in topo
    # 证实老三页口径确实漏掉这俩寄存器根
    old = rtl_scan.collect_excel_nets(wb)
    for k, v in rtl_scan.collect_mux_nets(wb).items():
        old.setdefault(k, v)
    for k, v in rtl_scan.collect_dft_nets(wb).items():
        old.setdefault(k, v)
    assert "clk_force_on" not in old
    assert "en_dig_clk" not in old
    # signals 过滤：只导一个 Topout 名
    one = rtl_scan.collect_topout_nets(wb, signals=["clk_force_on"])
    assert one == {"clk_force_on": one["clk_force_on"]}


def test_collect_nets_topout_page(wb):
    """⭐2026-06-25：collect_nets 支持 'topout' 页——只勾 topout=只导 Topout 探针网；
    寄存器根只在 topout 类别出现；默认(pages=None)四页全取含 topout。"""
    only_topo = rtl_scan.collect_nets(wb, pages=["topout"])
    assert only_topo == rtl_scan.collect_topout_nets(wb)
    only_logic = rtl_scan.collect_nets(wb, pages=["logic"])
    assert "clk_force_on" in only_topo
    assert "clk_force_on" not in only_logic        # 寄存器根不在 logic 页
    assert "clk_force_on" in rtl_scan.collect_nets(wb)            # 默认四页全取含 topout
    # 全页并集 ⊇ 单 topout 页
    assert len(rtl_scan.collect_nets(wb)) >= len(only_topo)


@pytest.fixture(scope="module")
def gated_ls_wb(tmp_path_factory):
    p = tmp_path_factory.mktemp("gatedls") / "mirror_gated_ls.xlsx"
    make_mirror_btlp.build_dft_gated_ls(str(p))
    return M.load_workbook(str(p))


def test_analyze_signal_pins_iddq_for_dft_gated_logic_via_ls(gated_ls_wb):
    """⭐2026-06-25：`d_en_vco_fc_ls` 经 level_shift 直接命中 logic 行(不走 dft 桥)→ dft_obs_name=None，
    但该 logic out_base(d_en_vco_fc)本身是 dft 页门控观测 → analyze_signal 曾漏 iddq 门、与
    generator.build/report(按 out_base 钉门)不一致(.sv/GUI 富表都有、analyze 没有)。修后三路一致。"""
    wb = gated_ls_wb
    assert "d_en_vco_fc" in (wb.dft or {})                        # logic out_base 是 dft 门控观测
    root = T.resolve_root(wb, "d_en_vco_fc_ls")
    assert root.kind == "logic" and getattr(root, "dft_obs_name", None) is None  # 触发条件
    topo = next(t for t in wb.topout if t.name == "d_en_vco_fc_ls")
    res = T.analyze_signal(wb, R.Resolver(wb), topo, root=root)
    ef = {tup[0] for v in res.vectors for tup in (getattr(v, "extra_forces", None) or [])}
    assert any("d_bt_lp_pll_dig_dft_iddq_mode" in w for w in ef)  # iddq 门已钉(修复点)
    # 三路一致：analyze / GUI 富表 / .sv 都含 iddq
    vm = next(m for m in T.topout_view_models(wb) if m["name"] == "d_en_vco_fc_ls")
    assert any("iddq" in (i.get("base", "") or "").lower() for i in vm["inputs"])
    sv, _ = T.render_topout_sv(wb, only=["d_en_vco_fc_ls"], max_tests=8)
    assert "d_bt_lp_pll_dig_dft_iddq_mode" in sv


def test_topout_view_models_carry_probe_prefix(wb):
    """⭐2026-06-25：Topout 视图模型带 probe_net + prefix（GUI『探针前缀』列数据源）——
    配了前缀的信号 prefix 非空、没配的为空；寄存器直连根按 topo 名查前缀。"""
    pp = {"clk_force_on": "U_BT_LP_PLL_DIG"}
    ms = {m["name"]: m for m in T.topout_view_models(wb, probe_prefixes=pp)}
    assert ms["clk_force_on"]["probe_net"] == "clk_force_on"
    assert ms["clk_force_on"]["prefix"] == "U_BT_LP_PLL_DIG"      # 配了 → 显示
    assert ms["en_dig_clk"]["prefix"] == ""                       # 没配 → 空
    # 不传前缀 → 全空
    assert all(m["prefix"] == "" for m in T.topout_view_models(wb))


def test_topout_view_models_assert_id_matches_build(wb):
    """⭐2026-06-25：Topout 视图模型 assert_id = .sv assert 标签的 <R>（仿真报 assert_<R>_T<n>
    失败时据此回查信号）——logic/mux 根=源 R 列/mux 号、寄存器/dft 根=TOP<n>，与 build_for_topout
    块标签逐一对应；故意≠清单第几号(R 是设计师固定 ID、清单按 Topout B 列序)。"""
    ms = {m["name"]: m for m in T.topout_view_models(wb)}
    b = T.build_for_topout(wb)
    n_checked = 0
    for _l, st in b["blocks"]:
        if st.get("n_vectors", 0) > 0:
            nm = st.get("topout_name")
            assert ms[nm]["assert_id"] == st.get("assert_id"), nm
            n_checked += 1
    assert n_checked >= 3
    assert ms["pll_lock_indicator"]["assert_id"] == ""      # RO 回读无 assert


def test_collect_page_nets_covers_dft_and_all_pages(wb):
    """⭐2026-06-25：collect_page_nets 按子模块页(logic/mux/dft/iddq)收探针+force 网，与 GUI
    各 tab 同口径——dft 页现在含【观测输出探针】(老 collect_dft_nets 只收 iddq 门网)；
    未知/空页 → 空 dict 不抛；全类别并集 = 5 个 tab 都覆盖(功能完整)。"""
    import dreg_verify.pageviews as P
    dft_page = rtl_scan.collect_page_nets(wb, "dft")
    assert dft_page                                          # dft 观测输出探针 + 接线网
    # dft 类别 ⊇ 老 iddq 门网口径(额外补了观测输出探针)
    assert set(rtl_scan.collect_dft_nets(wb)) <= set(rtl_scan.collect_nets(wb, pages=["dft"]))
    assert rtl_scan.collect_page_nets(wb, "nonexist") == {}  # 未知页 → 空，不抛
    for pg in P.PAGES:                                       # 每个子模块页都返回 dict、不抛
        assert isinstance(rtl_scan.collect_page_nets(wb, pg), dict)
    # 全类别并集 ⊇ 单页；且涵盖 Topout + 四子模块页
    alln = rtl_scan.collect_nets(wb)
    assert len(alln) >= len(dft_page)
    assert "clk_force_on" in alln                            # Topout 寄存器根也在并集里


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


# ═════════════════════════ 块B（续）：Topout .sv 产出路径（build_for_topout） ═════════════════════════
def test_build_for_topout_emits_sv_all_signals(wb):
    """以 Topout B 列为外层产出 .sv：11 个可建（7 logic + 1 mux + 1 _ls logic + 2 register）有断言，
    RO 回读记账（不产断言但不静默丢）。断言探针贴顶层真名（无前后缀）。"""
    from dreg_verify import sv_writer as W
    b = T.build_for_topout(wb, mode="max")
    assert b["summary"]["n_total"] == 12
    assert b["summary"]["n_emitted"] == 11            # 11 有向量；1 RO 记账
    assert b["summary"]["n_accounted"] == 1
    text = W.render_file(b["blocks"], comments=True)
    assert text.count("assert (") == b["summary"]["n_vectors"] > 0
    # rx_en 断言贴顶层真名（无 _to_logic / 无前缀）
    assert "`ENV_RF.d_logic_bt_lp_rx_en==" in text
    assert "d_logic_bt_lp_rx_en_to_logic" not in text


def test_build_for_topout_register_passthrough(wb):
    """直连寄存器(RW)根 → 平凡 passthrough .sv：RF_WRITE 该寄存器 + 断言 顶层口 == 写值。"""
    from dreg_verify import sv_writer as W
    b = T.build_for_topout(wb, mode="max")
    blk = next((ln, st) for ln, st in b["blocks"] if st.get("topout_name") == "clk_force_on")
    lines, st = blk
    assert st["topout_kind"] == T.REGISTER
    txt = "\n".join(lines)
    assert "`RF_WRITE(" in txt                          # 驱寄存器
    assert "`ENV_RF.clk_force_on==" in txt              # 断言顶层口
    assert st["assert_id"] == "1"                       # #7：Topout 行序命名(clk_force_on=第1行)


def test_topout_assert_labels_are_row_order(wb):
    """#7：.sv 断言标号 = Topout 页行序 1..N（取代旧 TOP0/源 Excel R/mux<N> 混排）。
    第k个 Topout 信号(可产出)→ assert_<k>_T<n>；行序连续、与清单位置一致。"""
    import re
    from dreg_verify import sv_writer as W
    text = W.render_file(T.build_for_topout(wb, mode="max")["blocks"])
    # 每个 emit 的 Topout 信号(非 RO/dup)块顶 assert 标号 R = 其 Topout 行号
    row = {t.name.lower(): str(i + 1) for i, t in enumerate(wb.topout)}
    # clk_force_on=1, d_bt_lp_lna_itrim=2, d_logic_bt_lp_rx_en=9 ...
    assert re.search(r"^assert_1_T\d+:", text, re.M)         # clk_force_on(寄存器根)=第1行
    assert re.search(r"^assert_2_T\d+:", text, re.M)         # d_bt_lp_lna_itrim(mux)=第2行
    assert re.search(r"^assert_9_T\d+:", text, re.M)         # d_logic_bt_lp_rx_en(logic)=第9行
    # 不再有旧 TOP<n> / mux<N> 风格标号
    assert not re.search(r"^assert_TOP\d", text, re.M)
    assert not re.search(r"^assert_mux\d", text, re.M)
    # 标号全是纯数字行序
    rlabels = set(re.findall(r"^assert_(\w+?)_T\d+", text, re.M))
    assert all(x.isdigit() for x in rlabels), rlabels


def test_assert_number_consistent_gui_sv_html(wb):
    """#7 收口：同一信号的断言号在【GUI 清单 / .sv 标号 / HTML 报告 R】三处一致 = Topout 行序。
    防回归用户实证『GUI 61 行 vs 报告/sv 95』——根因=报告/sv 曾用源 Excel R 而非 Topout 行号。"""
    import re
    from dreg_verify import sv_writer as W
    row = {t.name.lower(): str(i + 1) for i, t in enumerate(wb.topout)}
    gui = {m["name"].lower(): m.get("assert_id") for m in T.topout_view_models(wb, mode="min")}
    rep = T.topout_report(wb, mode="min")
    html = {str(t.get("topout_name", "")).lower(): str(t.get("R", "")) for t in rep["tables"]}
    sv = W.render_file(T.build_for_topout(wb, mode="min")["blocks"])
    checked = 0
    for t in wb.topout:
        nm, rn = t.name.lower(), row[t.name.lower()]
        if gui.get(nm):                      # 产出断言的信号(非 RO)
            assert gui[nm] == rn, ("GUI", nm, gui[nm], rn)
            assert html.get(nm) == rn, ("HTML", nm, html.get(nm), rn)
            assert re.search(r"^assert_%s_T" % re.escape(rn), sv, re.M), (".sv", nm, rn)
            checked += 1
    assert checked >= 8


# ───────────── S1 缝B：register/dft 改名根接回 build 的警告/claims 注入圈（M3/M4/M6） ─────────────
def test_build_for_topout_returns_warning_channels(wb):
    """M3：build_for_topout 返回 dict 透出 selfaudit/regmap/supplement 三警告通道 + summary 计数
    （旧版只透 dup_labels，把 G.build 算好的全丢 → 账目 n_with_issues 恒 0）。"""
    b = T.build_for_topout(wb, mode="max")
    for k in ("regmap_warnings", "supplement_warnings", "selfaudit_warnings", "claims"):
        assert k in b, k
        assert isinstance(b[k], list)
    s = b["summary"]
    assert s["n_selfaudit_warnings"] == len(b["selfaudit_warnings"])
    assert s["n_regmap_warnings"] == len(b["regmap_warnings"])
    assert s["n_supplement"] == len(b["supplement_warnings"])


def test_build_for_topout_emits_claims_for_register_root(wb):
    """M4：build_for_topout 产 claims（红区 binder 契约）；register 根探针 claim provenance='topout'
    （顶层真名权威）、kind=probe/identity=output、net_base=顶层名。此前 passthrough 不产任何 claim。"""
    b = T.build_for_topout(wb, mode="max")
    assert b["claims"], "应产出 claims"
    reg_probes = [c for c in b["claims"]
                  if c["kind"] == "probe" and c["net_base"] == "clk_force_on"]
    assert reg_probes, "register 根应有探针 claim"
    c = reg_probes[0]
    assert c["found_in"] == "topout" and c["identity"] == "output"


def test_report_for_topout_includes_register_tables_for_fortest(wb, res):
    """M7：直连寄存器根进 report_for_topout 的 tables（for_test schema：raw/writes/exp_num）——
    此前只过滤 logic/mux、register 整批丢 → GUI『回填 for_test』对 clk_force_on 空白。"""
    rep = T.report_for_topout(wb, res, mode="max")
    reg = [t for t in rep["tables"] if str(t.get("topout_name", "")) == "clk_force_on"]
    assert reg, "register 根应在 report tables 里"
    t = reg[0]
    assert t["is_logic"] and t["type"] == T.REGISTER
    assert t["tests"] and all(("raw" in x and "writes" in x and "exp_num" in x) for x in t["tests"])


def test_register_report_table_includes_iddq_gate_row(wb, res):
    """m2：门控直连寄存器根的报告表/HTML/for_test 含 iddq 门输入行（此前 _register_report_table 不读
    result.dft_gate）。手工给 register result 钉一个真实 RO iddq 门，验证表加门行 + 逐拍门值。"""
    topo = next(t for t in wb.topout if t.name == "clk_force_on")
    r = T.analyze_signal(wb, res, topo, mode="max")
    assert r.root.kind == T.REGISTER
    info = {"raw": "d_bt_lp_pll_dig_dft_iddq_mode", "base": "d_bt_lp_pll_dig_dft_iddq_mode",
            "width": 1, "msb": None, "lsb": None}
    gb = res.resolve("dft_gate_x", info)
    assert gb.resolved and gb.kind == "RO"
    r.dft_gate = (gb, 0)                              # 钉门，透传值 transp=0
    tbl = T._register_report_table(r)
    labels = [i["label"] for i in tbl["inputs"]]
    assert "d_bt_lp_pll_dig_dft_iddq_mode" in labels         # 门作输入行
    grow = next(i for i in tbl["inputs"] if i["label"] == "d_bt_lp_pll_dig_dft_iddq_mode")
    assert grow["ro"] and grow["addr"] is None
    assert all(t["raw"][-1] == 0 for t in tbl["tests"] if not t.get("neg"))  # 功能拍门=透传 0


def test_topout_fortest_rows_backfills_register_root(wb, res):
    """M7：topout_fortest_rows（→ build_fortest_rows）对寄存器根产出 for_test 组（D 行=顶层口、
    有 RF_WRITE 写值），不再空白。"""
    groups = T.topout_fortest_rows(wb, res, mode="max")
    names = {g["name"] for g in groups}
    assert any(n.startswith("clk_force_on") for n in names), "寄存器根应有 for_test 组"
    grp = next(g for g in groups if g["name"].startswith("clk_force_on"))
    out_rows = [r for r in grp["rows"] if r.get("kind") == "output"]
    assert out_rows and str(out_rows[0]["d"]).startswith("clk_force_on")


def test_passthrough_block_injects_block_top_warning_and_claims(wb):
    """M6：register/改名根 .sv 块顶补 // ⚠（此前裸渲染绕过 build 后处理）。手工把 iddq_skipped 塞进
    meta，验证 _passthrough_block 注入块顶 ⚠ + claims 走 is_topout 注入圈。"""
    topo = next(t for t in wb.topout if t.name == "clk_force_on")
    res = T.analyze_signal(wb, R.Resolver(wb), topo, mode="max")
    assert res.root.kind == T.REGISTER
    res.meta["iddq_skipped"] = "iddq 门 d_fake_gate 非可 force 的 RO 网，未补 DFT 拍"
    lines, stats, warns, claims = T._passthrough_block(
        res, topo.name, "BT", "TOP0", T.REGISTER, T.G.GenOptions(), wb)
    assert any("iddq" in ln and ln.startswith("// ⚠") for ln in lines[:2]), "块顶应有 iddq ⚠"
    assert any(c["kind"] == "probe" and c["found_in"] == "topout" for c in claims)


# M8：Topout 路径 logic_overrides（RTL 补充逻辑）——给 lna_agc 套一级 iddq 旁路 ECO（真表只到 DREG）。
_M8_SUPP = {"d_logic_bt_lp_lna_agc": {
    "enabled": True,
    "note": "Topout M8 测试：ECO 顶层口加一级 iddq 旁路",
    "expr": "EXTRA ? 3'b0 : (A ? C : B)",
    "inputs": [
        {"var": "EXTRA", "raw": "d_bt_lp_pll_dig_dft_iddq_mode"},
        {"var": "A", "raw": "d_bt_lp_linelocal_mode_ctrl_to_logic"},
        {"var": "B", "raw": "d_bt_lp_linectrl_lna_agc_to_logic[2:0]"},
        {"var": "C", "raw": "d_bt_lp_local_lna_agc_to_logic[2:0]"},
    ],
}}


def test_topout_logic_overrides_supplements_sv_and_view(wb):
    """M8：Topout 路径接 logic_overrides——补充式被扫成真值表(ECO 新维度 EXTRA)、.sv 块顶 // ⚠ 手工补充、
    summary n_supplement>0；无 override 时 n_supplement==0（防『真值表静默显示补充前旧逻辑』假绿）。"""
    from dreg_verify import sv_writer as W
    # 基线：无 override
    b0 = T.build_for_topout(wb, mode="max")
    assert b0["summary"]["n_supplement"] == 0
    vm0 = next(m for m in T.topout_view_models(wb, mode="max")
               if m["name"] == "d_logic_bt_lp_lna_agc")
    labels0 = {i["label"] for i in vm0["inputs"]}
    assert "d_bt_lp_pll_dig_dft_iddq_mode" not in labels0

    # 套 RTL 补充
    b = T.build_for_topout(wb, mode="max", logic_overrides=_M8_SUPP)
    assert b["summary"]["n_supplement"] == 1
    assert any(nm.startswith("d_logic_bt_lp_lna_agc") for nm, _a, _w in b["supplement_warnings"])
    blk = next(ln for ln, st in b["blocks"]
               if str(st.get("topout_name", "")).startswith("d_logic_bt_lp_lna_agc"))
    assert blk[0].startswith("// ⚠") and "手工补充" in blk[0]
    assert "iddq 旁路" in blk[0]                      # note 进块顶
    # 视图模型：ECO 新输入 EXTRA(iddq) 进真值表维度（否则 GUI 显示补充前逻辑=静默假绿）
    vm = next(m for m in T.topout_view_models(wb, mode="max", logic_overrides=_M8_SUPP)
              if m["name"] == "d_logic_bt_lp_lna_agc")
    assert "d_bt_lp_pll_dig_dft_iddq_mode" in {i["label"] for i in vm["inputs"]}


def test_topout_logic_overrides_does_not_mutate_wb(wb):
    """M8 守 R32：logic_overrides 用后 wb.logic 还原原对象（不原地改、不污染后续无 override 调用）。"""
    before = list(wb.logic)
    T.build_for_topout(wb, mode="min", logic_overrides=_M8_SUPP)
    T.topout_view_models(wb, mode="min", logic_overrides=_M8_SUPP)
    T.topout_report(wb, mode="min", logic_overrides=_M8_SUPP)
    assert wb.logic is not None and list(wb.logic) == before
    assert T.build_for_topout(wb, mode="min")["summary"]["n_supplement"] == 0


def test_topout_neg_all_adds_negatives_logic_and_register(wb):
    """m1：Topout .sv 全局负向(neg_all)——logic 根与 register 根都补自检负向（此前只能逐信号编辑）。"""
    from dreg_verify import sv_writer as W
    b0 = T.build_for_topout(wb, mode="min")
    assert b0["summary"]["n_negative"] == 0          # 默认无全局负向
    b = T.build_for_topout(wb, mode="min", neg_all=True)
    assert b["summary"]["n_negative"] > 0
    # register 根(clk_force_on)也补了负向（passthrough 经 add_negatives）
    reg = next(st for ln, st in b["blocks"] if st.get("topout_name") == "clk_force_on")
    assert reg["n_negative"] > 0


def test_topout_neg_signals_subset(wb):
    """m1：neg_signals 只给指定信号补负向（批量子集，非全开）。"""
    b = T.build_for_topout(wb, mode="min", neg_signals=["clk_force_on"])
    reg = next(st for ln, st in b["blocks"] if st.get("topout_name") == "clk_force_on")
    assert reg["n_negative"] > 0
    others = [st for ln, st in b["blocks"]
              if st.get("topout_name") not in ("clk_force_on",) and st.get("n_vectors", 0)]
    assert all(st.get("n_negative", 0) == 0 for st in others)


def test_topout_report_n_neg_reflects_global_negatives(wb):
    """m1：topout_report 汇总 n_neg 不再硬编码 0——neg_all 时反映实际负向（与 .sv 同口径）。"""
    rep0 = T.topout_report(wb, mode="min")
    assert all(r["n_neg"] == 0 for r in rep0["summary"])
    rep = T.topout_report(wb, mode="min", neg_all=True)
    assert sum(r["n_neg"] for r in rep["summary"]) > 0
    cf = next(r for r in rep["summary"] if r["signal"] == "clk_force_on")
    assert cf["n_neg"] > 0                              # register 根报告也带负向


def test_topout_report_summary_supplement_column(wb):
    """m7：套 RTL 补充后，topout_report 汇总 tab 的 supplement 列不再恒空（与真值表 banner 一致）。"""
    rep = T.topout_report(wb, mode="max", logic_overrides=_M8_SUPP)
    row = next(r for r in rep["summary"] if r["signal"] == "d_logic_bt_lp_lna_agc")
    assert row["supplement"] and "手工补充" in row["supplement"]
    # 无补充信号 supplement 仍空
    other = next(r for r in rep["summary"] if r["signal"] == "clk_force_on")
    assert other["supplement"] == ""


def test_topout_sv_suppresses_mux_bare_probe_noise(wb):
    """t1：Topout .sv 抑制 mux 块顶『top_out=0 用裸名探针』噪声（账目/报告已抑制、保持一致）；
    旧 logic-rooted 路径(generator.build 默认)仍保留该提示，二者刻意分开。"""
    from dreg_verify import generator as G
    from dreg_verify import sv_writer as W
    txt, _ = T.render_topout_sv(wb, mode="max", exhaustive=True)
    assert "top_out=0" not in txt                      # Topout .sv 已抑制
    old = G.build(wb, G.GenOptions(mode="max", exhaustive=True, include_risky=True))
    assert "top_out=0" in W.render_file(old["blocks"])  # 旧路径(默认 False)仍保留


def test_build_for_topout_ro_unresolved_accounted_not_dropped(wb):
    """护栏3：RO 回读 / 未解析在 .sv 里【优雅记账】（块顶注释 + accounted 列表），绝不静默丢、不崩。"""
    from dreg_verify import sv_writer as W
    b = T.build_for_topout(wb, mode="min")
    text = W.render_file(b["blocks"])
    # RO 回读：有记账注释、无断言
    assert "pll_lock_indicator" in text
    assert any(a["name"] == "pll_lock_indicator" and a["status"] == "skip" for a in b["accounted"])
    # 12 个 Topout 信号每个都在产物里【出现】（断言或记账注释），一个不丢
    for t in wb.topout:
        assert t.name in text, t.name


def test_build_for_topout_no_duplicate_assert_labels(wb):
    """全局断言标号唯一（同源去重 + register 'TOP<i>' 独立编号）——重复标号 = 非法 SV。"""
    from dreg_verify import sv_writer as W
    import re
    text = W.render_file(T.build_for_topout(wb, mode="max")["blocks"])
    labels = re.findall(r"^assert_(\S+):", text, re.M)
    assert len(labels) == len(set(labels)), "断言标号重复: %s" % (
        [x for x in labels if labels.count(x) > 1][:5])


def test_render_topout_sv_summary_wraps_once(wb):
    """render_topout_sv(sv_summary=True)：计数器命名块只包一次（不双重包），能 elaborate 形态。"""
    text, b = T.render_topout_sv(wb, mode="min", sv_summary=True)
    assert text.count("begin : dreg_rf_test") == 1
    assert text.count("end : dreg_rf_test") == 1
    assert b["summary"]["n_emitted"] == 11


# ═════════════════════════ 块B（续）：Topout GUI 视图模型 ═════════════════════════
def test_topout_view_models_full_list_and_classification(wb):
    """每个 Topout 信号一个视图模型，按 B 列序，分类齐全（logic/mux/register/ro-readback）。"""
    ms = T.topout_view_models(wb, mode="max")
    assert len(ms) == 12
    by = {m["name"]: m for m in ms}
    assert by["d_logic_bt_lp_rx_en"]["kind"] == T.LOGIC
    assert by["d_bt_lp_lna_itrim"]["kind"] == T.MUX
    assert by["clk_force_on"]["kind"] == T.REGISTER
    assert by["pll_lock_indicator"]["kind"] == T.RO_READBACK
    assert by["pll_lock_indicator"]["status"] == "skip"
    assert by["pll_lock_indicator"]["tests"] == []           # 无 cone → 无真值表（记账 note 说明）
    assert "回读" in by["pll_lock_indicator"]["note"]
    assert all(m["owner"] for m in ms)                        # owner 取自 Topout A 列


def test_topout_view_model_rx_en_truth_table(wb):
    """rx_en 视图模型 = 真值表：4 输入 + 12 列；输入 = SignalPath 实证的源寄存器/线控叶子；
    chain 是 list（rx_en 输入全是直连叶子→链空，正确；跨页展开链由 WL 深链引擎测把关）。"""
    ms = T.topout_view_models(wb, mode="max")
    rx = next(m for m in ms if m["name"] == "d_logic_bt_lp_rx_en")
    assert len(rx["inputs"]) == 4
    assert len(rx["tests"]) == 12
    # 每列 values 与 inputs 对齐（1:1）
    assert all(len(t["values"]) == len(rx["inputs"]) for t in rx["tests"])
    assert isinstance(rx["chain"], list)
    bases = {ip["base"].lower() for ip in rx["inputs"]}
    assert "d_bt_lp_linelocal_mode_ctrl" in bases and "d_bt_lp_rx_en_local" in bases


def test_topout_view_model_values_match_golden(wb, res, mirror_path):
    """视图模型真值表的【期望列】对得上 for_test 金标准（引擎一致：模型↔report↔.sv 同源，非自证）。"""
    golden = T.load_fortest_golden(mirror_path)
    ms = {m["name"].lower(): m for m in T.topout_view_models(wb, mode="max")}
    checked = 0
    for blk in golden:
        m = ms.get(blk["out"].lower())
        if m is None or not m["tests"]:
            continue
        # 模型逐列期望 == 引擎在该输入组合上算的期望（与 validate_against_golden 同引擎）
        reps = T.validate_against_golden(wb, res, [blk])
        if reps[0]["status"] == "checked":
            assert reps[0]["n_bad"] == 0
            checked += 1
    assert checked >= 1


def test_topout_view_model_register_has_passthrough_table(wb):
    """直连寄存器根的视图模型有真值表（1 输入=该寄存器，逐列 输出==写值）。"""
    ms = T.topout_view_models(wb, mode="max")
    reg = next(m for m in ms if m["name"] == "clk_force_on")
    assert reg["kind"] == T.REGISTER and reg["status"] == "ok"
    assert len(reg["inputs"]) == 1 and len(reg["tests"]) >= 1


def test_topout_view_models_wl_surfaces_conflicts(wl_wb):
    """WL 真冲突(lctune/slna)在视图模型 issues 透出（不静默假绿）；解析不了的也优雅记账。"""
    ms = T.topout_view_models(wl_wb, mode="min", max_tests=64)
    by = {m["name"]: m for m in ms}
    assert by["d_wl_rf_lp5g_rxrf_lna_lctune"]["issues"]
    assert by["d_bt_rx_slna_1st_bias_trim_gain_cal_wl"]["issues"]
    assert all(m["kind"] for m in ms)                         # 每个都有分类，无崩溃


# ═════════════════════ 对抗 review 修复回归（2026-06-23 块B 续）═════════════════════
def test_register_passthrough_width_from_resolved_field():
    """⭐major 修复：直连寄存器根 Topout 名【无位宽切片】(裸名)但字段是多 bit → 按字段全宽验，
    不是只 RF_WRITE bit0/断言 1 位的假绿（真表~211 顶层真名常无切片）。"""
    reg = {"d_dcoc": M.RegmapEntry("d_dcoc", "DCOC", "RW", "0", 0, 6, "BT", address=0x2E)}
    wb = M.DregWorkbook(logic=[], regmap=reg, tmm={}, sheet_names=[])
    res = R.Resolver(wb)
    topo = M.TopoutSignal(row=3, name="d_dcoc", width=1, owner="BT")    # 裸名 → _strip_width width=1
    r = T.analyze_signal(wb, res, topo, mode="max", exhaustive=True)
    assert r.root.kind == T.REGISTER and r.status == "ok"
    assert r.out_width == 7                                   # 字段全宽 7（非 1）
    assert any("字段宽 7" in i for i in r.issues)              # 加宽告警可见（不静默）
    assert max(v.exp_value for v in r.vectors) > 1            # 高位真被驱动/断言（非只 bit0）


def test_register_passthrough_assert_carries_width_slice():
    """⭐功能 bug 修复(2026-06-24)：直连寄存器 / dft 改名根的断言 LHS 必须带位宽切片
    (aac_ctf_bit_sel[2:0] 那类)——旧 .sv 只断言裸名(`ENV_RF.d_dcoc==)=只比 bit0 假绿，且与信号清单
    显示名 d_dcoc[6:0] 不一致。修后：显示什么、.sv 就断言什么（_topout_slice_suffix 单一口径）。"""
    from dreg_verify import sv_writer as W
    # (a) 裸名但解析出的字段是 7 bit → 断言贴推断切片 [6:0]，不再裸名
    reg = {"d_dcoc": M.RegmapEntry("d_dcoc", "DCOC", "RW", "0", 0, 6, "BT", address=0x2E)}
    wb = M.DregWorkbook(logic=[], regmap=reg, tmm={}, sheet_names=[])
    wb.topout = [M.TopoutSignal(3, "d_dcoc", 1, "BT")]
    txt = W.render_file(T.build_for_topout(wb, mode="max", exhaustive=True)["blocks"])
    assert "`ENV_RF.d_dcoc[6:0]==" in txt                     # 带推断切片
    assert "`ENV_RF.d_dcoc==" not in txt                      # 旧 bug：裸名断言不再出现
    ms = {m["name"]: m for m in T.topout_view_models(wb, mode="max")}
    assert ms["d_dcoc"]["disp"] == "d_dcoc[6:0]"              # 显示名与断言 LHS 同口径

    # (b) B 列显式切片(含 [14:12] 那类非零起始) → 断言贴显式切片，不是 [w-1:0]
    reg2 = {"d_sel": M.RegmapEntry("d_sel", "SEL", "RW", "0", 12, 14, "BT", address=0x10)}
    wb2 = M.DregWorkbook(logic=[], regmap=reg2, tmm={}, sheet_names=[])
    wb2.topout = [M.TopoutSignal(3, "d_sel", 3, "BT", msb=14, lsb=12, raw="d_sel[14:12]")]
    txt2 = W.render_file(T.build_for_topout(wb2, mode="max", exhaustive=True)["blocks"])
    assert "`ENV_RF.d_sel[14:12]==" in txt2                   # 忠于真表显式切片
    assert "`ENV_RF.d_sel==" not in txt2


def test_topout_view_models_per_signal_coverage(wb):
    """⭐N3：单点覆盖度——全局 min 时给【一个】信号设 exhaustive → 只它用例数变成穷举档，其余仍 min。"""
    base = {m["name"]: m["n_vectors"] for m in T.topout_view_models(wb, mode="min")}
    exh = {m["name"]: m["n_vectors"] for m in T.topout_view_models(wb, mode="max", exhaustive=True)}
    target = next((n for n in base if exh.get(n, 0) > base[n]), None)   # 找 min<穷举 的信号
    assert target is not None, "夹具里没有 min≠exhaustive 的信号，无法验单点档"
    one = {m["name"]: m["n_vectors"]
           for m in T.topout_view_models(wb, mode="min", sig_cov={target.lower(): "exhaustive"})}
    assert one[target] == exh[target]                       # 该信号=穷举档
    assert all(one[n] == base[n] for n in base if n != target)   # 其它信号仍跟随全局 min


def test_topout_sv_per_signal_coverage_matches_view(wb):
    """⭐N3：.sv 导出与清单同档——单点 exhaustive 的信号在 build_for_topout 也按穷举出向量。"""
    base = {m["name"]: m["n_vectors"] for m in T.topout_view_models(wb, mode="min")}
    exh = {m["name"]: m["n_vectors"] for m in T.topout_view_models(wb, mode="max", exhaustive=True)}
    target = next((n for n in base if exh.get(n, 0) > base[n]), None)
    assert target is not None
    b = T.build_for_topout(wb, mode="min", sig_cov={target.lower(): "exhaustive"})
    blk = next((st for _ln, st in b["blocks"] if st.get("topout_name") == target), None)
    assert blk is not None and blk["n_vectors"] == exh[target]


def test_build_for_topout_exposes_dup_labels(wb):
    """⭐N9：build_for_topout 透出 generator.build 算好的 dup_labels（旧 Topout 路径丢弃→静默导出非法 SV）。"""
    b = T.build_for_topout(wb, mode="min")
    assert "dup_labels" in b and isinstance(b["dup_labels"], list)


def test_topout_report_only_filters_to_checked(wb):
    """⭐N6：topout_report(only=...) 把 summary/tables/verifiability 限定到勾选信号（与 .sv 导出同口径）。"""
    rep = T.topout_report(wb, only=["d_logic_bt_lp_rx_en"])
    assert {s["signal"].lower() for s in rep["summary"]} == {"d_logic_bt_lp_rx_en"}
    assert all(t.get("topout_name", "").lower() == "d_logic_bt_lp_rx_en" for t in rep["tables"])
    assert {v["signal"].lower() for v in rep["verifiability"]["signals"]} == {"d_logic_bt_lp_rx_en"}
    # None=全部（不过滤）：信号数 = Topout 清单全量
    assert len(T.topout_report(wb)["summary"]) == len(wb.topout)


def test_register_unresolved_is_error_not_false_green():
    """⭐minor 修复：直连寄存器(RW)但解析不到(如缺地址) → status='error'，不发『ok』绿块驱不存在的网。"""
    reg = {"d_noaddr": M.RegmapEntry("d_noaddr", "NA", "RW", "0", 0, 0, "BT")}   # address=None
    wb = M.DregWorkbook(logic=[], regmap=reg, tmm={}, sheet_names=[])
    res = R.Resolver(wb)
    r = T.analyze_signal(wb, res, M.TopoutSignal(3, "d_noaddr", 1, "BT"))
    assert r.root.kind == T.REGISTER
    assert r.status == "error" and r.vectors == []           # 未解析 → error（非假绿 ok）


def test_validate_against_golden_survives_cone_failure(wb, res, mirror_path, monkeypatch):
    """⭐major 修复：金标准对照里某 logic 根 cone 展开失败(node=None) → 标 no-cone 记账，
    不让 evaluate_at 抛 ValueError 把【整批】对照拖崩（真表有成环/不可解析信号时尤甚）。"""
    golden = T.load_fortest_golden(mirror_path)
    orig = T.analyze_signal

    def patched(wb_, res_, topo_, **kw):
        r = orig(wb_, res_, topo_, **kw)
        if topo_.name == "d_logic_bt_lp_rx_en":              # 强制这块 cone 失败
            r.node = None; r.status = "error"; r.issues = ["forced cone fail"]
        return r
    monkeypatch.setattr(T, "analyze_signal", patched)
    reps = T.validate_against_golden(wb, res, golden)        # 不应抛
    assert len(reps) == len(golden)                          # 所有块都有报告（没崩）
    bad = next(r for r in reps if r["out"] == "d_logic_bt_lp_rx_en")
    assert bad["status"] == "no-cone"
    assert sum(1 for r in reps if r["status"] == "checked") == len(golden) - 1   # 其余仍 checked


def test_analyze_signal_never_throws_on_unexpected(wb, res):
    """⭐minor 修复(护栏3)：analyze_signal 遇意外异常也不抛，记账成 error（一个坏信号不连累整批/不崩 GUI）。"""
    from dreg_verify import vectors as V2
    topo = next(t for t in wb.topout if t.name == "d_logic_bt_lp_rx_en")
    orig = V2.generate_vectors
    try:
        V2.generate_vectors = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaboom"))
        r = T.analyze_signal(wb, res, topo)                  # 不应抛
        assert r.status == "error" and any("kaboom" in i for i in r.issues)
    finally:
        V2.generate_vectors = orig


def test_dup_source_reason_is_human_readable():
    """⭐nit 修复：两个 Topout 名映到同一源对象 → 记账 reason 是人读说明，不是状态字符串回声。"""
    sig = M.LogicSignal(row=3, out_name="top_x", out_width=1, expr="A", suffix="to_dft",
                        top_output="0", notes="", owner="BT", assert_id="1",
                        inputs={"A": {"raw": "d_a", "base": "d_a", "width": 1,
                                      "msb": None, "lsb": None}})
    sig._ls_name = "top_x_ls"                                # top_x 也可被 'top_x_ls' 命中
    reg = {"d_a": M.RegmapEntry("d_a", "A", "RW", "0", 0, 0, "BT", address=1)}
    wb = M.DregWorkbook(logic=[sig], regmap=reg, tmm={}, sheet_names=[])
    wb.topout = [M.TopoutSignal(3, "top_x", 1, "BT"), M.TopoutSignal(4, "top_x_ls", 1, "BT")]
    b = T.build_for_topout(wb, mode="min")
    dup = [a for a in b["accounted"] if a["status"] == "dup-source"]
    assert dup and dup[0]["reason"] != "dup-source"          # 人读说明，非状态回声
    assert "同源" in dup[0]["reason"]


def test_view_model_disp_name_carries_width_slice(wb):
    """⭐信号清单显示名带位宽切片(aac_ctf_bit_sel[2:0] 那类，2026-06-24)：多 bit 信号 disp 带 [w-1:0]，
    1 bit 不带；name(查找 key)仍是剥位宽的基名(不变，否则选中/编辑/解析全断)。"""
    ms = {m["name"]: m for m in T.topout_view_models(wb, mode="min", max_tests=8)}
    agc = ms["d_logic_bt_lp_lna_agc"]
    assert agc["width"] == 3 and agc["disp"] == "d_logic_bt_lp_lna_agc[2:0]"
    assert agc["name"] == "d_logic_bt_lp_lna_agc"            # key 不带切片(不变)
    itrim = ms["d_bt_lp_lna_itrim"]                          # mux 根、B 列写了 [3:0]
    assert itrim["disp"] == "d_bt_lp_lna_itrim[3:0]"
    one = ms["clk_force_on"]                                 # 1 bit → 不加切片
    assert one["width"] == 1 and one["disp"] == "clk_force_on"
