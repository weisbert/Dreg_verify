# -*- coding: utf-8 -*-
"""WL_RFTRX 结构 mux 支持的测试（2026-06-03 第十四轮）。

WL 表与 LPBT 表的结构差异（两轮 inspect_mux 实证）：
  ① 多控制信号：case = {B,C,D,E} 拼接（B 高位），可含 don't-care x 位
  ② 控制三来源：logic 行 / 寄存器直出(RW→RF_WRITE) / 上游 mux 输出(级联)
  ③ 数据三来源：RW(RF_WRITE) / RO 线控(force) / 级联
  ④ top_out 全 0：输出不在顶层，探针需 scan_rtl 前缀

LPBT 兼容性测试在 test_mux.py（一个都不能动）；本文件只测 WL 新行为。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import excel_model, expr as E, generator, mux_gen, resolver  # noqa: E402
from fixtures import build_wl_workbook, build_workbook  # noqa: E402


@pytest.fixture(scope="module")
def wl_wb(tmp_path_factory):
    path = tmp_path_factory.mktemp("wl") / "wl_dreg.xlsx"
    build_wl_workbook(str(path))
    return excel_model.load_workbook(str(path))


@pytest.fixture(scope="module")
def lpbt_wb(tmp_path_factory):
    path = tmp_path_factory.mktemp("lpbt") / "lpbt_dreg.xlsx"
    build_workbook(str(path), with_mux=True)
    return excel_model.load_workbook(str(path))


def _grp(wb, out_base):
    g = next((g for g in wb.mux if g.out_base == out_base), None)
    assert g is not None, "找不到 mux 组 %s（现有: %s）" % (out_base, [x.out_base for x in wb.mux])
    return g


# ───────────── ① 数据层：多控制读取 ─────────────
def test_wl_read_mux_group_count(wl_wb):
    """5 个组全部读出（按 G 基名归并）。"""
    assert len(wl_wb.mux) == 5
    assert [g.group_no for g in wl_wb.mux] == [1, 2, 3, 4, 5]


def test_wl_multi_ctrl_collected(wl_wb):
    """组4（多控制）：ctrls 按 B/C 列序收集，B 在首=case 高位。"""
    g4 = _grp(wl_wb, "d_wl_rf_tx_rc_code")
    assert len(g4.ctrls) == 2
    assert g4.is_multi_ctrl
    assert g4.ctrls[0].letter == "B"
    assert g4.ctrls[0].base == "d_wl_rf_rc_code_lut_en"
    assert g4.ctrls[0].width == 1
    assert g4.ctrls[1].letter == "C"
    assert g4.ctrls[1].base == "d_wl_rf_bwctrl"
    assert g4.ctrls[1].width == 4
    # case 总宽 = 1+4 = 5（与 F 列 5'b... 对上）
    assert g4.ctrl_total_width == 5
    # 兼容别名指向第一个控制（B 列）
    assert g4.ctrl_base == "d_wl_rf_rc_code_lut_en"
    assert g4.ctrl_width == 1


def test_wl_single_ctrl_unchanged_shape(wl_wb):
    """单控制组：ctrls 长度 1，兼容别名与 ctrls[0] 一致（LPBT 形状）。"""
    g1 = _grp(wl_wb, "d_wl_rf_lna_gain")
    assert len(g1.ctrls) == 1
    assert not g1.is_multi_ctrl
    assert g1.ctrls[0].letter == "B"
    assert g1.ctrl_base == "d_wl_rf_lna_gain_ctrl_mode"
    assert g1.ctrl_width == 1
    assert g1.ctrl_total_width == 1


def test_wl_top_out_zero(wl_wb):
    """WL 输出全部 top_out=0（不在顶层）。"""
    for g in wl_wb.mux:
        assert not g.is_top, "%s 应为非顶层输出" % g.out_base


def test_wl_multi_ctrl_expr_text(wl_wb):
    """多控制组的 expr 文本显示拼接 {c1,c2}。"""
    g4 = _grp(wl_wb, "d_wl_rf_tx_rc_code")
    assert g4.expr.startswith("case({d_wl_rf_rc_code_lut_en,d_wl_rf_bwctrl})")
    # 单控制组不变
    g1 = _grp(wl_wb, "d_wl_rf_lna_gain")
    assert g1.expr.startswith("case(d_wl_rf_lna_gain_ctrl_mode)")


def test_wl_ctrl_mismatch_rows_empty(wl_wb):
    """fixture 同组各行控制列一致 → ctrl_mismatch_rows 全空。"""
    for g in wl_wb.mux:
        assert g.ctrl_mismatch_rows == []


def test_lpbt_mux_ctrls_compat(lpbt_wb):
    """LPBT 表：单控制 → ctrls 长度 1 且兼容别名逐字节不变（test_mux.py 的断言镜像）。"""
    g1 = lpbt_wb.mux[0]
    assert len(g1.ctrls) == 1
    assert g1.ctrls[0].letter == "B"
    assert g1.ctrl_base == "d_logic_bt_lp_lna_agc"
    assert g1.ctrl_width == 3
    assert g1.ctrl_total_width == 3
    assert not g1.is_multi_ctrl
    assert g1.ctrl_mismatch_rows == []


def test_muxgroup_old_constructor_compat():
    """旧构造方式（单控制三标量，不传 ctrls）仍可用——test_mux.py 的 _mini_mux_group 依赖。"""
    grp = excel_model.MuxGroup(group_no=1, out_name="d_x[3:0]", out_width=4,
                               ctrl_raw="d_ctrl_to_mux[2:0]", ctrl_base="d_ctrl",
                               ctrl_width=3, owner="", top_output=1, cases=[])
    assert len(grp.ctrls) == 1
    assert grp.ctrls[0].base == "d_ctrl"
    assert grp.ctrl_base == "d_ctrl"
    assert grp.ctrl_width == 3
    assert grp.ctrl_total_width == 3
    # 无控制信号（空 ctrl_base）→ ctrls 空，别名回退到传入值
    grp2 = excel_model.MuxGroup(group_no=2, out_name="d_y", out_width=1,
                                ctrl_raw="", ctrl_base="", ctrl_width=1,
                                owner="", top_output=1, cases=[])
    assert grp2.ctrls == []
    assert grp2.ctrl_base == ""
    assert grp2.ctrl_total_width == 1


# ───────────── ② split_case_value / concat_case_parts ─────────────
def test_split_case_value_golden():
    """黄金样本（真表 row149-154）：5'b10010 按 [1,4] 拆 → lut_en=1, bwctrl=2。"""
    v, w, dc = E.parse_case_literal("5'b10010")
    assert (v, w, dc) == (0b10010, 5, 0)
    assert E.split_case_value(v, dc, [1, 4]) == [(1, 0), (2, 0)]


def test_split_case_value_dontcare():
    """5'b0xxxx 按 [1,4] 拆 → lut_en=0(确定), bwctrl=全 don't-care。"""
    v, w, dc = E.parse_case_literal("5'b0xxxx")
    assert (v, w, dc) == (0, 5, 0b01111)
    assert E.split_case_value(v, dc, [1, 4]) == [(0, 0), (0, 0b1111)]


def test_split_case_value_single():
    """单控制退化：拆分结果 = 原值。"""
    assert E.split_case_value(0b010, 0, [3]) == [(0b010, 0)]


def test_split_concat_roundtrip():
    """split ↔ concat 互逆。"""
    widths = [1, 4, 2]
    for value, dc in [(0b1001011, 0), (0b0000000, 0b0011110), (0b1111111, 0b1000001)]:
        parts = E.split_case_value(value, dc, widths)
        triples = [(p[0], w, p[1]) for p, w in zip(parts, widths)]
        assert E.concat_case_parts(triples) == (value, sum(widths), dc)


# ───────────── ③ 数据层：组结构细节 ─────────────
def test_wl_cascade_group_cases(wl_wb):
    """组3（级联控制）：4 个 case 与 lut0..3 一一对应。"""
    g3 = _grp(wl_wb, "d_wl_rf_tx_bwctrl")
    assert len(g3.cases) == 4
    assert [c.case_raw for c in g3.cases] == ["4'b0000", "4'b0001", "4'b0010", "4'b0011"]
    assert [c.input_base for c in g3.cases] == [
        "d_wl_rf_tx_bw_lut0", "d_wl_rf_tx_bw_lut1", "d_wl_rf_tx_bw_lut2", "d_wl_rf_tx_bw_lut3"]
    # 控制信号 = 组2 的输出（级联）
    assert g3.ctrl_base == "d_wl_rf_bwctrl"
    g2 = _grp(wl_wb, "d_wl_rf_bwctrl")
    assert g3.ctrl_base == g2.out_base


def test_wl_multi_ctrl_group_cases(wl_wb):
    """组4（多控制）：5 个 case（1 个 don't-care local + 4 个 lut）。"""
    g4 = _grp(wl_wb, "d_wl_rf_tx_rc_code")
    assert len(g4.cases) == 5
    assert g4.cases[0].case_raw == "5'b0xxxx"
    assert g4.cases[0].input_base == "d_wl_rf_tx_rc_local"
    assert g4.cases[1].case_raw == "5'b10000"
    assert g4.cases[4].input_base == "d_wl_rf_tx_rc_lut3"


def test_wl_logic_ctrl_group(wl_wb):
    """组5（logic 行控制 = LPBT 形态）：控制信号在 logic 页存在。"""
    g5 = _grp(wl_wb, "d_wl_rf_dpd_path")
    assert g5.ctrl_base == "d_wl_rf_fb_en"
    logic_bases = {s.out_base.lower() for s in wl_wb.logic}
    assert g5.ctrl_base.lower() in logic_bases


# ───────────── ④ 解析层：控制/数据三来源 ─────────────
@pytest.fixture(scope="module")
def wl_resolver(wl_wb):
    return resolver.Resolver(wl_wb)


def test_wl_ctrl_reg_direct(wl_wb, wl_resolver):
    """组1：控制信号是寄存器直出（RW）→ source='reg'，RF_WRITE 驱动，无 issue。"""
    g1 = _grp(wl_wb, "d_wl_rf_lna_gain")
    exp = mux_gen.expand_mux_group(wl_wb, wl_resolver, g1)
    assert exp["issues"] == []
    assert len(exp["ctrl_drivers"]) == 1
    d = exp["ctrl_drivers"][0]
    assert d["source"] == "reg"
    assert d["key"] == "c:reg"                       # 单控制 idx=0 保持 'c:' 前缀
    assert d["binding"].kind == "RW" and d["binding"].address == 0x50
    # used_vars: 控制在前数据在后
    assert exp["used_vars"][0] == "c:reg"
    assert exp["data_keys"] == ["d:0", "d:1"]


