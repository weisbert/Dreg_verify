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
        self.out_name = out_name        # K 列原文（含位宽如 [2:0]）
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
        # 是否被下游 logic 行以 <名>_to_logic 引用（read_logic 装载后回填；决定 RTL 网名后缀）
        self.to_logic_ref = False

    @property
    def is_top(self):
        """top_output==1：RTL/ENV_RF 顶层可见输出。"""
        return str(self.top_output).strip() in ("1", "1.0", "True", "true")

    @property
    def rtl_base(self):
        """RTL 真实网名(去位宽)。ls 行带 _ls 后缀；被下游引用的内部信号带 _to_logic 后缀。"""
        return rtl_net_name(self.out_base, self.suffix,
                            is_top=self.is_top, to_logic_ref=self.to_logic_ref)

    @property
    def rtl_name(self):
        """RTL 真实网名(含位宽切片)。assert 探针 LHS 用它，不能直接用 K 列原文。"""
        return self.rtl_base + self.out_name[len(self.out_base):]

    def __repr__(self):
        return "LogicSignal(R=%s, %s, expr=%r)" % (self.assert_id, self.out_name, self.expr)


class RegmapEntry:
    def __init__(self, signal, reg_name, reg_type, default, bit_lsb, bit_msb, owner,
                 address=None):
        self.signal = signal
        self.reg_name = reg_name
        self.reg_type = reg_type        # 原文（可能 RW/RO/R/W）
        self.default = default
        self.bit_lsb = bit_lsb
        self.bit_msb = bit_msb
        self.owner = owner
        self.address = address          # regmap H 列地址（'d13'→13），tmm 缺失时的兜底


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


class MuxCase:
    """mux 页的一行：一个 case 值 → 数据输入 的映射。"""

    def __init__(self, row, case_raw, input_raw, input_base, input_width,
                 input_msb, input_lsb):
        self.row = row                  # Excel 行号（便于回查）
        self.case_raw = case_raw        # F 列原文（如 3'b010 / 4'b000x，x=don't care 位）
        self.input_raw = input_raw      # A 列原文（含 _to_mux 后缀与位宽）
        self.input_base = input_base    # 剥位宽+_to_mux 后的基名 = 查 regmap/tmm 的 key
        self.input_width = input_width
        self.input_msb = input_msb
        self.input_lsb = input_lsb

    def __repr__(self):
        return "MuxCase(%s -> %s)" % (self.case_raw, self.input_base)


class MuxGroup:
    """mux 页的一组（同一个被验证输出）：G = case(B) { F行: A行; ... }

    与 LogicSignal 的本质区别：N 选 1 的 case 结构化列表，不是表达式 AST。
    属性接口尽量与 LogicSignal 对齐（out_name/out_base/out_width/owner/assert_id/
    rtl_base/rtl_name/is_top），方便 GUI/报告复用。
    """

    def __init__(self, group_no, out_name, out_width, ctrl_raw, ctrl_base, ctrl_width,
                 owner, top_output, cases):
        self.group_no = group_no        # N 列组号（assert_mux<N>_T<n> 的 N，用户拍板方案 A）
        self.out_name = out_name        # G 列原文（含位宽；顶层网名，直接探）
        out_base, _w, out_msb, out_lsb = _strip_width(out_name)
        self.out_base = out_base
        self.out_msb = out_msb          # G 列位宽切片（探针 LHS 重建用，比字符串索引可靠）
        self.out_lsb = out_lsb
        self.out_width = out_width
        self.ctrl_raw = ctrl_raw        # B 列原文（logic to_mux 行 K 名 + _to_mux + 位宽）
        self.ctrl_base = ctrl_base      # 剥位宽+_to_mux 的基名 = logic 页 to_mux 行的 K 基名
        self.ctrl_width = ctrl_width
        self.owner = owner              # L 列
        self.top_output = top_output    # I 列
        self.cases = cases              # list[MuxCase]
        # 与 LogicSignal 对齐的占位字段（mux 无 logic 后缀语义；expr 是只读属性=case 描述文本）
        self.suffix = "mux"

    @property
    def expr(self):
        """人读的 case 描述（GUI 表达式列 / 报告 / 搜索过滤用）。
        注意这不是可求值表达式——mux 不走 expr.parse 路径。"""
        items = "; ".join("%s:%s" % (c.case_raw, c.input_base) for c in self.cases)
        return "case(%s){%s}" % (self.ctrl_base, items)

    @property
    def is_top(self):
        """I 列 top_out==1（真表全 1）。"""
        return str(self.top_output).strip() in ("1", "1.0", "True", "true")

    @property
    def assert_id(self):
        """assert 标签前缀：assert_mux<N>_T<n>（mux 前缀防与 logic R 号撞号）。"""
        return "mux%s" % self.group_no

    @property
    def rtl_base(self):
        """mux 输出 RTL 网名 = G 列基名原样（2026-06-03 真表 scan_rtl 实证：7 个输出全在 DUT 顶层）。
        不走 logic 的 rtl_net_name 后缀变换（_ls/_to_logic）——套了反而 CUVUNF。"""
        return self.out_base

    @property
    def rtl_name(self):
        """含位宽切片的探针 LHS（assert 用）。
        用解析出的 msb/lsb 重建——不要用字符串索引拼接（G 列名含内部空格时会拼出非法 SV）。"""
        if self.out_msb is None or self.out_lsb is None:
            return self.rtl_base
        if self.out_msb == self.out_lsb:
            return "%s[%d]" % (self.rtl_base, self.out_msb)
        return "%s[%d:%d]" % (self.rtl_base, self.out_msb, self.out_lsb)

    def __repr__(self):
        return "MuxGroup(mux%s, %s, ctrl=%s, %d cases)" % (
            self.group_no, self.out_name, self.ctrl_base, len(self.cases))


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


