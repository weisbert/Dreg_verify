# -*- coding: utf-8 -*-
"""
cone.py — Cone 展开：当一个 logic 输出的输入引用其它 logic 行的输出时，
递归把那个行的表达式代入(AST 级)，直到所有叶子输入都是真实寄存器/可驱动信号。

两类输入会被展开（统称"可展开输入"）：
  ① logic-internal  内部信号(top_output=0)，如 pll_n1/pll_n2——没有输出端口，不展开就没法验
  ② logic-computed  上游计算网(top_output=1 但不自引用)，如 d_wl_wur_bt_pll_mode_sel_to_logic——
                     『展开上游』级联模式(默认)下展开；『force级联网』模式下改为直接 force 该网

实证依据（2026-06-02，Hi1108 真表 pll_n）：for_test(VBA) 对 pll_n[31:0] 的做法——
    pll_n  = en_dig_clk_div4 ? pll_n2<<1 : pll_n2          (logic row5)
    pll_n2 = en_dig_clk_div2 ? pll_n1<<1 : pll_n1          (logic row4, 内部)
    pll_n1 = xo_sel ? {1'b0,int_n,frac_msb,frac_lsb[15:1]}
                    : {int_n,frac_msb,frac_lsb}             (logic row3, 内部)
最终驱动叶子寄存器 int_n/frac_n_msb/frac_n_lsb/d_xo_freq_sel/en_dig_clk_div2/div4
（同地址字段合并 RF_WRITE），断言顶层输出 pll_n。

展开结果：
  node     — 复合 AST（被展开信号已代入；切片用 expr.Part 表达）
  bindings — {叶子大写基名: InputBinding}（驱动叶子寄存器；同寄存器多切片共享一个叶子）
之后向量生成/.sv 渲染照常使用，无需感知 cone。
"""

from . import expr as E


class ConeError(Exception):
    pass


MAX_DEPTH = 8

# 会被 cone 展开的输入来源标记（见模块头注释）
EXPANDABLE = ("logic-internal", "logic-computed")


def _is_mux_out_binding(b, resolver):
    """该输入是否是某 mux 组的输出——【即便用户给那根衔接网配了探针前缀】也认得出。

    背景(2026-06-16 d_wl_rf_lo2g5g_lcbufc0_bias_en 实证)：resolver 给 mux 输出标 found_in=
    'mux-output'(resolver.py:348)，但若用户为该衔接网【配了探针前缀】，resolver 会把它重标成
    'prefixed-wire'(resolver.py:364)。R38 跨边界展开只认 found_in=='mux-output' → 【配了前缀就
    停止展开】=bug：cone 模式本就要把 mux 展成源寄存器、前缀反而多余(展开后根本不 force 那根网)。
    故识别 mux 输出要回看 base 是否真是 mux 组输出，不能只认 found_in。
    """
    if b is None or not getattr(b, "base", None):
        return False
    if b.found_in == "mux-output":
        return True
    if b.found_in == "prefixed-wire" and resolver is not None:
        return str(b.base).lower() in getattr(resolver, "_mux_outputs", {})
    return False


def find_internal_inputs(node, bindings, resolver=None):
    """返回表达式里"可展开输入"的变量字母列表。空 = 不需要 cone 展开。

    传入 resolver 且 cascade_mode=='cone'（展开模式，默认=对齐 VBA）时，mux 输出也算可展开
    （跨 logic↔mux 边界，让 expand 把该 mux 合成等价 AST 代入并递归）；force 模式或 resolver
    缺省 → 只认 logic 上游(EXPANDABLE)，与历史逐字节相同。"""
    expand_mux = (resolver is not None
                  and getattr(resolver, "cascade_mode", "cone") == "cone")
    out = []
    for ltr in E.collect_vars(node):
        b = bindings.get(ltr)
        if b is None:
            continue
        if b.found_in in EXPANDABLE or (expand_mux and _is_mux_out_binding(b, resolver)):
            out.append(ltr)
    return out