def test_wl_data_ro_force_allowed(wl_wb, wl_resolver):
    """组1：线控数据（RO→force）放行，local 数据（RW→RF_WRITE）；旧版"必须RW"硬门已开。"""
    g1 = _grp(wl_wb, "d_wl_rf_lna_gain")
    exp = mux_gen.expand_mux_group(wl_wb, wl_resolver, g1)
    assert exp["issues"] == []
    b_line = exp["bindings"]["d:0"]                  # linectrl（RO 线控）
    b_local = exp["bindings"]["d:1"]                 # local（RW）
    assert b_line.kind == "RO"
    assert b_local.kind == "RW" and b_local.address == 0x51


def test_wl_ctrl_mux_cascade_recipe(wl_wb, wl_resolver):
    """组3：控制信号 = 组2 的输出 → source='mux'，上游配方（优先 RW 载体 + 上游控制驱动）。"""
    g3 = _grp(wl_wb, "d_wl_rf_tx_bwctrl")
    exp = mux_gen.expand_mux_group(wl_wb, wl_resolver, g3)
    assert exp["issues"] == []
    d = exp["ctrl_drivers"][0]
    assert d["source"] == "mux"
    assert d["upstream"].out_base == "d_wl_rf_bwctrl"
    recipe = d["recipe"]
    # 载体优先 RW = 组2 的 local 数据（case 1'b1, h53），不是 RO 线控
    assert recipe["carrier_ci"] == 1
    assert recipe["carrier_key"] == "m2.d:1"
    assert recipe["bindings"]["m2.d:1"].kind == "RW"
    # 上游控制 = bwctrl_mode 寄存器直出；载体 case 1'b1 → 上游控制驱到 1
    assert len(recipe["ctrl_drivers"]) == 1
    assert recipe["ctrl_drivers"][0]["source"] == "reg"
    assert recipe["ctrl_drivers"][0]["key"] == "m2.c:reg"
    assert recipe["ctrl_values"] == [1]


def test_wl_multi_ctrl_drivers(wl_wb, wl_resolver):
    """组4：两个控制 → 2 个 driver（B=寄存器直出，C=mux 级联）。"""
    g4 = _grp(wl_wb, "d_wl_rf_tx_rc_code")
    exp = mux_gen.expand_mux_group(wl_wb, wl_resolver, g4)
    assert exp["issues"] == []
    assert len(exp["ctrl_drivers"]) == 2
    assert exp["ctrl_drivers"][0]["source"] == "reg"           # B: lut_en
    assert exp["ctrl_drivers"][0]["key"] == "c:reg"
    assert exp["ctrl_drivers"][1]["source"] == "mux"           # C: bwctrl（级联到组2）
    assert exp["ctrl_drivers"][1]["upstream"].group_no == 2


def test_wl_logic_ctrl_lpbt_path(wl_wb, wl_resolver):
    """组5：logic 行控制（LPBT 形态）→ source='logic' + 兼容别名 line/local/ctrl_sig 都在。"""
    g5 = _grp(wl_wb, "d_wl_rf_dpd_path")
    exp = mux_gen.expand_mux_group(wl_wb, wl_resolver, g5)
    assert exp["issues"] == []
    d = exp["ctrl_drivers"][0]
    assert d["source"] == "logic"
    # LPBT 兼容别名（make_mux_vectors 现有路径靠它们）
    assert exp["ctrl_sig"] is not None and exp["ctrl_sig"].out_base == "d_wl_rf_fb_en"
    assert exp["line"] is not None and exp["line"]["kind"] == "RO"     # fb_line
    assert exp["local"] is not None and exp["local"]["kind"] == "RW"   # fb_local
    assert exp["line"]["key"] == "c:B" and exp["local"]["key"] == "c:C"


def test_wl_resolver_recognizes_mux_output(wl_wb):
    """resolver：logic 输入 = mux 组输出（mux→logic 级联）→ found_in='mux-output'（不再 wire 兜底）。"""
    r = resolver.Resolver(wl_wb)
    sig = next(s for s in wl_wb.logic if s.out_base == "d_wl_rf_lna_gain_dly")
    b = r.resolve_signal_inputs(sig)["A"]
    assert b.found_in == "mux-output"
    assert b.kind == "RO"
    assert b.wire == "d_wl_rf_lna_gain_to_logic"     # force 字面衔接网（不是基名）
    assert "mux" in b.note


def test_wl_resolver_mux_output_with_prefix(wl_wb):
    """配置探针前缀后：mux 输出衔接网 → prefixed-wire（确认存在，可生成）。"""
    r = resolver.Resolver(wl_wb,
                          wire_prefixes={"d_wl_rf_lna_gain_to_logic": "U_DREG.U_MUX"})
    sig = next(s for s in wl_wb.logic if s.out_base == "d_wl_rf_lna_gain_dly")
    b = r.resolve_signal_inputs(sig)["A"]
    assert b.found_in == "prefixed-wire"
    assert b.wire == "U_DREG.U_MUX.d_wl_rf_lna_gain_to_logic"


def test_wl_cascade_cycle_detection(wl_wb, wl_resolver):
    """mux 级联成环 → issue（不递归爆栈）。"""
    import copy
    # 人造环：组2 的控制改成组3 的输出（2→3→2）。
    # 控制位宽要与组2 自己的 case 位宽(1bit)一致——否则位宽不一致的 issue 先挡住，到不了环检测。
    wb2 = copy.copy(wl_wb)
    g2 = copy.deepcopy(_grp(wl_wb, "d_wl_rf_bwctrl"))
    g3 = copy.deepcopy(_grp(wl_wb, "d_wl_rf_tx_bwctrl"))
    g2.ctrls = [excel_model.MuxCtrl("B", "d_wl_rf_tx_bwctrl_to_mux",
                                    "d_wl_rf_tx_bwctrl", 1)]
    wb2.mux = [g2, g3]
    r = resolver.Resolver(wb2)
    exp = mux_gen.expand_mux_group(wb2, r, g3)
    assert any("成环" in i for i in exp["issues"]), exp["issues"]


def test_wl_cascade_width_mismatch_blocked(wl_wb):
    """上游载体 case 位宽与上游控制拼接总宽不一致 → issue（不静默生成错位 case）。"""
    import copy
    wb2 = copy.copy(wl_wb)
    g2 = copy.deepcopy(_grp(wl_wb, "d_wl_rf_bwctrl"))
    g3 = copy.deepcopy(_grp(wl_wb, "d_wl_rf_tx_bwctrl"))
    # 组2 的控制声明成 5bit，但它的 case 是 1'b0/1'b1 → 位宽不一致
    g2.ctrls = [excel_model.MuxCtrl("B", "d_wl_rf_tx_bwctrl_to_mux[4:0]",
                                    "d_wl_rf_tx_bwctrl", 5)]
    wb2.mux = [g2, g3]
    r = resolver.Resolver(wb2)
    exp = mux_gen.expand_mux_group(wb2, r, g3)
    assert any("不一致" in i for i in exp["issues"]), exp["issues"]


# ───────────── ⑤ 生成层：通用向量生成 ─────────────
def test_wl_vectors_reg_ctrl(wl_wb, wl_resolver):
    """组1（寄存器直出控制 + RO/RW 混合数据）：每 case 一条，c:reg 写 case 值。"""
    g1 = _grp(wl_wb, "d_wl_rf_lna_gain")
    exp = mux_gen.expand_mux_group(wl_wb, wl_resolver, g1)
    vecs, meta = mux_gen.make_mux_vectors(g1, exp, mode="min")
    assert len(vecs) == 2                                # 2 个 case
    assert meta["scan_path"] == "direct"
    assert not meta["value_collision"]
    # T0: case 1'b0 → ctrl_mode=0 选线控；数据互异值 [1,2]（3bit 窄寄存器从 1 递增）
    t0 = vecs[0]
    assert t0.assignments["c:reg"] == 0
    assert t0.assignments["d:0"] == 1 and t0.assignments["d:1"] == 2
    assert t0.exp_value == 1                             # 选中线控 → 期望=线控 force 的值
    # T1: case 1'b1 → ctrl_mode=1 选 local
    t1 = vecs[1]
    assert t1.assignments["c:reg"] == 1
    assert t1.exp_value == 2                             # 选中 local → 期望=local 写的值


def test_wl_vectors_cascade_ctrl(wl_wb, wl_resolver):
    """组3（mux 级联控制）：每条向量含上游配方（m2.c:reg=1 选 local + m2.d:1=case值）。"""
    g3 = _grp(wl_wb, "d_wl_rf_tx_bwctrl")
    exp = mux_gen.expand_mux_group(wl_wb, wl_resolver, g3)
    vecs, meta = mux_gen.make_mux_vectors(g3, exp, mode="min")
    assert len(vecs) == 4                                # 4 个 case（lut0..3）
    # 每条向量：上游 bwctrl_mode=1（选 local）、上游 bwctrl_local=本 case 的值
    for ci, v in enumerate(vecs):
        assert v.assignments["m2.c:reg"] == 1, "上游模式必须选 local 路径"
        assert v.assignments["m2.d:1"] == ci, "上游载体寄存器写 case 值 %d" % ci
    # 数据互异值 0xA 递减（5bit lut 寄存器），期望=被选中 lut 的值
    assert vecs[0].exp_value == 0xA and vecs[2].exp_value == 0x8
    # 上游配方键也在 used_vars 里（compute_drives 要写它们）
    assert "m2.c:reg" in exp["used_vars"] and "m2.d:1" in exp["used_vars"]


def test_wl_vectors_multi_ctrl_golden(wl_wb, wl_resolver):
    """组4（多控制拼接，黄金样本）：5'b10010 拆成 lut_en=1 / bwctrl=2 分别驱动。"""
    g4 = _grp(wl_wb, "d_wl_rf_tx_rc_code")
    exp = mux_gen.expand_mux_group(wl_wb, wl_resolver, g4)
    vecs, meta = mux_gen.make_mux_vectors(g4, exp, mode="min")
    assert len(vecs) == 5                                # 5 个 case（x 位取 0）
    assert meta["multi_ctrl"]
    assert meta["ctrl_sources"] == ["reg", "mux"]
    # T0: case 5'b0xxxx（x 取 0）→ lut_en=0，bwctrl=0 → 选 local
    t0 = vecs[0]
    assert t0.assignments["c:reg"] == 0                  # B 列控制 lut_en（高位段）
    assert t0.assignments["m2.d:1"] == 0                 # C 列控制 bwctrl（经上游 mux）
    assert t0.exp_value == 0xA                           # local 的互异值
    # T3: case 5'b10010（黄金样本）→ lut_en=1，bwctrl=2 → 选 lut2
    t3 = vecs[3]
    assert t3.assignments["c:reg"] == 1
    assert t3.assignments["m2.d:1"] == 2
    assert t3.exp_value == 0x7                           # lut2 的互异值（0xA,0x9,0x8,0x7,0x6 的第4个）


