# -*- coding: utf-8 -*-
"""mux 页验证功能测试（2026-06-03 第九轮）。

覆盖（按实现顺序逐层补充）：
  ① 数据层: excel_model.read_mux / strip_to_mux / MuxGroup/MuxCase / wb.mux 向后兼容
  ② case 字面量: expr.parse_case_literal / expand_case_values / case_matches（don't-care 位）
  后续: ③ resolver 数据寄存器/控制双路径 ④ make_mux_vectors ⑤ generator/sv_writer 端到端
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fixtures                                     # noqa: E402
from dreg_verify import excel_model                 # noqa: E402
from dreg_verify import expr as E                   # noqa: E402


# ───────────── fixtures ─────────────
@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    path = tmp_path_factory.mktemp("mux") / "synthetic_mux.xlsx"
    fixtures.build_workbook(str(path), with_mux=True)
    return excel_model.load_workbook(str(path))


@pytest.fixture(scope="module")
def wb_no_mux(tmp_path_factory):
    path = tmp_path_factory.mktemp("nomux") / "synthetic.xlsx"
    fixtures.build_workbook(str(path))
    return excel_model.load_workbook(str(path))


# ───────────── ① strip_to_mux ─────────────
def test_strip_to_mux():
    assert excel_model.strip_to_mux("d_x_to_mux") == "d_x"
    assert excel_model.strip_to_mux("d_x_TO_MUX") == "d_x"
    assert excel_model.strip_to_mux("d_x_to_logic") == "d_x_to_logic"   # 不剥 to_logic
    assert excel_model.strip_to_mux("d_x") == "d_x"
    # 反向：strip_to_logic 也不剥 _to_mux（两个后缀语义不同）
    assert excel_model.strip_to_logic("d_x_to_mux") == "d_x_to_mux"


# ───────────── ① read_mux ─────────────
def test_read_mux_groups(wb):
    assert len(wb.mux) == 2
    g1, g2 = wb.mux
    # 组1: rccal_i, 3 个 case, 控制=lna_agc
    assert g1.group_no == 1
    assert g1.out_name == "d_bt_lp_rccal_i[3:0]"
    assert g1.out_base == "d_bt_lp_rccal_i"
    assert g1.out_width == 4
    assert g1.ctrl_base == "d_logic_bt_lp_lna_agc"      # 剥 _to_mux + 位宽
    assert g1.ctrl_width == 3
    assert g1.owner == "Alice"
    assert g1.is_top
    assert [c.case_raw for c in g1.cases] == ["3'b010", "3'b011", "3'b10x"]
    assert g1.cases[0].input_base == "d_bt_lp_rccal_i_g1"   # 剥 _to_mux + 位宽
    assert g1.cases[0].input_width == 4
    # 组2
    assert g2.group_no == 2
    assert g2.out_base == "d_bt_lp_bias_q"
    assert g2.owner == "Bob"
    assert len(g2.cases) == 2


def test_read_mux_assert_id_and_rtl(wb):
    g1 = wb.mux[0]
    # 方案A: assert_mux<N>_T<n>，mux 前缀防与 logic R 号撞号
    assert g1.assert_id == "mux1"
    assert wb.mux[1].assert_id == "mux2"
    # mux 输出 = 顶层网名直接探（环境验证实证），不走 logic 的 _ls/_to_logic 变换
    assert g1.rtl_base == "d_bt_lp_rccal_i"
    assert g1.rtl_name == "d_bt_lp_rccal_i[3:0]"


def test_read_mux_filters_reserved(wb):
    # (reserved) 行 F 列为空 → 被过滤，且不会把组一分为二
    for g in wb.mux:
        for c in g.cases:
            assert "reserved" not in c.input_base.lower()


def test_no_mux_sheet_backward_compat(wb_no_mux):
    # 无 mux 页: wb.mux=[] 且 logic 流程完全不受影响（现有 236 个测试的前提）
    assert wb_no_mux.mux == []
    assert len(wb_no_mux.logic) == 3


def test_mux_data_registers_resolvable_in_tmm(wb):
    # mux 数据输入剥 _to_mux 后能在 tmm 查到地址（RF_WRITE 互异值的前提）
    g1 = wb.mux[0]
    for c in g1.cases:
        assert c.input_base in wb.tmm, "%s 应能在 tmm 查到" % c.input_base
        assert wb.tmm[c.input_base].reg_type == "RW"
    # g1/g2 同地址不同字段（镜像真表 t1..t4 同住一个 16bit 寄存器）→ 互异值分配要警惕合并裁剪
    assert wb.tmm["d_bt_lp_rccal_i_g1"].address == wb.tmm["d_bt_lp_rccal_i_g2"].address
    assert wb.tmm["d_bt_lp_rccal_i_g1"].bit_lsb != wb.tmm["d_bt_lp_rccal_i_g2"].bit_lsb


def test_mux_ctrl_links_to_logic_to_mux_row(wb):
    # 衔接点: 控制信号基名 == logic 页 M=to_mux 行的 K 基名
    g1 = wb.mux[0]
    logic_by_base = {s.out_base.lower(): s for s in wb.logic}
    assert g1.ctrl_base.lower() in logic_by_base
    ctrl_row = logic_by_base[g1.ctrl_base.lower()]
    assert ctrl_row.suffix == "to_mux"
    assert ctrl_row.is_top


# ───────────── ② parse_case_literal ─────────────
def test_parse_case_literal_plain():
    assert E.parse_case_literal("3'b010") == (0b010, 3, 0)
    assert E.parse_case_literal("3'b111") == (0b111, 3, 0)


def test_parse_case_literal_dontcare():
    assert E.parse_case_literal("4'b000x") == (0b0000, 4, 0b0001)
    assert E.parse_case_literal("4'b1x0x") == (0b1000, 4, 0b0101)
    assert E.parse_case_literal("3'b10x") == (0b100, 3, 0b001)
    # 真表 tsensor 形态全集 4'b000x..4'b111x
    for i in range(8):
        v, w, dc = E.parse_case_literal("4'b%s%s%sx" % ((i >> 2) & 1, (i >> 1) & 1, i & 1))
        assert (v >> 1, w, dc) == (i, 4, 1)


def test_parse_case_literal_hex_no_x():
    assert E.parse_case_literal("4'hA") == (0xA, 4, 0)


def test_parse_case_literal_errors():
    with pytest.raises(E.ExprError):
        E.parse_case_literal("not_a_literal")
    with pytest.raises(E.ExprError):
        E.parse_case_literal("4'hAx")       # 非二进制含 x
    with pytest.raises(E.ExprError):
        E.parse_case_literal("4'sb0000")    # 有符号


def test_parse_based_literal_unchanged():
    # 现有 parse_based_literal 行为不变（x 当 0）—— logic 路径不受任何影响
    assert E.parse_based_literal("4'b000x") == (0, 4)


# ───────────── ② expand_case_values / case_matches ─────────────
def test_expand_case_values():
    assert E.expand_case_values(0b010, 3, 0) == [0b010]                          # 无 x
    assert E.expand_case_values(0b0000, 4, 0b0001) == [0b0000, 0b0001]           # 1 个 x
    assert E.expand_case_values(0b1000, 4, 0b0101) == [0b1000, 0b1001, 0b1100, 0b1101]  # 2 个 x


def test_case_matches():
    # 4'b000x: 命中 0/1, 不命中 2
    assert E.case_matches(0b0000, 4, 0b0001, 0b0000)
    assert E.case_matches(0b0000, 4, 0b0001, 0b0001)
    assert not E.case_matches(0b0000, 4, 0b0001, 0b0010)
    # 精确 case
    assert E.case_matches(0b010, 3, 0, 0b010)
    assert not E.case_matches(0b010, 3, 0, 0b011)


def test_designer_sv_decoded_case_mapping():
    """复盘 designer .sv 的 assert_151 解码: tsensor=0..15 → t1..t8（每 case 命中 2 个值）。"""
    cases = ["4'b000x", "4'b001x", "4'b010x", "4'b011x",
             "4'b100x", "4'b101x", "4'b110x", "4'b111x"]
    for ctrl in range(16):
        hits = [i for i, c in enumerate(cases)
                if E.case_matches(*E.parse_case_literal(c), ctrl_value=ctrl)]
        assert hits == [ctrl >> 1], "tsensor=%d 应只命中 t%d" % (ctrl, (ctrl >> 1) + 1)
