# -*- coding: utf-8 -*-
"""
excel_model.py — 读取 Dreg 核心 Excel 的 logic / regmap / total_memory_map 三页，
构建生成器所需的内存数据结构。

约定（来自对真实 Excel 的结构分析，表头多在第 2 行）：
  logic 页：A..J 列 = 输入信号（变量字母 = 列字母），K=输出名，L=表达式，M=类型(suffix),
            N=top_output, O=Notes, P=owner, R=序号(assert id)
  regmap 页：D=Reg_Name, F=Reg Type, G=Signal_Name, I=default, J..Y=bit15..bit0, Z=suffix, AE=owner
  total_memory_map 页：无表头、寄存器块结构；字段行 A=字段名 B=Bit Field(8 或 15:7)
            C=复位 D=DIG TOP PIN(Y/N) E=功能 F=该字段所属寄存器地址(hex) H=类型(RO/RW)
"""

import re

try:
    import openpyxl
    from openpyxl.utils import column_index_from_string
except ImportError:  # pragma: no cover
    raise SystemExit("缺少 openpyxl，请先运行:  pip install openpyxl")


# ───────────────────────────── 数据结构 ─────────────────────────────
class LogicSignal:
    """logic 页的一行：一个被验证输出信号。"""

    def __init__(self, row, out_name, out_width, expr, suffix, top_output,
                 notes, owner, assert_id, inputs):
        self.row = row                  # Excel 行号（便于回查）
        self.out_name = out_name        # K 列原文（assert LHS 直接用，含位宽如 [2:0]）
        self.out_base = _strip_width(out_name)[0]
        self.out_width = out_width       # 解析出的输出位宽
        self.expr = expr                 # L 列表达式
        self.suffix = suffix             # M 列类型
        self.top_output = top_output     # N 列
        self.notes = notes               # O 列
        self.owner = owner               # P 列
        self.assert_id = assert_id       # R 列序号
        # inputs: dict 字母 -> {'raw','base','width','msb','lsb'}
        self.inputs = inputs

    def __repr__(self):
        return "LogicSignal(R=%s, %s, expr=%r)" % (self.assert_id, self.out_name, self.expr)


class RegmapEntry:
    def __init__(self, signal, reg_name, reg_type, default, bit_lsb, bit_msb, owner):
        self.signal = signal
        self.reg_name = reg_name
        self.reg_type = reg_type        # 原文（可能 RW/RO/R/W）
        self.default = default
        self.bit_lsb = bit_lsb
        self.bit_msb = bit_msb
        self.owner = owner


class TmmField:
    def __init__(self, name, bit_msb, bit_lsb, address, reg_type, dig_top_pin, reg_name,
                 reg_type_raw=""):
        self.name = name
        self.bit_msb = bit_msb
        self.bit_lsb = bit_lsb
        self.address = address          # int
        self.reg_type = reg_type        # 归一化后 'RO'/'RW'/None
        self.reg_type_raw = reg_type_raw  # H 列原文（诊断用）
        self.dig_top_pin = dig_top_pin  # 'Y'/'N'/None
        self.reg_name = reg_name        # 所属寄存器名（B 列里寄存器行给出）