def test_wl_vectors_logic_ctrl_lpbt_path(wl_wb, wl_resolver):
    """组5（logic 行控制）：走 LPBT line/local 双路径生成器（向量结构与 LPBT 相同）。"""
    g5 = _grp(wl_wb, "d_wl_rf_dpd_path")
    exp = mux_gen.expand_mux_group(wl_wb, wl_resolver, g5)
    vecs, meta = mux_gen.make_mux_vectors(g5, exp, mode="min")
    # LPBT 形态：2 case line 扫 + 1 条 local 抽测 = 3
    assert len(vecs) == 3
    assert meta["scan_path"] == "line"
    assert meta["other_path_scan"] == "probe"


def test_wl_coverage_levels(wl_wb, wl_resolver):
    """通用形态覆盖度：精简 < 全面（+x位展开+反码轮）；穷举=全面（无另一条路径概念）。"""
    g4 = _grp(wl_wb, "d_wl_rf_tx_rc_code")
    exp = mux_gen.expand_mux_group(wl_wb, wl_resolver, g4)
    n = {m: len(mux_gen.make_mux_vectors(g4, exp, mode=m)[0])
         for m in ("min", "max", "exhaustive")}
    # min=5（每 case 1 条）；max=20（case0 的 4 个 x 位展开成 16）+5（反码轮）=25
    assert n["min"] == 5
    assert n["max"] == 16 + 4 + 5
    assert n["exhaustive"] == n["max"]
    # 反码轮的期望值 = 反码互异值
    vecs_max, _ = mux_gen.make_mux_vectors(g4, exp, mode="max")
    inv = [v for v in vecs_max if "inverted data" in (v.note or "")]
    assert len(inv) == 5
    base_vals, _ = mux_gen.alloc_distinct_values(g4, exp["bindings"], exp["data_keys"])
    inv_vals, _ = mux_gen.alloc_inverted_values(g4, base_vals, exp["bindings"], exp["data_keys"])
    assert [v.exp_value for v in inv] == inv_vals


def test_wl_key_role():
    """键角色判断（generator/gui/report 共用）。"""
    assert mux_gen.key_role("c:A") == "ctrl"
    assert mux_gen.key_role("c:reg") == "ctrl"
    assert mux_gen.key_role("c1:reg") == "ctrl"
    assert mux_gen.key_role("c2:A") == "ctrl"
    assert mux_gen.key_role("d:0") == "data"
    assert mux_gen.key_role("m2.c:reg") == "upstream"
    assert mux_gen.key_role("m57.d:1") == "upstream"


# ───────────── ⑥ build/report 端到端 ─────────────
WL_PREFIX = "U_WL_DREG.U_RF_MUX"
WL_PREFIXES = {
    "d_wl_rf_lna_gain": WL_PREFIX, "d_wl_rf_bwctrl": WL_PREFIX,
    "d_wl_rf_tx_bwctrl": WL_PREFIX, "d_wl_rf_tx_rc_code": WL_PREFIX,
    "d_wl_rf_dpd_path": WL_PREFIX,
    # mux→logic 级联衔接网（logic 行 lna_gain_dly 的输入）
    "d_wl_rf_lna_gain_to_logic": WL_PREFIX,
}


def test_wl_build_generates_without_prefix_with_warning(wl_wb):
    """没配探针前缀：top_out=0 的 mux 组【照常生成】裸名探针 + 警告（2026-06-03 用户拍板：
    工具不替用户假设 top_out=0=埋深，探得到就过、真 CUVUNF 再配前缀）。"""
    res = generator.build(wl_wb, generator.GenOptions())
    # 5 个组全部生成（不再因 top_out=0 跳过）
    assert res["summary"]["n_mux_generated"] == 5
    assert res["summary"]["n_mux_warnings"] == 5
    # 警告文案提示 scan_rtl（裸名探针，CUVUNF 时配前缀）
    assert all("scan_rtl" in why for _n, _a, why in res["mux_warnings"])
    # .sv 里是裸名探针（无前缀），块顶有 ⚠ 警告注释
    text = "\n".join("\n".join(lines) for lines, _ in res["blocks"])
    assert "assert_mux1_T0:" in text
    assert "`ENV_RF.d_wl_rf_lna_gain[2:0]==" in text       # 裸名，无层级前缀
    assert "// ⚠" in text                                   # 块顶警告注释


def test_wl_build_prefix_silences_warning(wl_wb):
    """配了探针前缀的组：无警告（探针带前缀）。"""
    res = generator.build(wl_wb, generator.GenOptions(probe_prefixes=WL_PREFIXES))
    assert res["summary"]["n_mux_generated"] == 5
    assert res["summary"]["n_mux_warnings"] == 0


def test_wl_build_with_prefixes(wl_wb):
    """配置探针前缀后全部 5 个组生成；.sv 内容覆盖三来源驱动 + 前缀探针。"""
    opts = generator.GenOptions(probe_prefixes=WL_PREFIXES)
    res = generator.build(wl_wb, opts)
    assert res["summary"]["n_mux_groups"] == 5
    assert res["summary"]["n_mux_generated"] == 5, res["skipped"]
    text = "\n".join("\n".join(lines) for lines, _ in res["blocks"])
    # ① 寄存器直出控制：RF_WRITE 模式寄存器（h50）
    assert "`RF_WRITE(10'h50," in text
    # ② RO 线控数据：force（无前缀——线控是顶层 RO 寄存器）
    assert "force `ENV_RF.d_wl_rf_linectrl_lna_gain[2:0]=" in text
    # ③ 输出探针带层级前缀（top_out=0）
    assert "`ENV_RF.%s.d_wl_rf_lna_gain[2:0]==" % WL_PREFIX in text
    assert "`ENV_RF.%s.d_wl_rf_tx_bwctrl[4:0]==" % WL_PREFIX in text
    # ④ mux 级联：上游载体寄存器（bwctrl_local h53）被 RF_WRITE
    assert "`RF_WRITE(10'h53," in text
    # ⑤ assert 标签
    assert "assert_mux1_T0:" in text and "assert_mux4_T0:" in text


def test_wl_build_include_risky_generates_without_prefix(wl_wb):
    """--include-risky：没前缀也强制生成（探针不带前缀，用户自担 CUVUNF 风险）。"""
    res = generator.build(wl_wb, generator.GenOptions(include_risky=True))
    assert res["summary"]["n_mux_generated"] == 5


def test_wl_report_sync_with_build(wl_wb):
    """report 与 build 双轨同步：同口径生成；多控制键正确分类。"""
    # 无前缀：top_out=0 组照常生成（error 列空），警告进 warning 列
    rep0 = generator.report(wl_wb, generator.GenOptions())
    mux_rows0 = [r for r in rep0["summary"] if r["type"] == "mux"]
    assert len(mux_rows0) == 5
    assert all(r["error"] == "" for r in mux_rows0)                 # 不再跳过
    assert all("scan_rtl" in r.get("warning", "") for r in mux_rows0)  # 警告提示 scan_rtl
    assert all(r["n_tests"] > 0 for r in mux_rows0)                 # 真生成了向量
    # 有前缀：全部生成，tables 有 5 个 mux 表
    opts = generator.GenOptions(probe_prefixes=WL_PREFIXES)
    rep = generator.report(wl_wb, opts)
    mux_rows = [r for r in rep["summary"] if r["type"] == "mux"]
    assert all(r["error"] == "" for r in mux_rows), [r["error"] for r in mux_rows]
    mux_tables = [t for t in rep["tables"] if t.get("kind") == "mux"]
    assert len(mux_tables) == 5
    # 组3 的输入行包含上游配方键（标记为"上游mux"角色）
    g3_table = next(t for t in mux_tables if "tx_bwctrl" in t["signal"])
    letters = [r["letters"] for r in g3_table["inputs"]]
    assert any("上游mux" in s for s in letters), letters
    # build 与 report 的生成组数一致
    res = generator.build(wl_wb, opts)
    assert res["summary"]["n_mux_generated"] == len([r for r in mux_rows if not r["error"]])


def test_wl_analyze_mux_group_status(wl_wb, wl_resolver):
    """analyze_mux_group（GUI 状态源）：top_out=0 无前缀 → bare-probe【非阻断】（照常生成裸名）；
    有前缀 → clean。"""
    g1 = _grp(wl_wb, "d_wl_rf_lna_gain")
    a0 = generator.analyze_mux_group(wl_resolver, wl_wb, g1)
    assert a0["status"] == "bare-probe"
    assert "scan_rtl" in a0["error"]
    assert a0["blocking"] is False                  # 软提示，不挡生成（真值表照常）
    a1 = generator.analyze_mux_group(wl_resolver, wl_wb, g1, probe_prefix=WL_PREFIX)
    assert a1["status"] == "clean"


def test_wl_analyze_force_net_blocker_is_blocking(tmp_path):
    """对照：force 子模块衔接网缺前缀（force 级联模式）→ needs-prefix 且 blocking=True（真阻断）。"""
    wb = _build_mux_wb(
        str(tmp_path / "blk.xlsx"),
        tmm_fields=[("D0", "h11", "d_d0[2:0]", "2:0", "Y", "RO"),
                    ("D1", "h12", "d_d1[2:0]", "2:0", "N", "RW")],
        mux_rows=[
            # 控制是别的 mux 的输出，force 级联模式 → force 衔接网需前缀
            _mrow("d_d0_to_mux[2:0]", "d_up_mux_to_mux", "1'b0", "d_gg[2:0]", 1),
            _mrow("d_d1_to_mux[2:0]", "d_up_mux_to_mux", "1'b1", "d_gg[2:0]", 1),
            _mrow("d_x0_to_mux[2:0]", "d_sel_to_mux", "1'b0", "d_up_mux[2:0]", 2),
            _mrow("d_x1_to_mux[2:0]", "d_sel_to_mux", "1'b1", "d_up_mux[2:0]", 2),
        ],
    )
    r = resolver.Resolver(wb, cascade_mode="force")
    a = generator.analyze_mux_group(r, wb, _grp(wb, "d_gg"))
    # 控制衔接网 d_up_mux_to_mux 在子模块内，force 模式没前缀 → 真阻断
    assert a["status"] in ("needs-prefix", "unresolved")
    if a["status"] == "needs-prefix":
        assert a["blocking"] is True