def expand(sig, wb, resolver, _depth=0, _stack=None, chain_out=None):
    """递归展开 sig。返回 (node, bindings)。

    node:     复合 AST，所有内部信号引用已替换为其表达式（带 Part 切片）。
    bindings: {叶子大写基名: InputBinding}，全部为可驱动输入。
              每个叶子带 xl_letters 属性 = Excel 来源坐标（根行直接输入=列字母 "E"；
              上游行展开来的叶子="上游行名.字母" 如 "pll_n1.A"），供 GUI/报告显示溯源——
              展开后表达式变量名是大写基名，没有这个就对不回 Excel 的 A/B/C 列了。
    chain_out: 传入 list 时，把『展开链』按 DFS 顺序追加进去（GUI/报告显示用）：
              [{"out": 行基名, "expr": Excel 原式, "subst": 字母代入真实信号名的等价形式}, ...]
              首项=本行(根)，同一上游行被多次到达只记一次。

    循环引用 / 超深 / 找不到内部信号定义行 / 子表达式解析失败 → 抛 ConeError。
    """
    stack = list(_stack or [])
    me = sig.out_base.lower()
    if me in stack:
        raise ConeError("内部信号循环引用: %s" % " → ".join(stack + [me]))
    if _depth > MAX_DEPTH:
        raise ConeError("cone 展开深度超过 %d（链: %s）" % (MAX_DEPTH, " → ".join(stack + [me])))
    stack.append(me)

    try:
        node = E.parse(sig.expr)
    except E.ExprError as ex:
        raise ConeError("信号 %r 表达式解析失败: %s" % (sig.out_name, ex))
    bindings = resolver.resolve_signal_inputs(sig)

    # ── 展开链记录：本行原式 + 字母代入真实信号名(去 _to_logic 的基名+切片)的等价形式 ──
    if chain_out is not None and all(c["out"] != sig.out_base for c in chain_out):
        rename = {ltr: b.base + _slice_suffix(b)
                  for ltr, b in bindings.items() if b is not None and b.base}
        chain_out.append({"out": sig.out_base, "expr": str(sig.expr).strip(),
                          "subst": E.to_text(node, rename)})

    # 整个 cone 的根信号(stack[0] = 最外层被断言的输出)。叶子的"自引用"判定相对它：
    # 任何层级的叶子若引用根信号，都绝不能 force 根的输出网(那正是要断言的网)。
    root_base = stack[0]

    mapping = {}      # 原表达式变量字母 -> 替换 AST
    leaves = {}       # 叶子大写基名 -> InputBinding
    for letter in E.collect_vars(node):
        b = bindings.get(letter)
        if b is None:
            continue
        if b.found_in in EXPANDABLE:
            child = _find_logic(wb, b.base)
            if child is None:
                raise ConeError("内部信号 %r 在 logic 页找不到定义行" % b.base)
            child_node, child_leaves = expand(child, wb, resolver, _depth + 1, stack, chain_out)
            for key, leaf in child_leaves.items():
                # 子展开的叶子已带自己的 Excel 来源("行名.字母")，原样并入
                _merge_leaf(leaves, key, leaf, getattr(leaf, "xl_letters", None) or [])
            # 输入单元格带切片(如 pll_n2_to_logic[30:23]) → 对子表达式取位段
            if b.slice_msb is not None:
                mapping[letter] = E.Part(child_node, b.slice_msb, b.slice_lsb)
            else:
                mapping[letter] = child_node
        elif (_is_mux_out_binding(b, resolver)
              and getattr(resolver, "cascade_mode", "cone") == "cone"
              and _mux_expandable_at(wb, b.base)):
            # ⭐ 跨 logic↔mux 边界(2026-06-15 R38)：输入是某 mux 组的输出 → 不再 force 衔接网，而是把该
            # mux 合成等价 AST(选择?数据…)代入并递归(数据/控制还是 mux/logic 就继续展开)，对齐 VBA 端到端
            # 驱动真寄存器、顺手免探针前缀。仅展开模式(cascade=cone)且该 mux 可安全完整展开(mux_expandable)
            # 才走这里；force 模式 / 宽 select / 有 hole 的 mux 落 else 叶子(force 衔接网，与旧行为同)。
            from . import mux_gen           # 惰性 import 破循环
            grp = _find_mux(wb, b.base)
            child_node, child_leaves = mux_gen.synthesize_mux_expr(
                wb, resolver, grp, _depth + 1, stack, chain_out)
            for key, leaf in child_leaves.items():
                _merge_leaf(leaves, key, leaf, getattr(leaf, "xl_letters", None) or [])
            if b.slice_msb is not None:
                mapping[letter] = E.Part(child_node, b.slice_msb, b.slice_lsb)
            else:
                mapping[letter] = child_node
        else:
            # 叶子输入：变量名 = 大写基名；同一寄存器的多个切片共享同一个叶子 binding
            # self_base=root_base：叶子重解析时保持自引用语义
            # （否则自引用叶子会被误判级联 → force 被断言的输出网 → 原 d2a bug 在 cone 路径复活）
            key = b.base.upper()
            # Excel 来源坐标(纯显示)：根行直接输入 = 列字母本身；上游行的输入 = "行名.字母"
            src = letter if _depth == 0 else "%s.%s" % (sig.out_base, letter)
            _merge_leaf(leaves, key,
                        _full_binding(key, b, resolver, self_base=root_base), [src])
            mapping[letter] = E.Var(key, b.slice_msb, b.slice_lsb)

    return _substitute(node, mapping), leaves


