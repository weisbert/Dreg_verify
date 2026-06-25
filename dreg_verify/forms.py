# -*- coding: utf-8 -*-
"""形态分类(classify)——统一流水线的 classify 层（重构 S0，2026-06-25）。

设计基线：覆盖方案挂在【展开后表达式的形态】上，不挂"信号来自哪种门/哪一页"。
本模块把展开后 AST 按【结构】分成 5 种形态(F0..F4)；cover 层据形态派发覆盖算法。
纯函数、只依赖 expr，**不读 wb、不看来源页**（gate 信息由调用方/dft_gate_info 提供）。
形态↔覆盖映射见 docs/Topout统一流水线重构设计稿_20260625.md ②。

S0：本模块只新增、不接进任何产出路径（byte-identical 天然成立）；S1/S2 才逐步接入。
"""
from . import expr as E

# ───────────── 5 种形态(F0..F4) ─────────────
REGISTER = "register"   # F0 直连寄存器：根 = 单 Var 叶子（寄存器字段透传）
BOOLEAN = "boolean"     # F1 纯布尔/位运算：根 = Binary/Unary/Concat/Repeat/Part
SELECT = "select"       # F2 选路：根 = Ternary(控制选择) 或 mux 根(node=None)
GATED = "gated"         # F3/F4 门控：dft 页 iddq 门包在外层；inner = 内层形态
KINDS = (REGISTER, BOOLEAN, SELECT, GATED)


class Shape:
    """一个信号展开后的形态。
    GATED 时 inner=内层形态（习惯：inner∈{F0,F1}=F3 简单门控 / inner=F2=F4 门控套选路）。"""

    def __init__(self, kind, inner=None, expandable=None, control=None, data=None, gate=None):
        self.kind = kind
        self.inner = inner                # GATED：剥掉门后的内层 Shape
        self.expandable = expandable      # SELECT：mux 可否展开(来自 mux_gen.mux_expandable)；None=未知
        self.control = list(control or [])
        self.data = list(data or [])
        self.gate = gate                  # GATED：门信息 dict {gate_base, transparent, ...}

    @property
    def is_gated(self):
        return self.kind == GATED

    @property
    def base_kind(self):
        """剥掉门后的底层形态(GATED→inner.kind；否则=自身)。"""
        return self.inner.base_kind if (self.kind == GATED and self.inner) else self.kind

    def __repr__(self):
        if self.kind == GATED:
            return "Shape(GATED→%r)" % (self.inner,)
        suf = ("/可展" if self.expandable else ("/不可展" if self.expandable is False else ""))
        return "Shape(%s%s)" % (self.kind, suf)


def dft_gate_info(wb, out_base):
    """该输出在 dft 页是否被 iddq 门控 → 门信息 dict 或 None（读 wb.dft，excel_model.read_dft）。"""
    if not out_base:
        return None
    g = (getattr(wb, "dft", None) or {}).get(str(out_base).strip().lower())
    return dict(g) if g else None


def classify(node, bindings=None, gate=None, expandable=None):
    """展开后 AST → Shape。

    gate（门信息 dict 或 None）：S0/S1 由调用方据 dft 页给（dft_gate_info）；S2 后门折进 AST 也兼容。
    expandable：SELECT 的 mux 可展性（mux_gen.mux_expandable），仅 SELECT 用。
    纯结构判定：gate 优先 → mux 根(None) → 单 Var → 顶层 Ternary(选路) → 其余(布尔)。"""
    if gate is not None:                       # 外层门控 → 剥门递归内层
        inner = classify(node, bindings, None, expandable)
        return Shape(GATED, inner=inner, gate=gate, control=inner.control, data=inner.data)
    if node is None:                           # mux 根：用 expansion、无单一 AST
        return Shape(SELECT, expandable=expandable)
    if isinstance(node, E.Var):                # 单叶子 = 直连寄存器透传
        return Shape(REGISTER)
    try:
        ctrl, data = E.classify_vars(node, None)
    except Exception:                          # noqa: BLE001 —— 分类绝不崩，退布尔
        ctrl, data = [], []
    if isinstance(node, E.Ternary):            # 顶层选路（classify_vars 已把 cond 归 control）
        return Shape(SELECT, expandable=expandable, control=ctrl, data=data)
    return Shape(BOOLEAN, control=ctrl, data=data)   # Binary/Unary/Concat/Repeat/Part