def test_narrow_field_mux_uses_marker_method(tmp_path):
    """字段太窄(3 个 1-bit 数据，互异值装不下)：不再判假绿跳过，改『点名法』生成——
    每 case 一条：被测数据寄存器=标记(非0)、其余=0，期望=标记 → routing 全验，任何位宽都成立。"""
    wb = _build_mux_wb(
        str(tmp_path / "fg.xlsx"),
        tmm_fields=[
            ("CTRL", "h10", "d_ctrl[1:0]", "1:0", "N", "RW"),
            ("N0", "h20", "d_n0", "0", "N", "RW"),     # 三个 1-bit 数据字段：3 个互异值装不下
            ("N1", "h21", "d_n1", "0", "N", "RW"),
            ("N2", "h22", "d_n2", "0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_n0_to_mux", "d_ctrl_to_mux[1:0]", "2'b00", "d_fgout", 1),
            _mrow("d_n1_to_mux", "d_ctrl_to_mux[1:0]", "2'b01", "d_fgout", 1),
            _mrow("d_n2_to_mux", "d_ctrl_to_mux[1:0]", "2'b10", "d_fgout", 1),
        ],
    )
    r = resolver.Resolver(wb)
    grp = _grp(wb, "d_fgout")
    exp = mux_gen.expand_mux_group(wb, r, grp)
    vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode="min")
    assert meta["marker_method"] is True                 # 切到点名法
    assert meta["value_collision"] is False              # 不再算假绿
    assert len(vecs) == 3                                 # 每 case 一条
    dks = exp["data_keys"]
    for ci, v in enumerate(vecs):                         # 被测=标记非0，其余=0，期望=标记
        assert v.exp_value != 0
        for j, dk in enumerate(dks):
            assert (v.assignments[dk] != 0) == (j == ci)
    # 不再 false-green：analyze 能生成（输出 top_out=0 → 裸名探针/或需前缀）
    a = generator.analyze_mux_group(r, wb, grp)
    assert a["status"] in ("clean", "needs-prefix", "bare-probe"), (a["status"], a.get("error"))


def test_wl_negative_vectors(wl_wb):
    """负向用例（反例）对通用形态向量同样可用。"""
    opts = generator.GenOptions(probe_prefixes=WL_PREFIXES, neg_all=True)
    res = generator.build(wl_wb, opts)
    assert res["summary"]["n_mux_generated"] == 5
    text = "\n".join("\n".join(lines) for lines, _ in res["blocks"])
    assert "_NEG:" in text                               # 负向 assert 标签


# ───────────── ⑦ 对抗式审查确认的 5 个缺陷的回归（2026-06-03 第十四轮 review）─────────────
from fixtures import _set_row, _tmm_reg, _tmm_field  # noqa: E402


def _build_mux_wb(path, tmm_fields, mux_rows, regmap_fields=None, logic_rows=None):
    """造一个只有 logic(默认空)/tmm/regmap/mux 四页的最小工作簿，给级联/边界回归用。

    tmm_fields: [(reg_name, addr, field_name(可带[msb:lsb]), bit, dig, typ), ...]
    mux_rows:   [{列字母: 值}, ...]（直接 _set_row 到 mux 页数据行，row 从 3 起）
    regmap_fields: [(reg, typ, signal), ...] 可选
    logic_rows: [{列字母: 值}, ...] 可选——往 logic 页加行（A..J=输入, K=输出, L=表达式,
                M=suffix, N=top_output, R=序号），用于复现"mux 数据/控制输入是某 logic 输出"的形态。
    """
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "logic"
    _set_row(ws, 2, {"A": "in_A", "K": "out", "L": "expr", "M": "suffix",
                     "N": "top", "P": "owner", "R": "no"})
    for ri, cells in enumerate(logic_rows or [], start=3):
        _set_row(ws, ri, cells)
    rm = wb.create_sheet("regmap")
    _set_row(rm, 2, {"D": "Reg", "F": "Type", "G": "Signal", "J": "b15", "Y": "b0", "AE": "owner"})
    from openpyxl.utils import column_index_from_string
    for ri, (reg, typ, signal) in enumerate(regmap_fields or [], start=3):
        _set_row(rm, ri, {"D": reg, "F": typ, "G": signal})
        rm.cell(row=ri, column=column_index_from_string("Y"), value=1)
    tmm = wb.create_sheet("total_memory_map")
    r = 1
    for reg_name, addr, fname, bit, dig, typ in tmm_fields:
        r = _tmm_reg(tmm, r, reg_name, addr)
        r = _tmm_field(tmm, r, fname, bit, addr=addr, dig=dig, typ=typ)
    mx = wb.create_sheet("mux")
    _set_row(mx, 2, {"A": "mux_input", "B": "c1", "C": "c2", "D": "c3", "E": "c4",
                     "F": "case", "G": "mux_out", "H": "to_logic", "I": "top", "L": "Owner", "N": 0})
    for ri, cells in enumerate(mux_rows, start=3):
        _set_row(mx, ri, cells)
    wb.save(path)
    return excel_model.load_workbook(path)


def _mrow(inp, ctrl_b, case, out, n, h="to_dft"):
    return {"A": inp, "B": ctrl_b, "F": case, "G": out, "H": h, "I": 0, "L": "O1", "N": n}


def test_review_fix1_narrow_rw_carrier_at_ci1_rejected(tmp_path):
    """[审查#1 critical] 上游载体在 ci>=1 的窄 RW 字段：_effective_width 必须按真实字段位宽算
    （此前传 ci 而非 0 → clamp 被跳过 → 窄字段误判够宽）。修后窄 RW 被拒，改选够宽的 RO 线。"""
    wb = _build_mux_wb(
        str(tmp_path / "c1.xlsx"),
        tmm_fields=[
            ("UCTRL", "h10", "d_uctrl", "0", "N", "RW"),
            ("ULINE", "h11", "d_uline[3:0]", "3:0", "Y", "RO"),   # 4bit RO 线（够宽载体）
            ("ULOCAL", "h12", "d_ulocal[1:0]", "1:0", "N", "RW"),  # 字段仅 2bit（窄 RW）
            ("DD0", "h20", "d_dd0[3:0]", "3:0", "N", "RW"),
            ("DD1", "h21", "d_dd1[3:0]", "3:0", "N", "RW"),
        ],
        mux_rows=[
            # 上游组1：out d_umux[3:0]，控制 d_uctrl；ci0=line(RO)，ci1=local(窄 RW，声明 4bit)
            _mrow("d_uline_to_mux[3:0]", "d_uctrl_to_mux", "1'b0", "d_umux[3:0]", 1),
            _mrow("d_ulocal_to_mux[3:0]", "d_uctrl_to_mux", "1'b1", "d_umux[3:0]", 1),
            # 下游组2：控制 = d_umux（级联），case 需要 4bit 值
            _mrow("d_dd0_to_mux[3:0]", "d_umux_to_mux[3:0]", "4'b1010", "d_dmux[3:0]", 2),
            _mrow("d_dd1_to_mux[3:0]", "d_umux_to_mux[3:0]", "4'b0101", "d_dmux[3:0]", 2),
        ],
    )
    r = resolver.Resolver(wb)
    g_dn = _grp(wb, "d_dmux")
    exp = mux_gen.expand_mux_group(wb, r, g_dn)
    recipe = exp["ctrl_drivers"][0]["recipe"]
    # 窄 RW(ci=1, 字段2bit) 被正确拒绝（eff=2<out_width=4），改选够宽的 RO 线(ci=0)
    assert recipe["carrier_ci"] == 0
    assert recipe["carrier_key"] == "m1.d:0"
    assert recipe["carrier_eff_width"] == 4


def test_review_fix1_narrow_only_carrier_skips_group(tmp_path):
    """[审查#1] 唯一载体是窄 RW（修前会被误判够宽并产假向量）→ 修后整组无载体 → 跳过给原因。"""
    wb = _build_mux_wb(
        str(tmp_path / "c1b.xlsx"),
        tmm_fields=[
            ("UCTRL", "h10", "d_uctrl", "0", "N", "RW"),
            ("UL0", "h11", "d_ul0[1:0]", "1:0", "N", "RW"),       # 窄
            ("ULOCAL", "h12", "d_ulocal[1:0]", "1:0", "N", "RW"),  # 窄
            ("DD0", "h20", "d_dd0[3:0]", "3:0", "N", "RW"),
            ("DD1", "h21", "d_dd1[3:0]", "3:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_ul0_to_mux[3:0]", "d_uctrl_to_mux", "1'b0", "d_umux[3:0]", 1),
            _mrow("d_ulocal_to_mux[3:0]", "d_uctrl_to_mux", "1'b1", "d_umux[3:0]", 1),
            _mrow("d_dd0_to_mux[3:0]", "d_umux_to_mux[3:0]", "4'b1010", "d_dmux[3:0]", 2),
            _mrow("d_dd1_to_mux[3:0]", "d_umux_to_mux[3:0]", "4'b0101", "d_dmux[3:0]", 2),
        ],
    )
    r = resolver.Resolver(wb)
    exp = mux_gen.expand_mux_group(wb, r, _grp(wb, "d_dmux"))
    # 两个窄载体都 eff=2<4 → 没有可用载体 → issue
    assert any("载体" in i for i in exp["issues"]), exp["issues"]


