# -*- coding: utf-8 -*-
"""对抗式审查确认的 8 个问题的回归测试（每条对应一个 review finding）。"""

import os
import sys

import openpyxl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import expr as E             # noqa: E402
from dreg_verify import vectors as V          # noqa: E402
from dreg_verify import sv_writer as W        # noqa: E402
from dreg_verify import excel_model as M      # noqa: E402
from dreg_verify import generator as G       # noqa: E402
from dreg_verify.excel_model import _normalize_type, read_tmm, DregWorkbook, TmmField, LogicSignal  # noqa: E402
from dreg_verify.resolver import Resolver, InputBinding  # noqa: E402


# ── #1/#8 除法：解析与求值一致，且结果正确 ──
def test_division_consistent():
    assert E.eval_expr("A/B", {"A": 4, "B": 4}, {"A": 8, "B": 2}, 4)[0] == 4
    assert E.eval_expr("A/B", {"A": 4, "B": 4}, {"A": 8, "B": 0}, 4)[0] == 0   # 除0→0


# ── #2 有符号字面量：显式拒绝（不再静默按无符号）──
def test_signed_literal_rejected():
    with pytest.raises(E.ExprError):
        E.parse("4'sb1010")
    with pytest.raises(E.ExprError):
        E.eval_expr("4'sb1010", {}, {}, 4)


# ── #3 rule4：字段值按字段位宽裁剪，溢出不污染相邻字段 ──
def test_rule4_field_width_clamp():
    foo = InputBinding("A", "foo", "foo", width=3, kind="RW", address=0x10,
                       reg_lsb=0, reg_msb=0, wire="foo", found_in="tmm")   # 实际只占 bit0
    bar = InputBinding("B", "bar", "bar", width=1, kind="RW", address=0x10,
                       reg_lsb=1, reg_msb=1, wire="bar", found_in="tmm")
    vec = V.TestVector(0, {"A": 5, "B": 1}, exp_value=0, exp_width=1)       # foo=5 溢出 bit0
    lines, unresolved = W._build_drive_lines(vec, {"A": foo, "B": bar}, ["A", "B"])
    text = "\n".join(lines)
    assert not unresolved
    assert "16'h0003" in text          # foo 截为 bit0=1, bar=bit1 → 0x3
    assert "16'h0007" not in text      # 不应是 0x7（溢出污染）


# ── #4 截断/去重统计自洽：向量数 + 去重数 + 丢弃数 == 计划组合数 ──
def test_truncate_dedup_accounting():
    class _B:
        def __init__(self, w):
            self.width = w
    bindings = {x: _B(1) for x in "ABCD"}
    node = E.parse("(A?C:B)|D")        # 控制 A,D(各1位→4组合)，数据 B,C
    vecs, meta = V.generate_vectors(node, bindings, out_width=1, mode="max", max_tests=7)
    control, _ = E.classify_vars(node, E.Env({x: 1 for x in "ABCD"}))
    planned = (2 ** len(control)) * 5  # max=5 主题
    assert "deduped" in meta
    assert len(vecs) + meta["deduped"] + meta.get("dropped", 0) == planned
    assert meta["truncated"] is True


# ── #5 resolver 歧义后缀匹配：不静默选第一个，判 UNKNOWN + note ──
def test_resolver_ambiguous_suffix():
    tmm = {
        "d_x_bt_mode_sel": TmmField("d_x_bt_mode_sel", 0, 0, 0x2C, "RW", "N", "R1"),
        "d_y_lna_line_sel": TmmField("d_y_lna_line_sel", 0, 0, 0x31, "RW", "N", "R2"),
    }
    wb = DregWorkbook(logic=[], regmap={}, tmm=tmm, sheet_names=[])
    b = Resolver(wb).resolve("A", {"base": "sel", "width": 1, "raw": "sel"})
    assert b.kind == "UNKNOWN"
    assert not b.resolved
    assert "模糊匹配" in b.note


