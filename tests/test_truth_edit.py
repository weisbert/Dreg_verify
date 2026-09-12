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


def test_parse_int_round_trips_editor_cell_format():
    """编辑器显示格式(generator._fmt_cell)必须能被原样读回——否则复制粘贴一格就变值。"""
    from dreg_verify import generator
    for val, width in [(0, 1), (1, 1), (5, 3), (0xA, 4), (0x3F, 6), (0x1234, 16)]:
        assert TE.parse_int(generator._fmt_cell(val, width)) == val