def test_review_fix2_wire_fallback_data_skipped(tmp_path):
    """[审查#2 critical] mux 数据输入 wire 兜底（表里查无）→ 必须进 prefix_risks → 默认跳过，
    不能静默生成 force 裸名（RTL 顶层不存在的网 → elaboration CUVUNF）。"""
    wb = _build_mux_wb(
        str(tmp_path / "c2.xlsx"),
        tmm_fields=[
            ("CMODE", "h10", "d_cmode", "0", "N", "RW"),
            ("DLOCAL", "h12", "d_dlocal[2:0]", "2:0", "N", "RW"),
        ],
        mux_rows=[
            # 数据 ci0 = 表里查无的衔接网（既不在 tmm/regmap、也不是 logic/mux 输出）→ wire 兜底
            _mrow("d_mystery_internal_to_mux[2:0]", "d_cmode_to_mux", "1'b0", "d_gout[2:0]", 1),
            _mrow("d_dlocal_to_mux[2:0]", "d_cmode_to_mux", "1'b1", "d_gout[2:0]", 1),
        ],
    )
    r = resolver.Resolver(wb)
    grp = _grp(wb, "d_gout")
    exp = mux_gen.expand_mux_group(wb, r, grp)
    b = exp["bindings"]["d:0"]
    assert b.found_in == "wire"           # 确认走了 wire 兜底
    # 即便配了输出探针前缀（隔离输出风险），wire 兜底输入仍要进 risks
    opts = generator.GenOptions(probe_prefixes={"d_gout": "U_X"})
    risks = generator.mux_prefix_risks(grp, exp, opts)
    assert any("wire" in why or "CUVUNF" in why for _t, _n, why in risks), risks
    # build 默认跳过（不生成 force 裸名）
    res = generator.build(wb, opts)
    assert res["summary"]["n_mux_generated"] == 0
    text = "\n".join("\n".join(lines) for lines, _ in res["blocks"])
    assert "d_mystery_internal" not in text


def test_review_fix3_carrier_wider_than_output_clamped(tmp_path):
    """[审查#3 major] 载体寄存器比上游输出宽：控制值按上游【输出】位宽截断，
    越界 case 的向量被丢弃（此前用载体位宽 → 高位漏过 → 假绿）。"""
    wb = _build_mux_wb(
        str(tmp_path / "c3.xlsx"),
        tmm_fields=[
            ("UCTRL", "h10", "d_uctrl", "0", "N", "RW"),
            ("ULINE", "h11", "d_uline[3:0]", "3:0", "Y", "RO"),
            ("ULOCAL", "h12", "d_ulocal[5:0]", "5:0", "N", "RW"),   # 载体 6bit（宽于输出 4bit）
            ("DD0", "h20", "d_dd0[3:0]", "3:0", "N", "RW"),
            ("DD1", "h21", "d_dd1[3:0]", "3:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_uline_to_mux[3:0]", "d_uctrl_to_mux", "1'b0", "d_umux[3:0]", 1),
            _mrow("d_ulocal_to_mux[5:0]", "d_uctrl_to_mux", "1'b1", "d_umux[3:0]", 1),
            # 下游控制声明 6bit、case 需要超过上游输出(4bit)的高位 → 物理上达不到 → 丢弃
            _mrow("d_dd0_to_mux[3:0]", "d_umux_to_mux[5:0]", "6'b110000", "d_dmux[3:0]", 2),
            _mrow("d_dd1_to_mux[3:0]", "d_umux_to_mux[5:0]", "6'b000001", "d_dmux[3:0]", 2),
        ],
    )
    r = resolver.Resolver(wb)
    exp = mux_gen.expand_mux_group(wb, r, _grp(wb, "d_dmux"))
    vecs, meta = mux_gen.make_mux_vectors(_grp(wb, "d_dmux"), exp, mode="min")
    # case 6'b110000(=48) 需要上游输出 48，但上游只 4bit → 48&0xF=0 ≠ 48 → 该向量丢弃
    assert meta["dropped"] >= 1
    assert all("6'b110000" not in (v.note or "") for v in vecs)


def test_review_fix4_upstream_ctrl_truncated_dropped(tmp_path):
    """[审查#4 major] 载体 case 需要的上游控制值超出上游控制寄存器实际位宽 →
    上游被截断选错 case → 该向量必须丢弃（此前不回验上游是否真命中载体 case）。"""
    wb = _build_mux_wb(
        str(tmp_path / "c4.xlsx"),
        tmm_fields=[
            ("UCTRL", "h10", "d_uctrl", "0", "N", "RW"),          # 控制字段仅 1bit
            ("UD0", "h11", "d_ud0[3:0]", "3:0", "Y", "RO"),
            ("UD1", "h12", "d_ud1[3:0]", "3:0", "Y", "RO"),
            ("UWIDE", "h13", "d_uwide[3:0]", "3:0", "N", "RW"),    # 够宽 RW 载体，但在 case 2'b10
            ("DD0", "h20", "d_dd0[3:0]", "3:0", "N", "RW"),
        ],
        mux_rows=[
            # 上游控制 d_uctrl 声明 2bit（cases 2bit），但 tmm 字段仅 1bit
            _mrow("d_ud0_to_mux[3:0]", "d_uctrl_to_mux[1:0]", "2'b00", "d_umux[3:0]", 1),
            _mrow("d_ud1_to_mux[3:0]", "d_uctrl_to_mux[1:0]", "2'b01", "d_umux[3:0]", 1),
            _mrow("d_uwide_to_mux[3:0]", "d_uctrl_to_mux[1:0]", "2'b10", "d_umux[3:0]", 1),
            # 下游：控制 = d_umux，挑一个 case
            _mrow("d_dd0_to_mux[3:0]", "d_umux_to_mux[3:0]", "4'b1010", "d_dmux[3:0]", 2),
        ],
    )
    r = resolver.Resolver(wb)
    g_dn = _grp(wb, "d_dmux")
    exp = mux_gen.expand_mux_group(wb, r, g_dn)
    recipe = exp["ctrl_drivers"][0]["recipe"]
    # 载体 = case 2'b10 的 d_uwide（RW 优先），需要上游控制驱到 2，但 1bit 字段截断 2→0
    assert recipe["carrier_ci"] == 2 and recipe["ctrl_values"] == [2]
    vecs, meta = mux_gen.make_mux_vectors(g_dn, exp, mode="min")
    # 上游控制截断后选不中载体 case → 向量被丢弃（不静默生成假断言）
    assert meta["dropped"] >= 1
    assert any("截断" in rr for rr in meta.get("dropped_reasons", []))


def test_review_fix5_blank_continuation_rows_not_mismatch(tmp_path):
    """[审查#5 major] 续行控制列留空（合并单元格 / 控制只写组首行）→ 视为继承首行，
    不误判 mismatch（此前整组被跳过 + 误导『请核对 Excel』，是 LPBT 排版的回归）。"""
    wb = _build_mux_wb(
        str(tmp_path / "c5.xlsx"),
        tmm_fields=[
            ("CMODE", "h10", "d_cmode", "0", "N", "RW"),
            ("D0", "h11", "d_d0[2:0]", "2:0", "Y", "RO"),
            ("D1", "h12", "d_d1[2:0]", "2:0", "N", "RW"),
        ],
        mux_rows=[
            # 首行写控制；续行 B 列留空（模拟合并单元格/只写首行）
            _mrow("d_d0_to_mux[2:0]", "d_cmode_to_mux", "1'b0", "d_gg[2:0]", 1),
            {"A": "d_d1_to_mux[2:0]", "F": "1'b1", "G": "d_gg[2:0]", "H": "to_dft",
             "I": 0, "L": "O1", "N": 1},        # B 留空
        ],
    )
    grp = _grp(wb, "d_gg")
    assert grp.ctrl_mismatch_rows == []        # 续行空控制不算 mismatch
    assert len(grp.ctrls) == 1 and grp.ctrl_base == "d_cmode"
    r = resolver.Resolver(wb)
    exp = mux_gen.expand_mux_group(wb, r, grp)
    assert not any("不一致" in i for i in exp["issues"]), exp["issues"]
    # 配前缀后照常生成（不被误跳过）
    res = generator.build(wb, generator.GenOptions(probe_prefixes={"d_gg": "U_X"}))
    assert res["summary"]["n_mux_generated"] == 1


def test_review_fix5_genuine_mismatch_still_caught(tmp_path):
    """[审查#5] 续行写了【不同的非空】控制信号 → 仍要抓（不能把真错误也放过）。"""
    wb = _build_mux_wb(
        str(tmp_path / "c5b.xlsx"),
        tmm_fields=[
            ("CMODE", "h10", "d_cmode", "0", "N", "RW"),
            ("CMODE2", "h13", "d_other", "0", "N", "RW"),
            ("D0", "h11", "d_d0[2:0]", "2:0", "Y", "RO"),
            ("D1", "h12", "d_d1[2:0]", "2:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_d0_to_mux[2:0]", "d_cmode_to_mux", "1'b0", "d_gg[2:0]", 1),
            _mrow("d_d1_to_mux[2:0]", "d_other_to_mux", "1'b1", "d_gg[2:0]", 1),  # 控制变了
        ],
    )
    grp = _grp(wb, "d_gg")
    assert grp.ctrl_mismatch_rows == [4]       # 续行控制不同 → 仍记 mismatch
    r = resolver.Resolver(wb)
    exp = mux_gen.expand_mux_group(wb, r, grp)
    assert any("不一致" in i for i in exp["issues"])


# ───────────── ⑧ mux 数据输入是 top_output=0 logic 输出的 _to_mux 衔接网（2026-06-04 WL linectrl/_line）─────────────
# 真表 270 组里 ~41 个"数据输入未解析"的根因：mux 页 A 列写的是显式 X_to_mux 真网，但基名 X 撞上一个
# top_output=0 的 logic 输出 → resolve 在"内部信号"兜底短路（cone 还会假成环）→ 未解析跳过。
# 修复：原文带 _to_mux 时不走内部信号兜底，落到 force 字面网（needs-prefix）。


def _mux_skip_reason(wb, res, out_base):
    """从 compose_account 取某 mux 组的去向+原因（= 用户跑 --account 看到的那一行）。"""
    items = generator.compose_account(wb, generator.GenOptions(), res)
    it = next(i for i in items if i["kind"] == "mux" and i["name"].startswith(out_base))
    return it["disposition"], it["reason"]