# ───────────────────────────── 工具 ─────────────────────────────
def _s(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


_WIDTH_RANGE = re.compile(r"\[(\d+)\s*:\s*(\d+)\]\s*$")
_WIDTH_BIT = re.compile(r"\[(\d+)\]\s*$")


def _strip_width(text):
    """返回 (去掉位宽后的名字, width, msb, lsb)。无位宽则 width=1, msb=lsb=None。"""
    text = _s(text)
    m = _WIDTH_RANGE.search(text)
    if m:
        msb, lsb = int(m.group(1)), int(m.group(2))
        return text[:m.start()].strip(), abs(msb - lsb) + 1, msb, lsb
    m = _WIDTH_BIT.search(text)
    if m:
        b = int(m.group(1))
        return text[:m.start()].strip(), 1, b, b
    return text, 1, None, None


def strip_to_logic(name):
    """去掉 _to_logic 后缀（rule 2：输入 wire 名 = 输入名去 _to_logic）。"""
    name = name.strip()
    for suf in ("_to_logic", "_TO_LOGIC"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def parse_hex_addr(text):
    """'h1'/'h2D'/'0x2D'/'2D'/十进制 → int；无法解析返回 None。"""
    t = _s(text)
    if t == "":
        return None
    low = t.lower()
    try:
        if low.startswith("0x"):
            return int(low, 16)
        if low.startswith("h"):
            return int(low[1:], 16)
        if low.startswith("'h"):
            return int(low[2:], 16)
        # 纯数字：可能是十进制行号也可能是 hex；按 hex 解释更贴近寄存器地址语义
        if re.fullmatch(r"[0-9a-fA-F]+", t):
            return int(t, 16)
    except ValueError:
        return None
    return None


def parse_bitfield(text):
    """'8' → (8,8)；'15:7' → (15,7)；无法解析返回 (None,None)。"""
    t = _s(text).replace(" ", "")
    m = re.fullmatch(r"(\d+):(\d+)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    if re.fullmatch(r"\d+", t):
        return int(t), int(t)
    return None, None


def _col(row, letter):
    """按列字母取值（row 是 values_only 的 tuple，0-based）。"""
    idx = column_index_from_string(letter) - 1
    return row[idx] if idx < len(row) else None


# ───────────────────────────── 读取 logic 页 ─────────────────────────────
LOGIC_INPUT_LETTERS = list("ABCDEFGHIJ")   # A..J


def read_logic(ws, header_row=2):
    """返回 list[LogicSignal]。只保留 K(输出名) 与 L(表达式) 同时非空的行。"""
    signals = []
    for ri, row in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
        out_raw = _s(_col(row, "K"))
        expr = _s(_col(row, "L"))
        if out_raw == "" or expr == "":
            continue
        out_base, out_width, _, _ = _strip_width(out_raw)
        inputs = {}
        for letter in LOGIC_INPUT_LETTERS:
            cell = _s(_col(row, letter))
            if cell == "":
                continue
            base_with_tl, width, msb, lsb = _strip_width(cell)
            base = strip_to_logic(base_with_tl)
            inputs[letter] = {
                "raw": cell, "base": base, "width": width, "msb": msb, "lsb": lsb,
            }
        signals.append(LogicSignal(
            row=ri,
            out_name=out_raw,
            out_width=out_width,
            expr=expr,
            suffix=_s(_col(row, "M")),
            top_output=_s(_col(row, "N")),
            notes=_s(_col(row, "O")),
            owner=_s(_col(row, "P")),
            assert_id=_s(_col(row, "R")),
            inputs=inputs,
        ))
    return signals


# ───────────────────────────── 读取 regmap 页 ─────────────────────────────
def read_regmap(ws, header_row=2):
    """返回 dict: signal_name -> RegmapEntry。bit 位置由 J..Y(=bit15..bit0) 哪列非空推出。"""
    out = {}
    # J..Y 共 16 列，分别代表 bit15..bit0
    bit_cols = [openpyxl.utils.get_column_letter(c) for c in
                range(column_index_from_string("J"), column_index_from_string("Y") + 1)]
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        signal = _s(_col(row, "G"))
        if signal == "":
            continue
        occupied = []
        for offset, col in enumerate(bit_cols):
            val = _s(_col(row, col))
            if val not in ("", "0"):
                occupied.append(15 - offset)   # J=bit15 ... Y=bit0
        if occupied:
            bit_msb, bit_lsb = max(occupied), min(occupied)
        else:
            bit_msb = bit_lsb = None
        out[signal] = RegmapEntry(
            signal=signal,
            reg_name=_s(_col(row, "D")),
            reg_type=_s(_col(row, "F")),
            default=_s(_col(row, "I")),
            bit_lsb=bit_lsb,
            bit_msb=bit_msb,
            owner=_s(_col(row, "AE")),
        )
    return out


# ───────────────────────────── 读取 total_memory_map 页 ─────────────────────────────
def read_tmm(ws):
    """
    状态机解析：跳过 # / 'Register Name' / 'Field Name' 标题行；
    字段行判定 = B 像 bit 号/范围 且 F 像 hex 地址。
    返回 dict: field_name -> TmmField（同名取首个；地址不同会各保留，键带后缀）。
    """
    fields = {}
    cur_reg_name = ""
    for row in ws.iter_rows(values_only=True):
        a = _s(_col(row, "A"))
        b = _s(_col(row, "B"))
        f = _s(_col(row, "F"))
        if a == "":
            continue
        low = a.lower()
        if low.startswith("#") or low in ("register name", "field name"):
            continue
        # 寄存器定义行：A=寄存器名, B=地址hex, D=类型, 记录当前寄存器名
        reg_addr = parse_hex_addr(b)
        bit_msb, bit_lsb = parse_bitfield(b)
        field_addr = parse_hex_addr(f)
        if bit_msb is not None and field_addr is not None:
            # 字段行
            name = a
            pin = _s(_col(row, "D")).upper()
            dig = "Y" if pin in ("Y", "YES") else ("N" if pin in ("N", "NO") else None)
            raw_type = _s(_col(row, "H"))
            entry = TmmField(
                name=name,
                bit_msb=bit_msb,
                bit_lsb=bit_lsb,
                address=field_addr,
                reg_type=_normalize_type(raw_type),
                reg_type_raw=raw_type,
                dig_top_pin=dig,        # 精确匹配，避免 'NA'/'N/A' 被首字符截断误判为 'N'
                reg_name=cur_reg_name,
            )
            # 同名字段：保留首个，但记录所有以便诊断
            fields.setdefault(name, entry)
        elif reg_addr is not None:
            # 寄存器定义行
            cur_reg_name = a
    return fields


def _normalize_type(v):
    """归一化寄存器类型为 RO/RW。基于关键词，兼容 RW/RO/R/W/R/W/READ WRITE/READ ONLY/WO 等写法。"""
    t = _s(v).upper()
    if not t:
        return None
    has_w = ("W" in t) or ("WRITE" in t)   # RW/W/WO/W/O/READ WRITE/...
    has_r = ("R" in t) or ("READ" in t)
    if has_w:                              # 可写（含只写 WO）→ 按 RW 处理
        return "RW"
    if has_r:                              # 只读
        return "RO"
    return t or None


# ───────────────────────────── 顶层装载 ─────────────────────────────
class DregWorkbook:
    def __init__(self, logic, regmap, tmm, sheet_names):
        self.logic = logic              # list[LogicSignal]
        self.regmap = regmap            # dict
        self.tmm = tmm                  # dict
        self.sheet_names = sheet_names


def _find_sheet(wb, *candidates):
    lowered = {s.lower(): s for s in wb.sheetnames}
    for c in candidates:
        if c.lower() in lowered:
            return wb[lowered[c.lower()]]
    return None


def load_workbook(path, logic_header_row=2, regmap_header_row=2):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws_logic = _find_sheet(wb, "logic")
    if ws_logic is None:
        wb.close()
        raise SystemExit("Excel 中找不到 'logic' 页；现有页：%s" % wb.sheetnames)
    ws_regmap = _find_sheet(wb, "regmap")
    ws_tmm = _find_sheet(wb, "total_memory_map", "total memory map", "memory_map")

    logic = read_logic(ws_logic, logic_header_row)
    regmap = read_regmap(ws_regmap, regmap_header_row) if ws_regmap is not None else {}
    tmm = read_tmm(ws_tmm) if ws_tmm is not None else {}
    names = list(wb.sheetnames)
    wb.close()
    return DregWorkbook(logic, regmap, tmm, names)
