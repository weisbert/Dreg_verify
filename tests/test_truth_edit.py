# -*- coding: utf-8 -*-
"""truth_edit 纯逻辑单测（不需要 Qt/Excel）：数值写法 / 列名 / 加负向挑列。

这层是新门面(SignalView)与『排查(旧)』共用的编辑逻辑——两边此前各写一遍、语义已经漂移
（新门面只认 4 种数值写法、列名唯一性只查 U<n>）。GUI 重做后这份单测照样有效。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                       # noqa: E402

from dreg_verify import truth_edit as TE            # noqa: E402


# ───────────────────────── ① 数值写法 ─────────────────────────
@pytest.mark.parametrize("text,want", [
    ("16'hA", 10), ("8'h3f", 0x3F), ("'hA", 10),      # 带位宽/不带位宽的 Verilog 十六进制
    ("'b101", 5), ("4'b1010", 10), ("0b101", 5),      # 二进制
    ("'d9", 9), ("16'd257", 257),                     # 显式十进制
    ("0xA", 10), ("0X1f", 0x1F), ("hA", 10), ("H3", 3),
    ("9", 9), ("257", 257),                           # 纯十进制
    ("ab", 0xAB), ("0bc", 0xBC),                      # 裸十六进制（0bc 不是二进制）
    (" 16'h 3 ", 3),                                  # 空格无关
    ("", 0),                                          # 空 = 0（"没填"由 parse_cell 区分）
])
def test_parse_int_eight_notations(text, want):
    assert TE.parse_int(text) == want


@pytest.mark.parametrize("text", ["", "0x", "0b", "'h", "'b", "h", "xyz", "12g", "0x-1", "-3"])
def test_parse_int_rejects_garbage_loudly(text):
    """识别不了必须抛 ValueError——绝不返回 0/None 让调用方静默吞值（空串除外，那是 0）。"""
    if text == "":
        assert TE.parse_int(text) == 0
        return
    with pytest.raises(ValueError):
        TE.parse_int(text)


def test_parse_cell_distinguishes_empty_from_zero():
    """单元格语义：空 = 这一格没填(None)，"0" = 填了 0。"""
    assert TE.parse_cell("") is None
    assert TE.parse_cell("   ") is None
    assert TE.parse_cell("0") == 0
    with pytest.raises(ValueError):
        TE.parse_cell("nope")


def _c(neg=False, **vals):
    return {"neg": neg, "vals": vals}


# ───────────────────────── ② 列名 ─────────────────────────
def test_uniq_col_name_scans_the_whole_set_including_neg():
    """唯一性查全集：只查 U<n> 是"批量加负向全撞成 U0_NEG"的根因。"""
    assert TE.uniq_col_name(["T0", "T1"]) == "U0"
    assert TE.uniq_col_name(["T0", "U0"]) == "U1"
    assert TE.uniq_col_name(["T0", "U0_NEG"], suffix="_NEG") == "U1_NEG"
    assert TE.uniq_col_name(["u0_neg"], suffix="_NEG") == "U1_NEG"      # 大小写无关
    names = set()
    for _ in range(5):                                                   # 连造 5 条：条条不同
        n = TE.uniq_col_name(names, suffix="_NEG")
        assert n not in names
        names.add(n)
    assert len(names) == 5


def test_sanitize_and_reserved_names():
    assert TE.sanitize_name(" a-b c ") == "a_b_c"
    assert TE.sanitize_name("中文") == "__"
    assert TE.is_reserved_test_name("T3") and TE.is_reserved_test_name("t12_NEG")
    assert not TE.is_reserved_test_name("TT3") and not TE.is_reserved_test_name("U0")
    assert TE.final_col_name("MY", negative=True) == "MY_NEG"
    assert TE.final_col_name("MY_NEG", negative=True) == "MY_NEG"        # 不重复加
    assert TE.final_col_name("MY", negative=False) == "MY"


@pytest.mark.parametrize("name,others,neg,ok", [
    ("MY_CASE", ["T0", "U0"], False, True),
    ("   ", ["T0"], False, False),                    # 空
    ("中文", ["T0"], False, True),                     # 清成 "__"，非空 → 放行
    ("T3", ["T0"], False, False),                     # 撞自动命名保留名
    ("t3_neg", ["T0"], False, False),
    ("U0", ["T0", "U0"], False, False),               # 与其它列重名
    ("U0", ["T0", "U0_NEG"], True, False),            # 负向最终名 U0_NEG 与其它列重名
    ("U0", ["T0", "U0_NEG"], False, True),            # 正向 U0 不与 U0_NEG 冲突
])
def test_check_col_name(name, others, neg, ok):
    got, info = TE.check_col_name(name, others, negative=neg)
    assert got is ok, info


# ───────────────────────── ③ 加负向挑列 ─────────────────────────
def test_plan_negatives_only_positive_columns():
    """负向列不再套负向——此前『全选 12 列点一次』会 12→24→48。"""
    cols = [_c(a=0), _c(a=1), _c(neg=True, a=0)]
    targets, skipped = TE.plan_negatives(cols, sel=[0, 1, 2])
    assert 2 not in targets                       # 第 3 列是负向，不做源
    assert targets == [1] and skipped == 1        # 第 1 列取值已有负向 → 跳过


def test_plan_negatives_dedupes_same_input_values():
    cols = [_c(a=0), _c(a=0), _c(a=1)]            # 前两列输入取值相同
    targets, skipped = TE.plan_negatives(cols, sel=[0, 1, 2])
    assert targets == [0, 2] and skipped == 1


def test_plan_negatives_falls_back_to_first_positive():
    cols = [_c(neg=True, a=0), _c(a=1)]
    assert TE.plan_negatives(cols, sel=[])[0] == [1]      # 未选 → 首条正向
    assert TE.plan_negatives(cols, sel=[0])[0] == [1]     # 只选中负向 → 也退回首条正向
    assert TE.plan_negatives([_c(neg=True, a=0)], sel=[]) == ([], 0)   # 无正向 → 空


def test_plan_negatives_all_positive():
    cols = [_c(a=0), _c(a=1), _c(neg=True, a=0), _c(a=2)]
    targets, skipped = TE.plan_negatives(cols, all_positive=True)
    assert targets == [1, 3] and skipped == 1     # 全部正向里，a=0 那条已有负向


def test_parse_int_round_trips_editor_cell_format():
    """编辑器显示格式(generator._fmt_cell)必须能被原样读回——否则复制粘贴一格就变值。"""
    from dreg_verify import generator
    for val, width in [(0, 1), (1, 1), (5, 3), (0xA, 4), (0x3F, 6), (0x1234, 16)]:
        assert TE.parse_int(generator._fmt_cell(val, width)) == val