def test_wl_linectrl_collision_data_input_forces_net(tmp_path):
    """linectrl 家族（名字撞车 + 纯透传）：mux 数据输入 X_to_mux，X 既是 top_output=0 logic 输出、
    又与一个 RO 寄存器同名。修前→resolve 在"内部信号"兜底短路→未解析跳过；修后→force 字面网。"""
    wb = _build_mux_wb(
        str(tmp_path / "linectrl.xlsx"),
        tmm_fields=[
            ("MODE", "h10", "d_wl_rf_mode", "0", "N", "RW"),
            ("BAND", "h11", "d_wl_rf_band_sel[5:0]", "5:0", "Y", "RO"),   # 底层 RO（与 logic 输出同名）
            ("LOCAL", "h12", "d_wl_rf_local[3:0]", "3:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_wl_rf_band_sel_to_mux[3:0]", "d_wl_rf_mode_to_mux", "1'b0", "d_wl_rf_out[3:0]", 1),
            _mrow("d_wl_rf_local_to_mux[3:0]", "d_wl_rf_mode_to_mux", "1'b1", "d_wl_rf_out[3:0]", 1),
        ],
        logic_rows=[
            # 纯透传 + 名字撞车：logic 输出 d_wl_rf_band_sel[3:0] = A（同名 RO 寄存器的低 4 位）
            {"A": "d_wl_rf_band_sel_to_logic[3:0]", "K": "d_wl_rf_band_sel[3:0]",
             "L": "A", "M": "to_mux", "N": 0, "R": 1},
        ],
    )
    r = resolver.Resolver(wb)
    exp = mux_gen.expand_mux_group(wb, r, _grp(wb, "d_wl_rf_out"))
    b = exp["bindings"]["d:0"]                              # 线控数据输入
    assert b.found_in == "needs-prefix", (b.found_in, b.note)
    assert b.kind == "RO"
    assert b.resolved is True                              # ⭐ 不再"未解析"
    assert b.wire == "d_wl_rf_band_sel_to_mux"            # force 字面衔接网
    assert not any("未解析" in i for i in exp["issues"]), exp["issues"]
    # --account 视角：没前缀 → 跳过原因是"缺前缀"(丙类)，不是"未解析"(丁类)
    res0 = generator.build(wb, generator.GenOptions())
    disp, reason = _mux_skip_reason(wb, res0, "d_wl_rf_out")
    assert disp == "跳过"
    assert "前缀" in reason and "未解析" not in reason, reason
    # 配前缀 → 整组生成
    res1 = generator.build(wb, generator.GenOptions(probe_prefixes={
        "d_wl_rf_out": "U_X.U_MUX", "d_wl_rf_band_sel_to_mux": "U_X.U_SIGLOGIC"}))
    assert res1["summary"]["n_mux_generated"] == 1


def test_wl_line_family_data_input_forces_net(tmp_path):
    """_line 家族（无名字撞车 + 门控三元 B?A:0）：mux 数据输入 X_to_mux，X 是 top_output=0 logic
    输出（与寄存器不同名）。同样应 force 字面 _to_mux 网（needs-prefix），不判未解析。"""
    wb = _build_mux_wb(
        str(tmp_path / "line.xlsx"),
        tmm_fields=[
            ("MODE", "h10", "d_wl_rf_tx_en_mode", "0", "N", "RW"),
            ("TXLINE", "h11", "d_wl_rf_linectrl_tx_en", "0", "Y", "RO"),    # _line 底层 RO 线控寄存器
            ("FREQ", "h12", "d_wl_rf_freq_5g_sel_reg", "0", "N", "RW"),     # 门控 B（简化为寄存器）
            ("LOCAL", "h13", "d_wl_rf_tx_en_local", "0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_wl_rf_tx_en_line_to_mux", "d_wl_rf_tx_en_mode_to_mux", "1'b0", "d_wl_rf_tx_en", 1),
            _mrow("d_wl_rf_tx_en_local_to_mux", "d_wl_rf_tx_en_mode_to_mux", "1'b1", "d_wl_rf_tx_en", 1),
        ],
        logic_rows=[
            # _line 形态：d_wl_rf_tx_en_line = B?A:1'b0（A=RO 线控, B=频选），top_output=0
            {"A": "d_wl_rf_linectrl_tx_en_to_logic", "B": "d_wl_rf_freq_5g_sel_reg_to_logic",
             "K": "d_wl_rf_tx_en_line", "L": "B?A:1'b0", "M": "to_mux", "N": 0, "R": 1},
        ],
    )
    r = resolver.Resolver(wb)
    exp = mux_gen.expand_mux_group(wb, r, _grp(wb, "d_wl_rf_tx_en"))
    b = exp["bindings"]["d:0"]
    assert b.found_in == "needs-prefix", (b.found_in, b.note)
    assert b.kind == "RO" and b.resolved is True
    assert b.wire == "d_wl_rf_tx_en_line_to_mux"
    assert not any("未解析" in i for i in exp["issues"]), exp["issues"]


def test_internal_to_logic_input_still_cone_expandable(tmp_path):
    """守护：修复只对 _to_mux 生效。logic 页输入引用 top_output=0 内部信号（_to_logic 后缀）时
    仍标 logic-internal（cone 展开路径不受影响）——logic 页输入从不带 _to_mux 后缀。"""
    wb = _build_mux_wb(
        str(tmp_path / "cone.xlsx"),
        tmm_fields=[("R0", "h10", "d_leaf_reg", "0", "N", "RW")],
        mux_rows=[  # 给个最小 mux 组占位（_build_mux_wb 需要 mux 页）
            _mrow("d_leaf_reg_to_mux", "d_leaf_reg_to_mux", "1'b0", "d_dummy", 9)],
        logic_rows=[
            # 内部信号 d_inner（top_output=0）；下游 d_top 以 d_inner_to_logic 引用它
            {"A": "d_leaf_reg_to_logic", "K": "d_inner", "L": "A", "M": "to_logic", "N": 0, "R": 1},
            {"A": "d_inner_to_logic", "K": "d_top", "L": "A", "M": "ls", "N": 1, "R": 2},
        ],
    )
    r = resolver.Resolver(wb)
    sig = next(s for s in wb.logic if s.out_base == "d_top")
    b = r.resolve_signal_inputs(sig)["A"]
    # _to_logic 内部信号 → 仍走 cone 可展开标记（未被 _to_mux 例外改变）
    assert b.found_in in ("logic-internal", "logic-computed"), (b.found_in, b.note)


def test_wl_control_internal_logic_output_forces_net(tmp_path):
    """控制侧·纯 OR 族（fb_en=A|B，输入全是 top_output=0 内部信号）：控制本身是内部 logic 输出，
    经显式 _to_mux 网喂 mux。无透传路径 → 改 force 字面 _to_mux 网（needs-prefix），不判未解析。"""
    wb = _build_mux_wb(
        str(tmp_path / "ctrl_or.xlsx"),
        tmm_fields=[
            ("LEAF", "h10", "d_leaf", "0", "N", "RW"),
            ("D0", "h20", "d_data0[3:0]", "3:0", "N", "RW"),
            ("D1", "h21", "d_data1[3:0]", "3:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_data0_to_mux[3:0]", "d_fb_en_to_mux", "1'b0", "d_cout[3:0]", 1),
            _mrow("d_data1_to_mux[3:0]", "d_fb_en_to_mux", "1'b1", "d_cout[3:0]", 1),
        ],
        logic_rows=[
            # 两个内部 enable（top_output=0），作为控制表达式的输入
            {"A": "d_leaf_to_logic", "K": "d_en_a", "L": "A", "M": "to_logic", "N": 0, "R": 1},
            {"A": "d_leaf_to_logic", "K": "d_en_b", "L": "A", "M": "to_logic", "N": 0, "R": 2},
            # 控制 fb_en = A|B（两输入都是内部信号）→ discover_ctrl_paths 找不到 line/local
            {"A": "d_en_a_to_logic", "B": "d_en_b_to_logic", "K": "d_fb_en",
             "L": "A|B", "M": "to_mux", "N": 0, "R": 3},
        ],
    )
    r = resolver.Resolver(wb)
    exp = mux_gen.expand_mux_group(wb, r, _grp(wb, "d_cout"))
    d = exp["ctrl_drivers"][0]
    assert d["source"] == "mux-force", (d["source"], exp["issues"])
    b = d["binding"]
    assert b.found_in == "needs-prefix", (b.found_in, b.note)
    assert b.kind == "RO" and b.resolved is True
    assert b.wire == "d_fb_en_to_mux"                      # force 字面控制衔接网
    assert not any("未解析" in i for i in exp["issues"]), exp["issues"]
    # --account：没前缀 → "缺前缀"(丙类)，不是"未解析"(丁类)
    res0 = generator.build(wb, generator.GenOptions())
    disp, reason = _mux_skip_reason(wb, res0, "d_cout")
    assert disp == "跳过"
    assert "前缀" in reason and "未解析" not in reason, reason
    # 配控制网前缀 → 整组生成
    res1 = generator.build(wb, generator.GenOptions(probe_prefixes={
        "d_cout": "U_X.U_MUX", "d_fb_en_to_mux": "U_X.U_SIGLOGIC"}))
    assert res1["summary"]["n_mux_generated"] == 1


def test_wl_control_gated_logic_output_forces_net(tmp_path):
    """控制侧·门控族（tx_filter_en=A&(B|C)，三输入全内部）：与纯 OR 同理，门控表达式同样无透传
    路径 → force 控制 _to_mux 网。守护"四种表达式形态统一触发"中的 A&(B|C)。"""
    wb = _build_mux_wb(
        str(tmp_path / "ctrl_gate.xlsx"),
        tmm_fields=[
            ("LEAF", "h10", "d_leaf", "0", "N", "RW"),
            ("D0", "h20", "d_data0[3:0]", "3:0", "N", "RW"),
            ("D1", "h21", "d_data1[3:0]", "3:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_data0_to_mux[3:0]", "d_txf_en_to_mux", "1'b0", "d_cout2[3:0]", 1),
            _mrow("d_data1_to_mux[3:0]", "d_txf_en_to_mux", "1'b1", "d_cout2[3:0]", 1),
        ],
        logic_rows=[
            {"A": "d_leaf_to_logic", "K": "d_en_a", "L": "A", "M": "to_logic", "N": 0, "R": 1},
            {"A": "d_leaf_to_logic", "K": "d_en_b", "L": "A", "M": "to_logic", "N": 0, "R": 2},
            {"A": "d_leaf_to_logic", "K": "d_en_c", "L": "A", "M": "to_logic", "N": 0, "R": 3},
            {"A": "d_en_a_to_logic", "B": "d_en_b_to_logic", "C": "d_en_c_to_logic",
             "K": "d_txf_en", "L": "A&(B|C)", "M": "to_mux", "N": 0, "R": 4},
        ],
    )
    r = resolver.Resolver(wb)
    exp = mux_gen.expand_mux_group(wb, r, _grp(wb, "d_cout2"))
    d = exp["ctrl_drivers"][0]
    assert d["source"] == "mux-force", (d["source"], exp["issues"])
    assert d["binding"].wire == "d_txf_en_to_mux"
    assert d["binding"].found_in == "needs-prefix"
    assert not any("未解析" in i for i in exp["issues"]), exp["issues"]