def strip_to_mux(name):
    """去掉 _to_mux 后缀（mux 页 A/B 列都带；剥掉后才能查 regmap/tmm/logic K 名）。
    注意 strip_to_logic 不剥 _to_mux —— 两个后缀语义不同，分开两个函数。"""
    name = name.strip()
    for suf in ("_to_mux", "_TO_MUX"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def rtl_net_name(base, suffix, is_top=True, to_logic_ref=False):
    """K 列名 → RTL 真实网名。

    实证（2026-06-02 公司 lpbt_dig_top.v / BT_LP_DREG_sig_logic.v）：
      - M=ls 行：RTL 端口 = K 列名 + "_ls"（d_en_refbuf → d_en_refbuf_ls）
      - 内部信号(top_output=0)且被下游 logic 行以 <名>_to_logic 引用：
        RTL wire = K 列名 + "_to_logic"（pll_n1 → pll_n1_to_logic，
        在 BT_LP_DREG_sig_logic 模块内部，配合探针前缀即可验证）
      - 其余（to_logic/to_mux 顶层输出、未被引用的内部信号如 reserve）：K 列已是全名，原样用
    直接用 K 列名探针会 elaboration CUVUNF。
    """
    sfx = _s(suffix).lower()
    low = base.lower()
    if sfx == "ls" and not low.endswith("_ls"):
        return base + "_ls"
    if not is_top and to_logic_ref and not low.endswith("_to_logic"):
        return base + "_to_logic"
    return base


def parse_hex_addr(text):
    """地址解析。本 Excel 约定：tmm 用 'h' 前缀(hex，如 hD=13)，regmap 用 'd' 前缀(十进制，如 d13=13)。
    'h1'/'h2D'/'0x2D'/'d13'/'2D' → int；无法解析返回 None。"""
    t = _s(text)
    if t == "":
        return None
    low = t.lower()
    try:
        if low.startswith("0x"):
            return int(low, 16)
        if low.startswith("'h"):
            return int(low[2:], 16)
        if low.startswith("h"):
            return int(low[1:], 16)
        if low.startswith("d") and low[1:].isdigit():
            return int(low[1:], 10)        # 'd13' = 十进制 13（regmap 约定）
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

    # ── 标注"被下游以 <名>_to_logic 引用"的信号 ──
    # 实证(2026-06-02 BT_LP_DREG_sig_logic.v)：这类内部信号的 RTL wire 名 = K列名 + "_to_logic"
    # (pll_n1 → pll_n1_to_logic)。reserve 这类没被下游引用的内部信号 RTL 名 = K 原文。
    referenced = set()
    for sig in signals:
        for info in sig.inputs.values():
            raw_base = _strip_width(info["raw"])[0]
            if raw_base.lower().endswith("_to_logic"):
                referenced.add(info["base"].lower())
    for sig in signals:
        sig.to_logic_ref = sig.out_base.lower() in referenced
    return signals


# ───────────────────────────── 读取 mux 页 ─────────────────────────────
MUX_CTRL_LETTERS = list("BCDE")     # 控制信号 1..4（真表只用 B，C/D/E 预留全空）


def read_mux(ws, header_row=2):
    """读 mux 页 → list[MuxGroup]。

    真表排版（2026-06-03 inspect_mux 实证，54行×232列，表头第2行）：
      A=mux_input（寄存器字段+_to_mux）   B=mux_ctrl_sig1（logic to_mux 行 K 名+_to_mux）
      C/D/E=ctrl_sig2~4（预留全空）       F=case（控制值，可含 don't-care 位 x，如 4'b000x）
      G=mux_out（被验证输出，顶层网名）    I=top_out（全1）  L=Owner  N=组号
    语义：G = case(B) { F行: A行; ... }

    只保留 A/F/G 同时非空的行（'(reserved)' 行 F 为空被自然过滤）。
    分组按 **G 列基名全局归并**（同一输出的行即使不连续也归同一组）——
    劈成多组会导致 assert_id 撞号(非法 SV) + 互异值各自重启(撞值=假绿)。
    不依赖 N 列本身，防 read_only 模式下行尾留空导致 N 列读丢（232 列宽表的真实风险）。
    组号优先用首行 N 列值；N 列缺失/非法时顺延（上一组+1）。
    """
    groups = []
    by_base = {}        # G 基名(小写) -> MuxGroup（全局归并，非连续行也并入同一组）
    for ri, row in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True),
                             start=header_row + 1):
        in_raw = _s(_col(row, "A"))
        case_raw = _s(_col(row, "F"))
        out_raw = _s(_col(row, "G"))
        if in_raw == "" or case_raw == "" or out_raw == "":
            continue

        in_base_tm, in_width, in_msb, in_lsb = _strip_width(in_raw)
        case = MuxCase(
            row=ri, case_raw=case_raw, input_raw=in_raw,
            input_base=strip_to_mux(in_base_tm),
            input_width=in_width, input_msb=in_msb, input_lsb=in_lsb,
        )

        out_base, out_width, _, _ = _strip_width(out_raw)
        key = out_base.lower()
        if key in by_base:
            by_base[key].cases.append(case)
            continue

        # ── 新组 ──
        ctrl_raw = ""
        for letter in MUX_CTRL_LETTERS:         # 取第一个非空控制列（真表只有 B）
            v = _s(_col(row, letter))
            if v:
                ctrl_raw = v
                break
        ctrl_base_tm, ctrl_width, _, _ = _strip_width(ctrl_raw)
        n_raw = _s(_col(row, "N"))
        try:
            group_no = int(float(n_raw))
        except (ValueError, TypeError):
            group_no = groups[-1].group_no + 1 if groups else 1
        grp = MuxGroup(
            group_no=group_no,
            out_name=out_raw, out_width=out_width,
            ctrl_raw=ctrl_raw, ctrl_base=strip_to_mux(ctrl_base_tm), ctrl_width=ctrl_width,
            owner=_s(_col(row, "L")),
            top_output=_s(_col(row, "I")),
            cases=[case],
        )
        by_base[key] = grp
        groups.append(grp)

    # 组号冲突兜底：N 列读丢/重复导致两组同号时，后者顺延到未用号（撞号=非法 SV 标签）
    seen_no = set()
    for grp in groups:
        while grp.group_no in seen_no:
            grp.group_no += 1
        seen_no.add(grp.group_no)
    return groups