def test_resolver_single_suffix_still_works():
    tmm = {"d_x_bt_mode_sel": TmmField("d_x_bt_mode_sel", 0, 0, 0x2C, "RW", "N", "R1")}
    wb = DregWorkbook(logic=[], regmap={}, tmm=tmm, sheet_names=[])
    b = Resolver(wb).resolve("A", {"base": "bt_mode_sel", "width": 1, "raw": "x"})
    assert b.kind == "RW" and b.address == 0x2C and b.resolved   # 唯一后缀匹配仍可用


def test_resolver_ambiguous_honors_override():
    tmm = {
        "d_x_bt_mode_sel": TmmField("d_x_bt_mode_sel", 0, 0, 0x2C, "RW", "N", "R1"),
        "d_y_lna_line_sel": TmmField("d_y_lna_line_sel", 0, 0, 0x31, "RW", "N", "R2"),
    }
    wb = DregWorkbook(logic=[], regmap={}, tmm=tmm, sheet_names=[])
    b = Resolver(wb, force_overrides=["sel"]).resolve("A", {"base": "sel", "width": 1, "raw": "x"})
    assert b.kind == "RO"   # 用户显式覆盖时即便歧义也尊重


# ── #6 _normalize_type：各种 RO/RW 写法 ──
@pytest.mark.parametrize("text,expect", [
    ("RW", "RW"), ("RO", "RO"), ("R", "RO"), ("W", "RW"),
    ("R/W", "RW"), ("READ/WRITE", "RW"), ("READ WRITE", "RW"), ("Read Write", "RW"),
    ("WO", "RW"), ("W/O", "RW"), ("READ ONLY", "RO"), ("Read Only", "RO"),
    ("READ", "RO"), ("", None),
])
def test_normalize_type(text, expect):
    assert _normalize_type(text) == expect


# ── #7 dig_top_pin：'NA'/'N/A' 不被误判为 'N' ──
def test_dig_top_pin_exact():
    wb = openpyxl.Workbook()
    ws = wb.active
    from openpyxl.utils import column_index_from_string as ci

    def setrow(r, m):
        for col, v in m.items():
            ws.cell(row=r, column=ci(col), value=v)

    setrow(1, {"A": "REG1", "B": "h40", "D": "RW"})            # 寄存器行
    setrow(2, {"A": "f_na", "B": "0", "F": "h40", "D": "NA", "H": "RO"})
    setrow(3, {"A": "f_y", "B": "1", "F": "h40", "D": "Y", "H": "RO"})
    setrow(4, {"A": "f_n", "B": "2", "F": "h40", "D": "N", "H": "RW"})
    fields = read_tmm(ws)
    assert fields["f_na"].dig_top_pin is None      # 'NA' → None（不再误判 'N'）
    assert fields["f_y"].dig_top_pin == "Y"
    assert fields["f_n"].dig_top_pin == "N"


# ── 追加: owner 名字含空格的筛选（大小写无关 + 折叠多余空格）──
def test_owner_filter_with_spaces():
    sig = LogicSignal(row=3, out_name="d_x", out_width=1, expr="A", suffix="ls",
                      top_output=1, notes="", owner="Wei Yu", assert_id="1",
                      inputs={"A": {"raw": "a_to_logic", "base": "a", "width": 1,
                                    "msb": None, "lsb": None}})
    wb = DregWorkbook(logic=[sig], regmap={}, tmm={}, sheet_names=[])
    for q in ["Wei Yu", "wei yu", "Wei  Yu", "  WEI YU  "]:
        assert len(G.select_signals(wb, G.GenOptions(owners=[q]))) == 1, q
    assert len(G.select_signals(wb, G.GenOptions(owners=["Alice"]))) == 0


