# -*- coding: utf-8 -*-
"""表达式求值器单元测试。重点：Verilog 两遍位宽语义 + logic 页真实表达式形态。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dreg_verify import expr  # noqa: E402
from dreg_verify.expr import eval_expr, parse, ExprError, classify_vars, Env  # noqa: E402


def ev(text, widths, values, out_width):
    v, w = eval_expr(text, widths, values, out_width)
    return v


# ───────────── 基本变量 / 常量 ─────────────
def test_passthrough_scalar():
    assert ev("A", {"A": 1}, {"A": 1}, 1) == 1
    assert ev("A", {"A": 1}, {"A": 0}, 1) == 0


def test_based_literal_const():
    assert ev("1'b0", {}, {}, 1) == 0
    assert ev("1'b1", {}, {}, 1) == 1
    assert ev("5'b00000", {}, {}, 5) == 0
    assert ev("4'hF", {}, {}, 4) == 0xF
    assert ev("8'hA5", {}, {}, 8) == 0xA5
    assert ev("5'b10101", {}, {}, 5) == 0b10101


def test_bus_passthrough():
    assert ev("A", {"A": 3}, {"A": 0b101}, 3) == 0b101


# ───────────── 关键：~ 的上下文宽度陷阱 ─────────────
def test_not_context_width_scalar_extend():
    # out[2:0] = ~A ，A 为 1 位 = 1 → ~A 在上下文宽度3下 = 3'b110
    assert ev("~A", {"A": 1}, {"A": 1}, 3) == 0b110
    # A=0 → ~A = 3'b111
    assert ev("~A", {"A": 1}, {"A": 0}, 3) == 0b111


def test_not_self_width():
    # out 1 位 = ~A，A=1 → 0
    assert ev("~A", {"A": 1}, {"A": 1}, 1) == 0
    # 多位变量取反
    assert ev("~A", {"A": 3}, {"A": 0b101}, 3) == 0b010


# ───────────── reserve: (A?C:B)&(~J) ，全 1 位 ─────────────
@pytest.mark.parametrize("a,b,c,j,exp", [
    (0, 0, 0, 0, 0),   # 选 B=0, 未门控
    (0, 1, 0, 0, 1),   # 选 B=1
    (1, 0, 1, 0, 1),   # 选 C=1
    (1, 0, 0, 0, 0),   # 选 C=0
    (1, 0, 1, 1, 0),   # 选 C=1 但 iddq=1 门控拉 0
    (0, 1, 0, 1, 0),   # 选 B=1 但 iddq=1 门控拉 0
])
def test_reserve_expr(a, b, c, j, exp):
    widths = {"A": 1, "B": 1, "C": 1, "J": 1}
    vals = {"A": a, "B": b, "C": c, "J": j}
    assert ev("(A?C:B)&(~J)", widths, vals, 1) == exp


# ───────────── lna_agc: A?C:B ，[2:0] 3 位 mux ─────────────
@pytest.mark.parametrize("a,b,c,exp", [
    (0, 0b101, 0b010, 0b101),   # A=0 → 选 B
    (1, 0b101, 0b010, 0b010),   # A=1 → 选 C
    (0, 0b111, 0b000, 0b111),
    (1, 0b111, 0b000, 0b000),
])
def test_lna_agc_mux(a, b, c, exp):
    widths = {"A": 1, "B": 3, "C": 3}
    vals = {"A": a, "B": b, "C": c}
    assert ev("A?C:B", widths, vals, 3) == exp


# ───────────── 多位总线门控 {W{~J}} ─────────────
def test_replicated_gate():
    # (A?C:B) & {3{~J}} ，J=1 → 全 0；J=0 → 透传
    widths = {"A": 1, "B": 3, "C": 3, "J": 1}
    base = {"A": 1, "B": 0b101, "C": 0b010}
    assert ev("(A?C:B)&{3{~J}}", widths, {**base, "J": 0}, 3) == 0b010
    assert ev("(A?C:B)&{3{~J}}", widths, {**base, "J": 1}, 3) == 0


# ───────────── 拼接 ─────────────
def test_concat_width_and_value():
    # {A, B} ，A 2位=0b10, B 3位=0b011 → 共5位 = 0b10011
    widths = {"A": 2, "B": 3}
    vals = {"A": 0b10, "B": 0b011}
    assert ev("{A,B}", widths, vals, 5) == 0b10011


def test_concat_with_const():
    # {1'b0, A} ，A 3位 → 4位
    assert ev("{1'b0, A}", {"A": 3}, {"A": 0b111}, 4) == 0b0111


def test_repeat_concat():
    # {2{A}} ，A=0b01 (2位) → 0b0101
    assert ev("{2{A}}", {"A": 2}, {"A": 0b01}, 4) == 0b0101


# ───────────── 归约算子 ─────────────
def test_reduction_and():
    assert ev("&A", {"A": 3}, {"A": 0b111}, 1) == 1
    assert ev("&A", {"A": 3}, {"A": 0b110}, 1) == 0


def test_reduction_or_xor():
    assert ev("|A", {"A": 3}, {"A": 0b000}, 1) == 0
    assert ev("|A", {"A": 3}, {"A": 0b001}, 1) == 1
    assert ev("^A", {"A": 3}, {"A": 0b011}, 1) == 0
    assert ev("^A", {"A": 3}, {"A": 0b111}, 1) == 1


# ───────────── 按位 / 嵌套三元 ─────────────
def test_bitwise_and_or():
    widths = {"A": 4, "B": 4}
    assert ev("A&B", widths, {"A": 0b1100, "B": 0b1010}, 4) == 0b1000
    assert ev("A|B", widths, {"A": 0b1100, "B": 0b1010}, 4) == 0b1110
    assert ev("A^B", widths, {"A": 0b1100, "B": 0b1010}, 4) == 0b0110


def test_nested_ternary():
    # A ? B : (C ? D : E)
    widths = {"A": 1, "B": 2, "C": 1, "D": 2, "E": 2}
    text = "A?B:(C?D:E)"
    assert ev(text, widths, {"A": 1, "B": 0b11, "C": 0, "D": 0b01, "E": 0b10}, 2) == 0b11
    assert ev(text, widths, {"A": 0, "B": 0b11, "C": 1, "D": 0b01, "E": 0b10}, 2) == 0b01
    assert ev(text, widths, {"A": 0, "B": 0b11, "C": 0, "D": 0b01, "E": 0b10}, 2) == 0b10


# ───────────── 比较 / 移位 ─────────────
def test_comparison():
    assert ev("A==B", {"A": 3, "B": 3}, {"A": 5, "B": 5}, 1) == 1
    assert ev("A==B", {"A": 3, "B": 3}, {"A": 5, "B": 4}, 1) == 0
    assert ev("A!=B", {"A": 3, "B": 3}, {"A": 5, "B": 4}, 1) == 1


def test_shift():
    assert ev("A<<B", {"A": 4, "B": 2}, {"A": 1, "B": 2}, 4) == 0b0100
    assert ev("A>>B", {"A": 4, "B": 2}, {"A": 0b1000, "B": 2}, 4) == 0b0010


# ───────────── 位选 / 部分选 ─────────────
def test_bit_select():
    assert ev("A[0]", {"A": 4}, {"A": 0b1010}, 1) == 0
    assert ev("A[1]", {"A": 4}, {"A": 0b1010}, 1) == 1
    assert ev("A[3:2]", {"A": 4}, {"A": 0b1010}, 2) == 0b10


# ───────────── 变量分类 ─────────────
def test_classify_reserve():
    node = parse("(A?C:B)&(~J)")
    env = Env({"A": 1, "B": 1, "C": 1, "J": 1})
    control, data = classify_vars(node, env)
    assert set(control) == {"A", "J"}    # A=三元条件, J=门控
    assert set(data) == {"B", "C"}       # 分支数据


def test_classify_mux():
    node = parse("A?C:B")
    env = Env({"A": 1, "B": 3, "C": 3})
    control, data = classify_vars(node, env)
    assert control == ["A"]
    assert set(data) == {"B", "C"}


def test_classify_concat_all_data():
    node = parse("{A,B}")
    env = Env({"A": 2, "B": 3})
    control, data = classify_vars(node, env)
    assert control == []
    assert set(data) == {"A", "B"}


# ───────────── 错误处理 ─────────────
def test_unknown_char_raises():
    with pytest.raises(ExprError):
        parse("A @ B")


def test_unbound_var_width_raises():
    with pytest.raises(ExprError):
        eval_expr("A&B", {"A": 1}, {"A": 1, "B": 1}, 1)  # B 无宽度


def test_empty_expr_raises():
    with pytest.raises(ExprError):
        parse("   ")


# ───────────── 反解析 to_text（cone 展开链显示用） ─────────────
def test_to_text_roundtrip_equivalent():
    """parse(to_text(node)) 与原 node 求值等价（括号按优先级最小化但不丢语义）。"""
    cases = [
        ("A&B|C", {"A": 1, "B": 1, "C": 1}),
        ("A&(B|C)", {"A": 1, "B": 1, "C": 1}),
        ("(A?C:B)&(~J)", {"A": 1, "B": 1, "C": 1, "J": 1}),
        ("E?{B,C,D,1'b0}:{A,B,C,D}", {"A": 1, "B": 8, "C": 7, "D": 16, "E": 1}),
        ("A-(B-C)", {"A": 8, "B": 8, "C": 8}),
        ("A<<2", {"A": 8}),
        ("~(A&B)", {"A": 1, "B": 1}),
        ("{2{A}}", {"A": 4}),
        ("A[3:0]^B[1]", {"A": 8, "B": 2}),
    ]
    import itertools
    for text, widths in cases:
        node = parse(text)
        rendered = expr.to_text(node)
        node2 = parse(rendered)
        # 在多组输入取值下两棵 AST 求值一致
        names = sorted(widths)
        for combo in itertools.product(*[(0, (1 << widths[n]) - 1, 1) for n in names]):
            values = dict(zip(names, combo))
            v1 = expr.evaluate(node, Env(widths, values), 16)[0]
            v2 = expr.evaluate(node2, Env(widths, values), 16)[0]
            assert v1 == v2, "%s -> %s @ %s" % (text, rendered, values)


def test_to_text_rename():
    """rename 把字母换成真实信号名；不在映射里的保持原名。"""
    node = parse("E?{B,C}:A")
    out = expr.to_text(node, rename={"A": "pll_n2[31:0]", "B": "pll_n2[30:23]",
                                     "E": "en_dig_clk_div4"})
    assert out == "en_dig_clk_div4?{pll_n2[30:23],C}:pll_n2[31:0]"


def test_to_text_consts():
    """常量渲染：短常量二进制(1'b0)，宽常量十六进制。"""
    assert expr.to_text(parse("1'b0")) == "1'b0"
    assert expr.to_text(parse("4'b1010")) == "4'b1010"
    assert expr.to_text(parse("9'h1FF")) == "9'h1FF"


def test_to_text_part_node():
    """Part 节点(cone 代入产物)：子表达式取位段加括号，裸变量不加。"""
    part = expr.Part(parse("A?B:C"), 7, 0)
    assert expr.to_text(part) == "(A?B:C)[7:0]"
    part2 = expr.Part(expr.Var("X"), 3, 3)
    assert expr.to_text(part2) == "X[3]"
