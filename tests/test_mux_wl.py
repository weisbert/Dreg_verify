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


def test_wl_build_skips_without_prefix(wl_wb):
    """没配探针前缀：top_out=0 的 mux 组全部跳过且原因写明 scan_rtl（不生成必 CUVUNF 的 .sv）。"""
    res = generator.build(wl_wb, generator.GenOptions())
    assert res["summary"]["n_mux_generated"] == 0
    mux_skips = [(n, a, r) for n, a, r in res["skipped"] if str(a).startswith("mux")]
    assert len(mux_skips) == 5
    assert all("scan_rtl" in str(r) for _n, _a, r in mux_skips)


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
    """report 与 build 双轨同步：同口径跳过/生成；多控制键正确分类。"""
    # 无前缀：report 的 mux 行 error 列都写前缀原因
    rep0 = generator.report(wl_wb, generator.GenOptions())
    mux_rows0 = [r for r in rep0["summary"] if r["type"] == "mux"]
    assert len(mux_rows0) == 5
    assert all("scan_rtl" in r["error"] for r in mux_rows0)
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
    """analyze_mux_group（GUI 状态源）：无前缀 → needs-prefix；有前缀 → clean。"""
    g1 = _grp(wl_wb, "d_wl_rf_lna_gain")
    a0 = generator.analyze_mux_group(wl_resolver, wl_wb, g1)
    assert a0["status"] == "needs-prefix"
    assert "scan_rtl" in a0["error"]
    a1 = generator.analyze_mux_group(wl_resolver, wl_wb, g1, probe_prefix=WL_PREFIX)
    assert a1["status"] == "clean"


def test_wl_negative_vectors(wl_wb):
    """负向用例（反例）对通用形态向量同样可用。"""
    opts = generator.GenOptions(probe_prefixes=WL_PREFIXES, neg_all=True)
    res = generator.build(wl_wb, opts)
    assert res["summary"]["n_mux_generated"] == 5
    text = "\n".join("\n".join(lines) for lines, _ in res["blocks"])
    assert "_NEG:" in text                               # 负向 assert 标签


# ───────────── ⑦ 对抗式审查确认的 5 个缺陷的回归（2026-06-03 第十四轮 review）─────────────
from fixtures import _set_row, _tmm_reg, _tmm_field  # noqa: E402


def _build_mux_wb(path, tmm_fields, mux_rows, regmap_fields=None):
    """造一个只有 logic(空)/tmm/regmap/mux 四页的最小工作簿，给级联/边界回归用。

    tmm_fields: [(reg_name, addr, field_name(可带[msb:lsb]), bit, dig, typ), ...]
    mux_rows:   [{列字母: 值}, ...]（直接 _set_row 到 mux 页数据行，row 从 3 起）
    regmap_fields: [(reg, typ, signal), ...] 可选
    """
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "logic"
    _set_row(ws, 2, {"A": "in_A", "K": "out", "L": "expr", "M": "suffix",
                     "N": "top", "P": "owner", "R": "no"})
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