# ───────────────────────────── 读取 regmap 页 ─────────────────────────────
def read_regmap(ws, header_row=2):
    """返回 dict: signal_name -> RegmapEntry。bit 位置由 J..Y(=bit15..bit0) 哪列非空推出。"""
    out = {}
    # J..Y 共 16 列，分别代表 bit15..bit0
    bit_cols = [openpyxl.utils.get_column_letter(c) for c in
                range(column_index_from_string("J"), column_index_from_string("Y") + 1)]
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        raw_signal = _s(_col(row, "G"))
        if raw_signal == "":
            continue
        # Signal_Name 带位宽如 int_n[8:0]，去掉 [msb:lsb] 后才能与 logic 输入基名匹配
        signal, _w, _m, _l = _strip_width(raw_signal)
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
            address=parse_hex_addr(_col(row, "H")),   # regmap H 列 = 地址（'d13'）
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
            # 字段行。字段名(A列)带位宽标注如 int_n[8:0] / d_pfd_en_lnmode[1:0]，
            # 去掉 [msb:lsb] 后才能与 logic 输入基名(已去位宽)匹配。
            name, _w, _m, _l = _strip_width(a)
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
    def __init__(self, logic, regmap, tmm, sheet_names, mux=None):
        self.logic = logic              # list[LogicSignal]
        self.regmap = regmap            # dict
        self.tmm = tmm                  # dict
        self.sheet_names = sheet_names
        self.mux = mux if mux is not None else []   # list[MuxGroup]（无 mux 页 = 空列表）


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
    ws_mux = _find_sheet(wb, "mux")

    logic = read_logic(ws_logic, logic_header_row)
    regmap = read_regmap(ws_regmap, regmap_header_row) if ws_regmap is not None else {}
    tmm = read_tmm(ws_tmm) if ws_tmm is not None else {}
    mux = read_mux(ws_mux) if ws_mux is not None else []
    names = list(wb.sheetnames)
    wb.close()
    return DregWorkbook(logic, regmap, tmm, names, mux=mux)
