# -*- coding: utf-8 -*-
"""inspect_vba.py 的 MS-OVBA 解压回归测试（spec 对抗式校验后修复的几条）。"""

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tools"))   # inspect_vba 已挪进 tools/（2026-06-24 整理）

import inspect_vba as V  # noqa: E402


def test_decompress_literals():
    assert V._ovba_decompress(bytes([0x01, 0x05, 0xB0, 0x00, 0x48, 0x65, 0x6C, 0x6C, 0x6F]), 0) == b"Hello"


def test_decompress_copytoken():
    assert V._ovba_decompress(bytes([0x01, 0x05, 0xB0, 0x08, 0x61, 0x62, 0x63, 0x00, 0x20]), 0) == b"abcabc"


def test_reject_bad_signature():
    # header sig 位 != 0b011 应被拒（避免暴力扫描把 p-code 误解压成垃圾）
    with pytest.raises(ValueError):
        V._ovba_decompress(bytes([0x01, 0x05, 0x00, 0x00, 0x41]), 0)


def test_copytoken_boundary_truncated_token_discarded():
    # 合规 2-chunk：chunk1 末尾 CopyToken 只剩 1 字节(残缺)应丢弃，不得跨 chunk 读下一头
    blob = bytes([0x01, 0x02, 0xB0, 0x02, 0x5A, 0x0F, 0x01, 0xB0, 0x00, 0x51])
    assert V._ovba_decompress(blob, 0) == b"ZQ"


def test_signature_rejects_most_random_0x01():
    import random
    random.seed(1)
    g = bytes(random.randrange(256) for _ in range(20000))
    ok = 0
    for i, b in enumerate(g):
        if b == 1:
            try:
                V._ovba_decompress(g, i)
                ok += 1
            except Exception:  # noqa: BLE001
                pass
    n01 = g.count(1)
    assert n01 > 20            # 确实有不少 0x01
    assert ok < n01 // 4       # 签名校验后绝大多数被拒（旧版几乎全过）


def test_codepage_to_encoding():
    assert V._codepage_to_encoding(936) == "cp936"
    assert V._codepage_to_encoding(65001) == "utf-8"
    assert V._codepage_to_encoding(1252) == "cp1252"