# ───────────────────────────── 内部 ─────────────────────────────
def _slice_suffix(b):
    """输入 binding 的切片后缀(来自 Excel 单元格的 [msb:lsb])：[7] / [30:23]；无切片为空。"""
    if b.slice_msb is None:
        return ""
    if b.slice_lsb is None or b.slice_msb == b.slice_lsb:
        return "[%d]" % b.slice_msb
    return "[%d:%d]" % (b.slice_msb, b.slice_lsb)


def _find_logic(wb, base):
    low = str(base).strip().lower()
    for s in wb.logic:
        if s.out_base.lower() == low:
            return s
    return None


def _find_mux(wb, base):
    """对称 _find_logic：按基名(小写)在 wb.mux 找 MuxGroup，找不到返回 None。
    供 expand 跨边界展开时按输入基名找回它引用的 mux 组。"""
    low = str(base).strip().lower()
    for g in (getattr(wb, "mux", None) or []):
        if g.out_base.lower() == low:
            return g
    return None


def _mux_expandable_at(wb, base):
    """base 命中一个【可安全完整展开】的 mux 组吗（mux_gen.mux_expandable）。否则 cone 不展开、当叶子
    force 衔接网(旧行为)，避免宽 select 假绿 / hole 假红。"""
    grp = _find_mux(wb, base)
    if grp is None:
        return False
    from . import mux_gen           # 惰性 import 破循环
    return mux_gen.mux_expandable(grp)


def _full_binding(key, b, resolver, self_base=None):
    """以"整个寄存器字段"为单位重建叶子 binding（切片在表达式 Part/Var 层处理）。

    例: frac_n_lsb[15:1] 与 frac_n_lsb[0] 是同一个 16bit 字段的两个切片，
    叶子 = frac_n_lsb 全 16bit，RF_WRITE 写全宽度；表达式分别取 [15:1] 与 [0]。

    self_base: 正在展开的根信号基名(小写)。必须透传给 resolve()，否则自引用叶子
    在二次解析时丢失 self 语义 → 被误判级联 → force 被验证的输出网(原 d2a bug 复活)。
    """
    width = b.width
    if b.reg_msb is not None and b.reg_lsb is not None:
        width = max(width, b.reg_msb - b.reg_lsb + 1)
    # raw 保留原始单元格文本（不能用 base）：resolver 靠"原文是否带 _to_logic 后缀"区分
    # regfile 导出的前级信号(force 基名) vs 直接引用上游 logic 输出网(force RTL 网名)。
    info = {"raw": b.raw, "base": b.base, "width": width,
            "msb": width - 1 if width > 1 else None,
            "lsb": 0 if width > 1 else None}
    return resolver.resolve(key, info, self_base=self_base)


def _wider(old, new):
    """同名叶子保留更宽的 binding（不同切片并存时取全宽）。"""
    if old is None:
        return new
    return new if new.width > old.width else old


def _merge_leaf(leaves, key, new_binding, new_sources):
    """并入一个叶子：binding 取更宽的；Excel 来源(xl_letters)去重累积。

    去重很关键——pll_n 的 A/B/C/D 四个切片都展开 pll_n2，pll_n2 的四个切片又都展开
    pll_n1，同一叶子(int_n)会被到达 16 次；来源按 "行名.字母" 字符串去重后仍只有 1 条。"""
    old = leaves.get(key)
    merged = _wider(old, new_binding)
    sources = list(getattr(old, "xl_letters", None) or [])
    for s in new_sources:
        if s not in sources:
            sources.append(s)
    merged.xl_letters = sources
    leaves[key] = merged


def _substitute(node, mapping):
    """把 AST 里的变量引用替换为 mapping 给定的子树；带位选的变量包成 Part。"""
    if isinstance(node, E.Var):
        sub = mapping.get(node.name)
        if sub is None:
            return node
        if node.msb is not None:
            return E.Part(sub, node.msb, node.lsb)
        return sub
    if isinstance(node, E.Const):
        return node
    if isinstance(node, E.Unary):
        return E.Unary(node.op, _substitute(node.operand, mapping))
    if isinstance(node, E.Binary):
        return E.Binary(node.op, _substitute(node.left, mapping), _substitute(node.right, mapping))
    if isinstance(node, E.Ternary):
        return E.Ternary(_substitute(node.cond, mapping),
                         _substitute(node.then, mapping),
                         _substitute(node.els, mapping))
    if isinstance(node, E.Concat):
        return E.Concat([_substitute(p, mapping) for p in node.parts])
    if isinstance(node, E.Repeat):
        return E.Repeat(node.count_node, _substitute(node.body, mapping))
    if isinstance(node, E.Part):
        return E.Part(_substitute(node.operand, mapping), node.msb, node.lsb)
    return node