def test_lpbt_logic_control_passthrough_not_hijacked(tmp_path):
    """守护：控制是 logic 输出但有干净 line(RO)/local(RW) 透传路径（LPBT 形态）时，仍走 logic
    级联（source='logic'），不被 force 兜底劫持——force 只在『无透传路径』时接管。"""
    wb = _build_mux_wb(
        str(tmp_path / "lpbt_ctrl.xlsx"),
        tmm_fields=[
            ("MODE", "h10", "d_mode", "0", "N", "RW"),
            ("LINE", "h11", "d_line", "0", "Y", "RO"),
            ("LOCAL", "h12", "d_local", "0", "N", "RW"),
            ("D0", "h20", "d_data0[3:0]", "3:0", "N", "RW"),
            ("D1", "h21", "d_data1[3:0]", "3:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_data0_to_mux[3:0]", "d_ctrl_to_mux", "1'b0", "d_cout3[3:0]", 1),
            _mrow("d_data1_to_mux[3:0]", "d_ctrl_to_mux", "1'b1", "d_cout3[3:0]", 1),
        ],
        logic_rows=[
            # 控制 d_ctrl = mode?local:line（line=RO 透传, local=RW 透传）→ 有 line/local 路径
            {"A": "d_line_to_logic", "B": "d_mode_to_logic", "C": "d_local_to_logic",
             "K": "d_ctrl", "L": "B?C:A", "M": "to_mux", "N": 0, "R": 1},
        ],
    )
    r = resolver.Resolver(wb)
    exp = mux_gen.expand_mux_group(wb, r, _grp(wb, "d_cout3"))
    d = exp["ctrl_drivers"][0]
    assert d["source"] == "logic", (d["source"], exp["issues"])   # 没被 force 劫持
    assert d["line"] is not None or d["local"] is not None


# ───────────── 同源喂多 case：互异值按物理寄存器分配（2026-06-04 回归） ─────────────
def test_wl_shared_data_source_across_cases_generates(tmp_path):
    """⭐回归（2026-06-04，真表 vga_cfb_dpd_ss_lut5 症状）：同一个数据寄存器喂多个 case
    （d:0/d:1/d:2 全是 *_t0）。旧版互异值【按 case 序号】分配 → 同一寄存器被写 3 个不同值 →
    emit 的 by_base 冲突检查把每条向量都丢弃 → 空向量 → 误报"控制信号没有可用的驱动路径"。
    修复：互异值【按物理寄存器】分配，同源 case 复用同值 → 正常生成、不再空。"""
    wb = _build_mux_wb(
        str(tmp_path / "shared.xlsx"),
        tmm_fields=[
            ("CTRL", "h10", "d_sel[1:0]", "1:0", "N", "RW"),      # 2bit 寄存器直出控制
            ("T0", "h20", "d_src_t0[2:0]", "2:0", "N", "RW"),
            ("T1", "h21", "d_src_t1[2:0]", "2:0", "N", "RW"),
        ],
        mux_rows=[
            # 3 个 case：00→t0, 01→t0(同源!), 10→t1
            _mrow("d_src_t0_to_mux[2:0]", "d_sel_to_mux[1:0]", "2'b00", "d_shared[2:0]", 1),
            _mrow("d_src_t0_to_mux[2:0]", "d_sel_to_mux[1:0]", "2'b01", "d_shared[2:0]", 1),
            _mrow("d_src_t1_to_mux[2:0]", "d_sel_to_mux[1:0]", "2'b10", "d_shared[2:0]", 1),
        ],
    )
    r = resolver.Resolver(wb)
    grp = _grp(wb, "d_shared")
    exp = mux_gen.expand_mux_group(wb, r, grp)
    assert not exp["issues"], exp["issues"]
    # 互异值：同源(t0)的两个 case 必须同值；t0 != t1（不同寄存器才需互异）
    values, collision = mux_gen.alloc_distinct_values(grp, exp["bindings"], exp["data_keys"])
    assert values[0] == values[1], "同一寄存器喂的两个 case 必须分配同值（否则 by_base 冲突全丢）"
    assert values[0] != values[2], "不同寄存器必须互异（mux 选路才能验）"
    assert not collision                                   # 2 个不同寄存器，3bit 装得下
    # 端到端：向量真的生成了（不再空、不再被 by_base 冲突全丢）
    vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode="min")
    assert vecs, "同源喂多 case 不该再产生空向量"
    assert meta["dropped"] == 0, meta.get("dropped_reasons")
    assert not meta.get("value_collision")
    # build 流程同样生成（不再落进 skipped）
    import copy
    wb2 = copy.copy(wb)
    wb2.mux = [grp]
    res = generator.build(wb2, generator.GenOptions(types=["mux"], include_risky=True))
    assert res["summary"]["n_mux_generated"] == 1


def test_wl_shared_source_narrow_marker_marks_whole_register(tmp_path):
    """同源喂多 case + 字段太窄（点名法路径）：被测寄存器喂的【所有】case 一起给标记，
    只把别的寄存器置 0——否则同源 case 一个标记一个 0 仍会撞 by_base 冲突。"""
    wb = _build_mux_wb(
        str(tmp_path / "shared_narrow.xlsx"),
        tmm_fields=[
            ("CTRL", "h10", "d_sel2[1:0]", "1:0", "N", "RW"),
            ("T0", "h20", "d_n_t0", "0", "N", "RW"),          # 1bit
            ("T1", "h21", "d_n_t1", "0", "N", "RW"),          # 1bit
            ("T2", "h22", "d_n_t2", "0", "N", "RW"),          # 1bit
        ],
        mux_rows=[
            _mrow("d_n_t0_to_mux", "d_sel2_to_mux[1:0]", "2'b00", "d_narrow", 1),
            _mrow("d_n_t0_to_mux", "d_sel2_to_mux[1:0]", "2'b01", "d_narrow", 1),  # 同源 t0
            _mrow("d_n_t1_to_mux", "d_sel2_to_mux[1:0]", "2'b10", "d_narrow", 1),
            _mrow("d_n_t2_to_mux", "d_sel2_to_mux[1:0]", "2'b11", "d_narrow", 1),
        ],
    )
    r = resolver.Resolver(wb)
    grp = _grp(wb, "d_narrow")
    exp = mux_gen.expand_mux_group(wb, r, grp)
    assert not exp["issues"], exp["issues"]
    # 3 个不同寄存器、各 1bit（只放得下值 1，避 0）→ 互异值装不下 → 点名法
    _values, collision = mux_gen.alloc_distinct_values(grp, exp["bindings"], exp["data_keys"])
    assert collision
    # 点名 ci=0（t0）：t0 喂的两个 case(0,1)都给标记，t1/t2 置 0
    mv = mux_gen._marker_values(grp, 0, exp["bindings"], exp["data_keys"], "ones")
    assert mv[0] == 1 and mv[1] == 1 and mv[2] == 0 and mv[3] == 0, mv
    # 端到端：点名法生成、不空、不丢
    vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode="min")
    assert vecs and meta.get("marker_method") and meta["dropped"] == 0, meta.get("dropped_reasons")


def test_wl_cascaded_control_with_shared_data_source_generates(tmp_path):
    """⭐回归（完全贴合真表 vga_cfb_dpd_ss_lut5 形态）：控制是级联 mux(temp_code) + 下游同一个
    数据寄存器喂多个 case。控制侧完全正常解析（全 RW/regmap 命中），却因数据侧 by_base 冲突空向量
    → 误报"控制信号没有可用的驱动路径"（指错了地方）。修复后正常生成。"""
    wb = _build_mux_wb(
        str(tmp_path / "casc_shared.xlsx"),
        tmm_fields=[
            ("TCMODE", "h10", "d_tcmode", "0", "N", "RW"),
            ("TCLINE", "h11", "d_tcline[3:0]", "3:0", "Y", "RO"),     # 够宽载体（RO 线）
            ("TCLOCAL", "h12", "d_tclocal[3:0]", "3:0", "N", "RW"),   # 够宽载体（RW，优先）
            ("DT0", "h20", "d_dt0[2:0]", "2:0", "N", "RW"),
            ("DT1", "h21", "d_dt1[2:0]", "2:0", "N", "RW"),
            ("DT2", "h22", "d_dt2[2:0]", "2:0", "N", "RW"),
        ],
        mux_rows=[
            # 上游组1：控制 mux d_tc = case(tcmode){0:line; 1:local}
            _mrow("d_tcline_to_mux[3:0]", "d_tcmode_to_mux", "1'b0", "d_tc[3:0]", 1),
            _mrow("d_tclocal_to_mux[3:0]", "d_tcmode_to_mux", "1'b1", "d_tc[3:0]", 1),
            # 下游组2：控制=d_tc(级联)，5 个 case 映射 t0/t0/t1/t1/t2（同一寄存器喂多 case）
            _mrow("d_dt0_to_mux[2:0]", "d_tc_to_mux[3:0]", "4'b0000", "d_dout[2:0]", 2),
            _mrow("d_dt0_to_mux[2:0]", "d_tc_to_mux[3:0]", "4'b0001", "d_dout[2:0]", 2),  # 同源 t0
            _mrow("d_dt1_to_mux[2:0]", "d_tc_to_mux[3:0]", "4'b0010", "d_dout[2:0]", 2),
            _mrow("d_dt1_to_mux[2:0]", "d_tc_to_mux[3:0]", "4'b0011", "d_dout[2:0]", 2),  # 同源 t1
            _mrow("d_dt2_to_mux[2:0]", "d_tc_to_mux[3:0]", "4'b0100", "d_dout[2:0]", 2),
        ],
    )
    r = resolver.Resolver(wb)
    grp = _grp(wb, "d_dout")
    exp = mux_gen.expand_mux_group(wb, r, grp)
    assert not exp["issues"], exp["issues"]
    assert exp["ctrl_drivers"][0]["source"] == "mux"      # 控制确是级联 mux，正常解析
    # 端到端：向量生成、不空、不丢（修复前这里空 → 误报"控制无驱动路径"）
    vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode="min")
    assert vecs, "级联控制+同源数据不该再产生空向量"
    assert meta["dropped"] == 0, meta.get("dropped_reasons")
    # 同源 t0 的两个 case 在同一条向量里写同值
    dv, _coll = mux_gen.alloc_distinct_values(grp, exp["bindings"], exp["data_keys"])
    assert dv[0] == dv[1] and dv[2] == dv[3] and dv[0] != dv[2] != dv[4]