# ── 追加: 级联中间信号 + wire 兜底（对应真表诊断里的 pll_n*/*_mux_out 等）──
def _logic(out_name, out_width, expr, inputs, aid, owner="C", suffix="ls"):
    return LogicSignal(row=1, out_name=out_name, out_width=out_width, expr=expr,
                       suffix=suffix, top_output=0, notes="", owner=owner,
                       assert_id=aid, inputs=inputs)


def test_chained_and_wire_fallback():
    # plln1 是一个 32bit logic 输出；plln2 把 plln1 当输入(级联中间信号)
    s1 = _logic("d_plln1[31:0]", 32, "A",
                {"A": {"raw": "int_n_to_logic[31:0]", "base": "int_n", "width": 32,
                       "msb": 31, "lsb": 0}}, "60")
    s2 = _logic("d_plln2[31:0]", 32, "A",
                {"A": {"raw": "d_plln1", "base": "d_plln1", "width": 1,
                       "msb": None, "lsb": None}}, "61", suffix="to_mux")
    wb = DregWorkbook(logic=[s1, s2], regmap={}, tmm={}, sheet_names=[])
    res = Resolver(wb)
    # s1.A=int_n 表里查无 → wire 兜底 force
    b1 = res.resolve_signal_inputs(s1)["A"]
    assert b1.kind == "RO" and b1.found_in == "wire" and b1.resolved
    # s2.A=d_plln1 是 logic 输出 → 级联，宽度取真实 32
    b2 = res.resolve_signal_inputs(s2)["A"]
    assert b2.kind == "RO" and b2.found_in == "logic" and b2.width == 32


def test_force_literal_width_adaptive():
    # 32bit wire 的 force 字面量应是 32'h，不被截成 16'h
    b = InputBinding("A", "d_plln1", "d_plln1", width=32, kind="RO", address=None,
                     reg_lsb=None, reg_msb=None, wire="d_plln1", found_in="logic")
    vec = V.TestVector(0, {"A": 0x12345678}, exp_value=0, exp_width=32)
    lines, _ = W._build_drive_lines(vec, {"A": b}, ["A"])
    assert "32'h12345678" in "\n".join(lines)


def test_exclude_signals_and_regex():
    s1 = _logic("pll_n1[31:0]", 32, "A", {"A": {"raw": "x", "base": "x", "width": 1,
                                                  "msb": None, "lsb": None}}, "1")
    s2 = _logic("d_logic_bt_lp_reserve", 1, "A", {"A": {"raw": "y", "base": "y", "width": 1,
                                                         "msb": None, "lsb": None}}, "2")
    wb = DregWorkbook(logic=[s1, s2], regmap={}, tmm={}, sheet_names=[])
    # 按名排除
    sel = G.select_signals(wb, G.GenOptions(exclude=["pll_n1"]))
    assert [s.out_name for s in sel] == ["d_logic_bt_lp_reserve"]
    # 按正则排除 datapath
    sel2 = G.select_signals(wb, G.GenOptions(exclude_regex="pll_n|_to_dsm"))
    assert [s.out_name for s in sel2] == ["d_logic_bt_lp_reserve"]


def test_no_wire_fallback_keeps_unknown():
    s1 = _logic("d_x", 1, "A",
                {"A": {"raw": "foo", "base": "foo", "width": 1, "msb": None, "lsb": None}}, "1")
    wb = DregWorkbook(logic=[s1], regmap={}, tmm={}, sheet_names=[])
    b = Resolver(wb, wire_fallback=False).resolve_signal_inputs(s1)["A"]
    assert b.kind == "UNKNOWN" and not b.resolved


