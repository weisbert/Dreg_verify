# -*- coding: utf-8 -*-
"""真值表编辑层的【纯逻辑】——不依赖 Qt，新门面(SignalView)与『排查(旧)』共用同一份实现。

放这里的东西只有三类（都是此前两套门面各写一遍、语义已经漂移的）：
  ① 用户输入的数值写法解析（16'hA / 0x.. / hA / 'b.. / 'd.. / 0b.. / 十进制 / 裸 hex）；
  ② 测试列名的生成 / 清洗 / 校验（唯一性要查【全集】，包括 _NEG 后缀的列）；
  ③ 加负向时"哪些列该造、哪些已有负向要跳过"的判定。

不碰 Qt、不碰 Excel、不碰生成器：可以直接单测（tests/test_truth_edit.py），GUI 重做后照样复用。
"""

import re

__all__ = ["parse_int", "parse_cell"]


# ───────────────────────── ① 数值写法 ─────────────────────────
_RE_TICK_H = re.compile(r"'h([0-9a-f]+)$")
_RE_TICK_B = re.compile(r"'b([01]+)$")
_RE_TICK_D = re.compile(r"'d(\d+)$")
_RE_DEC = re.compile(r"\d+$")
_RE_HEX = re.compile(r"[0-9a-f]+$")


def parse_int(s):
    """宽松解析用户在真值表里填的数值，支持 IC 工程师常写的 8 种写法：

        16'hA / 'hA（带位宽的 Verilog 十六进制）      → 10
        'b1010 / 0b1010（二进制）                     → 10
        'd9（十进制）                                  → 9
        0xA / hA（十六进制前缀）                       → 10
        9（十进制）/ ab（裸十六进制）

    空串 = 0（与「这一格没填」由调用方用 parse_cell 区分）。
    识别不了 → 抛 ValueError（**绝不返回 0/None 静默吞值**，调用方必须让用户看见）。
    """
    t = str(s).strip().lower().replace(" ", "")
    if t == "":
        return 0
    if t in ("0x", "0b"):        # 只有前缀没数字 = 打了一半，别让裸 hex 兜底把 "0b" 读成 11
        raise ValueError("只有前缀没有数值: %r" % s)
    m = _RE_TICK_H.search(t)
    if m:
        return int(m.group(1), 16)
    m = _RE_TICK_B.search(t)
    if m:
        return int(m.group(1), 2)
    m = _RE_TICK_D.search(t)
    if m:
        return int(m.group(1), 10)
    if t.startswith("0b") and t[2:] and set(t[2:]) <= {"0", "1"}:
        return int(t, 2)         # 仅当 0b 后全是 0/1 才当二进制；'0bc' 之类仍按裸 hex（向后兼容）
    if t.startswith("0x") and t[2:]:
        return int(t, 16)
    if t.startswith("h") and t[1:]:
        return int(t[1:], 16)
    if _RE_DEC.match(t):
        return int(t, 10)
    if _RE_HEX.match(t):
        return int(t, 16)
    raise ValueError("无法识别 %r（用 0x.. 或 16'h.. 或纯数字）" % s)


def parse_cell(s):
    """真值表单元格：空/全空白 = 这一格没填 → None；其余同 parse_int（非法照样抛 ValueError）。"""
    if str(s or "").strip() == "":
        return None
    return parse_int(s)
