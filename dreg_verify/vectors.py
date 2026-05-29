# -*- coding: utf-8 -*-
"""
vectors.py — 为一个 logic 信号生成测试向量（控制位穷举 + 数据采样），并支持负向用例。

策略（design-plan）：
  控制/选择位（三元条件 + 门控位，按 1 位枚举）全组合 ×
  数据总线给确定性特征值（同一 mux 内各总线值互不相同→可观测选路）。
  min：1 套数据特征；max：多套数据模式(全0/全1/互补/走1)。
  总输入位 ≤ 阈值时支持 --exhaustive 真·全穷举。
期望输出 = 在该向量上对表达式求值（永远正确，与向量选择无关）。
负向用例：取正常向量但故意填错期望值(取反/加一/指定)，用于自检 checker 能抓错。
"""

import itertools

from . import expr as E


class TestVector:
    def __init__(self, index, assignments, exp_value, exp_width,
                 is_negative=False, neg_value=None, neg_mode=None, note=""):
        self.index = index                  # T 编号
        self.assignments = assignments       # {字母: int}
        self.exp_value = exp_value           # 正确期望值
        self.exp_width = exp_width
        self.is_negative = is_negative
        self.neg_value = neg_value           # 负向时实际写入断言的(错误)期望
        self.neg_mode = neg_mode
        self.note = note

    @property
    def asserted_value(self):
        return self.neg_value if self.is_negative else self.exp_value


# ───────────────────────────── 数据特征模式 ─────────────────────────────
def _pattern(theme, width, var_index):
    """为某数据总线生成确定性取值。var_index 用于让同一 mux 内各总线互不相同。"""
    m = E.mask(width)
    if theme == "all0":
        base = 0
    elif theme == "all1":
        base = m
    elif theme == "comp":
        base = (0x5555555555555555 if var_index % 2 == 0 else 0xAAAAAAAAAAAAAAAA) & m
    elif theme == "walk":
        base = (1 << (var_index % width)) & m if width > 0 else 0
    else:  # 'distinct'：每条总线一个互不相同的特征码
        seeds = [0x5, 0xA, 0x3, 0xC, 0x6, 0x9, 0xF, 0x1, 0x2, 0x4, 0x8, 0x7]
        seed = seeds[var_index % len(seeds)]
        base = 0
        bit = 0
        # 把 4-bit 种子平铺到整个宽度，保证不同 var 取值不同
        while bit < width:
            base |= (seed << bit)
            bit += 4
        base &= m
        # 再叠加 var_index 偏移，进一步去重
        base ^= (var_index & m)
    return base & m


def _control_levels(width):
    """控制位枚举电平：1 位 → {0,1}；多位 → {0, 全1}（覆盖真值/假值）。"""
    if width <= 1:
        return [0, 1]
    return [0, E.mask(width)]