def test_empty_vector_reason_surfaces_dropped_reasons():
    """向量为空时不再一律甩"控制无驱动路径"：meta 有逐 case 丢弃原因就抛真因（去重）。"""
    # 有 dropped_reasons → 抛真因
    meta = {"dropped_reasons": [
        "case 2'b00: 寄存器 d_x 同时被下游数据与上游级联配方写不同值（冲突），该向量丢弃",
        "case 2'b01: 寄存器 d_x 同时被下游数据与上游级联配方写不同值（冲突），该向量丢弃",
    ]}
    msg = generator._empty_vector_reason(meta)
    assert "都被丢弃" in msg and "冲突" in msg
    assert "控制信号没有可用的驱动路径" not in msg
    # 无 dropped_reasons（控制来源 unknown / 无扫描路径）→ 通用兜底
    assert "控制信号没有可用的驱动路径" in generator._empty_vector_reason({})


# ───────────── 嵌套 mux 自动归一化（2026-06-04，envdet5g 实证形态） ─────────────
def _nested_envdet_wb(path):
    """复刻真表 envdet5g cornercode（1607~1612 行）的两级嵌套块：
       里层 case(r_code[2:0]){00x:lut0;01x:lut1;10x:lut2;11x:lut3} + 外层 case(sel){0:自己;1:local}。"""
    return _build_mux_wb(
        path,
        tmm_fields=[
            ("RCODE", "h3B", "d_r_code[2:0]", "2:0", "N", "RW"),
            ("SEL", "h10", "d_cc_sel", "0", "N", "RW"),
            ("LUT", "h20", "d_cc_lut0[1:0]", "1:0", "N", "RW"),
            ("LUT", "h20", "d_cc_lut1[1:0]", "3:2", "N", "RW"),
            ("LUT", "h20", "d_cc_lut2[1:0]", "5:4", "N", "RW"),
            ("LUT", "h20", "d_cc_lut3[1:0]", "7:6", "N", "RW"),
            ("LOCAL", "h21", "d_cc_local[1:0]", "1:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_cc_lut0_to_mux[1:0]", "d_r_code_to_mux[2:0]", "3'b00x", "d_cc[1:0]", 1),
            _mrow("d_cc_lut1_to_mux[1:0]", "d_r_code_to_mux[2:0]", "3'b01x", "d_cc[1:0]", 1),
            _mrow("d_cc_lut2_to_mux[1:0]", "d_r_code_to_mux[2:0]", "3'b10x", "d_cc[1:0]", 1),
            _mrow("d_cc_lut3_to_mux[1:0]", "d_r_code_to_mux[2:0]", "3'b11x", "d_cc[1:0]", 1),
            _mrow("d_cc_to_mux[1:0]", "d_cc_sel_to_mux", "1'b0", "d_cc[1:0]", 1),       # 自引用=里层结果
            _mrow("d_cc_local_to_mux[1:0]", "d_cc_sel_to_mux", "1'b1", "d_cc[1:0]", 1),
        ],
    )


def test_nested_mux_normalized_to_multi_ctrl(tmp_path):
    """⭐两级嵌套块（不同行不同控制+自引用）自动折叠成多控制拼接 {sel, r_code}，6 行→5 行。"""
    wb = _nested_envdet_wb(str(tmp_path / "nested.xlsx"))
    grp = _grp(wb, "d_cc")
    # 控制变成 [sel(高), r_code(低)]，4-bit 拼接
    assert grp.is_multi_ctrl and [c.base for c in grp.ctrls] == ["d_cc_sel", "d_r_code"]
    assert grp.ctrl_total_width == 4
    assert not grp.ctrl_mismatch_rows                      # 折叠后清空，不再报"控制列不一致"
    assert grp.normalized_note and "嵌套" in grp.normalized_note
    # 自引用那行消失；5 行拼接 case 正确
    pairs = [(c.case_raw, c.input_base) for c in grp.cases]
    assert pairs == [
        ("4'b1xxx", "d_cc_local"),       # sel=1 → local（r_code don't-care）
        ("4'b000x", "d_cc_lut0"),        # sel=0 → r_code 选 lut
        ("4'b001x", "d_cc_lut1"),
        ("4'b010x", "d_cc_lut2"),
        ("4'b011x", "d_cc_lut3"),
    ], pairs
    assert all(c.input_base != "d_cc" for c in grp.cases)  # 自引用已剔除


def test_nested_mux_end_to_end_generates(tmp_path):
    """折叠后走老的多控制拼接路径：解析通、向量生成、不空（救活原本的 ✗未解析）。"""
    wb = _nested_envdet_wb(str(tmp_path / "nested2.xlsx"))
    grp = _grp(wb, "d_cc")
    r = resolver.Resolver(wb)
    exp = mux_gen.expand_mux_group(wb, r, grp)
    assert not exp["issues"], exp["issues"]
    vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode="min")
    assert vecs and meta["dropped"] == 0, meta.get("dropped_reasons")
    assert meta.get("multi_ctrl")
    res = generator.build(wb, generator.GenOptions(types=["mux"], include_risky=True))
    assert res["summary"]["n_mux_generated"] == 1
    # 可见性：.sv 块顶有 ⚙ 注释，report 的可验证性 detail 也带 note（designer 复核用）
    sv_lines = [ln for lines, st in res["blocks"] if st.get("is_mux") for ln in lines]
    assert any("⚙" in ln and "嵌套" in ln for ln in sv_lines), "缺 .sv 折叠注释"
    rep = generator.report(wb, generator.GenOptions(types=["mux"]))
    vdet = next(v["detail"] for v in rep["verifiability"]["signals"] if v["type"] == "mux")
    assert "⚙" in vdet and "嵌套" in vdet, vdet


def test_nested_mux_no_self_ref_stays_errored(tmp_path):
    """护栏：控制列不一致但【没有自引用】（真 typo 形态）→ 不折叠，维持 ctrl_mismatch 报错。"""
    wb = _build_mux_wb(
        str(tmp_path / "noself.xlsx"),
        tmm_fields=[
            ("RCODE", "h3B", "d_r_code[2:0]", "2:0", "N", "RW"),
            ("SEL", "h10", "d_xx_sel", "0", "N", "RW"),
            ("D0", "h20", "d_xx_d0[1:0]", "1:0", "N", "RW"),
            ("D1", "h21", "d_xx_d1[1:0]", "1:0", "N", "RW"),
            ("D2", "h22", "d_xx_d2[1:0]", "1:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_xx_d0_to_mux[1:0]", "d_r_code_to_mux[2:0]", "3'b00x", "d_xx[1:0]", 1),
            _mrow("d_xx_d1_to_mux[1:0]", "d_r_code_to_mux[2:0]", "3'b01x", "d_xx[1:0]", 1),
            _mrow("d_xx_d2_to_mux[1:0]", "d_xx_sel_to_mux", "1'b1", "d_xx[1:0]", 1),   # 控制变了但无自引用
        ],
    )
    grp = _grp(wb, "d_xx")
    assert grp.ctrl_mismatch_rows                          # 没折叠，原报错保留
    assert not grp.normalized_note
    exp = mux_gen.expand_mux_group(wb, resolver.Resolver(wb), grp)
    assert any("不一致" in i for i in exp["issues"]), exp["issues"]


def test_nested_mux_three_regimes_not_normalized(tmp_path):
    """护栏：三个控制 regime（>2 级）太复杂、不猜 → 不折叠，维持原报错。"""
    wb = _build_mux_wb(
        str(tmp_path / "three.xlsx"),
        tmm_fields=[
            ("A", "h10", "d_a3", "0", "N", "RW"),
            ("B", "h11", "d_b3", "0", "N", "RW"),
            ("C", "h12", "d_c3", "0", "N", "RW"),
            ("D0", "h20", "d_y3_d0[1:0]", "1:0", "N", "RW"),
            ("D1", "h21", "d_y3_d1[1:0]", "1:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_y3_d0_to_mux[1:0]", "d_a3_to_mux", "1'b0", "d_y3[1:0]", 1),
            _mrow("d_y3_to_mux[1:0]", "d_b3_to_mux", "1'b1", "d_y3[1:0]", 1),       # 自引用
            _mrow("d_y3_d1_to_mux[1:0]", "d_c3_to_mux", "1'b1", "d_y3[1:0]", 1),    # 第三个控制
        ],
    )
    grp = _grp(wb, "d_y3")
    assert not grp.normalized_note and grp.ctrl_mismatch_rows


def test_nested_mux_bad_width_not_normalized(tmp_path):
    """护栏：子块 case 位宽与其控制位宽对不上 → _case_bitstr 抛错 → 整组 bail（不部分折叠），
    维持原 ctrl_mismatch 报错（绝不猜一个错结构出来）。"""
    wb = _build_mux_wb(
        str(tmp_path / "badw.xlsx"),
        tmm_fields=[
            ("RCODE", "h3B", "d_r_code[2:0]", "2:0", "N", "RW"),
            ("SEL", "h10", "d_bw_sel", "0", "N", "RW"),
            ("D0", "h20", "d_bw_d0[1:0]", "1:0", "N", "RW"),
            ("LOCAL", "h21", "d_bw_local[1:0]", "1:0", "N", "RW"),
        ],
        mux_rows=[
            _mrow("d_bw_d0_to_mux[1:0]", "d_r_code_to_mux[2:0]", "4'b0000", "d_bw[1:0]", 1),  # 4bit case vs 3bit 控制
            _mrow("d_bw_to_mux[1:0]", "d_bw_sel_to_mux", "1'b0", "d_bw[1:0]", 1),             # 自引用
            _mrow("d_bw_local_to_mux[1:0]", "d_bw_sel_to_mux", "1'b1", "d_bw[1:0]", 1),
        ],
    )
    grp = _grp(wb, "d_bw")
    assert not grp.normalized_note and grp.ctrl_mismatch_rows


def test_normal_multi_ctrl_group_untouched(wl_wb):
    """护栏：今天能跑的正常多控制拼接组（控制每行一致）完全不进归一化——note 空、结构原样。"""
    g4 = _grp(wl_wb, "d_wl_rf_tx_rc_code")                 # 真·多控制拼接 {lut_en, bwctrl}
    assert not g4.normalized_note
    assert not g4.ctrl_mismatch_rows
    assert [c.base for c in g4.ctrls] == ["d_wl_rf_rc_code_lut_en", "d_wl_rf_bwctrl"]