# ── 追加: tmm/regmap 字段名带位宽([8:0])时去标注后匹配（真表里 int_n[8:0] 这类）──
def test_tmm_regmap_strip_width_in_field_names():
    from openpyxl.utils import column_index_from_string as ci
    from dreg_verify.excel_model import read_tmm, read_regmap

    twb = openpyxl.Workbook()
    tws = twb.active

    def sr(r, m):
        for c, v in m.items():
            tws.cell(row=r, column=ci(c), value=v)
    sr(1, {"A": "INT_N", "B": "h2", "D": "RW"})                       # 寄存器定义行
    sr(2, {"A": "int_n[8:0]", "B": "15:7", "C": "d45", "D": "N", "F": "h2", "H": "RW"})
    fields = read_tmm(tws)
    assert "int_n" in fields and "int_n[8:0]" not in fields
    assert fields["int_n"].address == 0x2
    assert fields["int_n"].bit_msb == 15 and fields["int_n"].bit_lsb == 7

    rwb = openpyxl.Workbook()
    rws = rwb.active

    def sr2(r, m):
        for c, v in m.items():
            rws.cell(row=r, column=ci(c), value=v)
    sr2(2, {"G": "Signal_Name", "F": "Reg Type", "H": "Address"})     # 表头 row2
    sr2(3, {"G": "int_n[8:0]", "F": "RW", "H": "d2"})                 # d2 = 十进制 2
    reg = read_regmap(rws)
    assert "int_n" in reg and reg["int_n"].address == 2 and reg["int_n"].reg_type == "RW"


def test_real_pll_field_resolves_to_rfwrite():
    # 复现真表：logic 输入基名 int_n，tmm 字段名 int_n[8:0]（h2, 15:7）→ 应解析为 RW/RF_WRITE
    from openpyxl.utils import column_index_from_string as ci
    from dreg_verify.excel_model import read_tmm
    twb = openpyxl.Workbook()
    tws = twb.active

    def sr(r, m):
        for c, v in m.items():
            tws.cell(row=r, column=ci(c), value=v)
    sr(1, {"A": "PFD", "B": "hD", "D": "RW"})
    sr(2, {"A": "d_pfd_en_lnmode[1:0]", "B": "15:14", "D": "N", "F": "hD", "H": "RW"})
    wb = DregWorkbook(logic=[], regmap={}, tmm=read_tmm(tws), sheet_names=[])
    b = Resolver(wb).resolve("A", {"base": "d_pfd_en_lnmode", "width": 2, "raw": "x"})
    assert b.kind == "RW" and b.address == 0xD and b.reg_lsb == 14 and b.resolved


# ── 追加: 同名输入(同一物理信号占多个变量)共享取值，RF_WRITE 不重复 ──
def test_same_base_inputs_share_value():
    from dreg_verify.excel_model import TmmField
    tmm = {"d_pfd": TmmField("d_pfd", 15, 14, 0xD, "RW", "N", "PFD")}
    s = _logic("d_pfd", 2, "A?B:B",
               {"A": {"raw": "d_pfd", "base": "d_pfd", "width": 2, "msb": None, "lsb": None},
                "B": {"raw": "d_pfd", "base": "d_pfd", "width": 2, "msb": None, "lsb": None}},
               "1", suffix="to_mux")
    wb = DregWorkbook(logic=[s], regmap={}, tmm=tmm, sheet_names=[])
    res = Resolver(wb)
    bindings = res.resolve_signal_inputs(s)
    vecs, _ = V.generate_vectors(E.parse(s.expr), bindings, 2, mode="max")
    for v in vecs:                                  # 同一物理信号 → A、B 必同值
        assert v.assignments["A"] == v.assignments["B"]
    lines, _ = W._build_drive_lines(vecs[0], bindings, ["A", "B"])
    assert "\n".join(lines).count("`RF_WRITE(10'h00D") == 1   # 同字段只写一次，不重复


# ── #8 重复次数取自变量值时不被声明位宽截断 ──
def test_repeat_count_not_truncated():
    # A 声明 1 位但取值 2 → {2{B}} = 2'b11 = 3
    assert E.eval_expr("{A{B}}", {"A": 1, "B": 1}, {"A": 2, "B": 1}, 4)[0] == 0b11
    # 字面量次数不受影响
    assert E.eval_expr("{3{C}}", {"C": 1}, {"C": 1}, 3)[0] == 0b111