# ───────────────────────────── 主生成 ─────────────────────────────
def generate_vectors(node, bindings, out_width, mode="min", max_tests=256,
                     exhaustive=False, exhaustive_bit_cap=10):
    """
    node: AST
    bindings: dict 字母 -> InputBinding（提供宽度）
    返回 (vectors:list[TestVector], meta:dict)
    """
    widths = {ltr: b.width for ltr, b in bindings.items()}
    env_for_class = E.Env(widths)
    control, data = E.classify_vars(node, env_for_class)
    # 仅保留实际有绑定的变量（表达式可能引用未在 inputs 的字母 → 异常，过滤+记录）
    all_used = E.collect_vars(node)
    missing = [v for v in all_used if v not in widths]
    control = [v for v in control if v in widths]
    data = [v for v in data if v in widths]

    total_bits = sum(widths[v] for v in all_used if v in widths)
    meta = {
        "control": list(control), "data": list(data),
        "missing_vars": missing, "total_bits": total_bits,
        "truncated": False, "dropped": 0, "exhaustive": False,
    }

    vectors = []

    if exhaustive and total_bits <= exhaustive_bit_cap and all_used:
        meta["exhaustive"] = True
        combos = _exhaustive_assignments(all_used, widths)
        for idx, assign in enumerate(combos):
            if idx >= max_tests:
                meta["truncated"] = True
                meta["dropped"] = len(combos) - max_tests
                break
            v, w = E.evaluate(node, E.Env(widths, assign), out_width)
            vectors.append(TestVector(idx, assign, v, w))
        return vectors, meta

    # 控制位全组合
    if control:
        control_value_lists = [_control_levels(widths[v]) for v in control]
        control_combos = list(itertools.product(*control_value_lists))
    else:
        control_combos = [()]

    # 数据模式主题
    if mode == "max":
        themes = ["all0", "all1", "comp", "walk", "distinct"]
    else:  # min
        themes = ["distinct"] if control else ["all0", "distinct"]

    idx = 0
    for combo in control_combos:
        ctrl_assign = {v: combo[i] for i, v in enumerate(control)}
        for theme in themes:
            if idx >= max_tests:
                meta["truncated"] = True
                break
            assign = dict(ctrl_assign)
            for j, v in enumerate(data):
                assign[v] = _pattern(theme, widths[v], j)
            # 表达式可能也直接引用控制位作数据，已在 ctrl_assign；缺的兜 0
            for v in all_used:
                assign.setdefault(v, 0)
            val, w = E.evaluate(node, E.Env(widths, assign), out_width)
            vectors.append(TestVector(idx, assign, val, w))
            idx += 1
        if meta["truncated"]:
            break

    # 截断丢弃数 = 计划组合 - 实际生成（去重前），明确指"因 max_tests 未生成的计划组合数"
    planned = len(control_combos) * len(themes)
    generated = len(vectors)               # 截断后、去重前
    if generated < planned:
        meta["truncated"] = True
        meta["dropped"] = planned - generated

    # 去重（控制位多位时 0/全1 可能与数据模式产生重复向量），去重数单列，不与截断混淆
    pre = len(vectors)
    vectors = _dedup(vectors)
    meta["deduped"] = pre - len(vectors)
    for i, v in enumerate(vectors):
        v.index = i
    return vectors, meta


def _exhaustive_assignments(varnames, widths):
    ranges = [range(1 << widths[v]) for v in varnames]
    out = []
    for combo in itertools.product(*ranges):
        out.append({v: combo[i] for i, v in enumerate(varnames)})
    return out


def _dedup(vectors):
    seen = set()
    out = []
    for v in vectors:
        key = tuple(sorted(v.assignments.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


# ───────────────────────────── 负向用例 ─────────────────────────────
def make_negative(vec, mode="invert", fixed_value=None):
    """基于一个正常向量造一个负向用例（故意填错期望值）。"""
    w = vec.exp_width
    m = E.mask(w)
    correct = vec.exp_value
    if mode == "invert":
        wrong = (~correct) & m
    elif mode == "inc":
        wrong = (correct + 1) & m
    elif mode == "value":
        wrong = (fixed_value if fixed_value is not None else 0) & m
    else:
        raise ValueError("未知负向模式 %r" % mode)
    if wrong == correct:
        # 取反/加一后若仍相等（极少见），强制翻最低位
        wrong = correct ^ (1 if w >= 1 else 0)
    return TestVector(
        index=vec.index, assignments=dict(vec.assignments),
        exp_value=correct, exp_width=w,
        is_negative=True, neg_value=wrong, neg_mode=mode,
        note="故意填错期望值(%s)，此断言预期应 FAIL，用于自检 checker 能否抓错" % mode,
    )


def add_negatives(vectors, mode="invert", which="first", fixed_value=None):
    """
    在正常向量基础上追加负向用例。
    which: 'first' 只对第一个向量造一个；'all' 每个向量都造。
    返回新列表（正常 + 负向，负向排在后面）。
    """
    if not vectors:
        return list(vectors)
    out = list(vectors)
    targets = vectors[:1] if which == "first" else vectors
    for vec in targets:
        out.append(make_negative(vec, mode=mode, fixed_value=fixed_value))
    return out
