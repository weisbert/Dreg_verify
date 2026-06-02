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
                 is_negative=False, neg_value=None, neg_mode=None, note="", name=None):
        self.index = index                  # T 编号(默认命名 T<index> 用)
        self.assignments = assignments       # {字母: int}
        self.exp_value = exp_value           # 正确期望值
        self.exp_width = exp_width
        self.is_negative = is_negative
        self.neg_value = neg_value           # 负向时实际写入断言的(错误)期望
        self.neg_mode = neg_mode
        self.note = note
        self.name = name                     # 自定义测试名(None→用 T<index>)；用户可对自己加的测试改名

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
    """控制位枚举电平：≤2 位 → 全枚举（多 bit 切片组如 d_pfd_en_lnmode[1:0]，各 bit 独立有意义）；
    更宽 → {0, 全1}（覆盖真值/假值，避免组合爆炸）。"""
    if width <= 2:
        return list(range(1 << max(width, 1)))
    return [0, E.mask(width)]


def _group_meta(letters, bindings, widths):
    """同一物理信号的多个字母可能各占不同 bit 切片（A=d_pfd_en_lnmode[1]、B=[0]）。

    返回 (union_width, union_msb, union_lsb, letter_lsbs)：
      union 覆盖所有切片（[1]+[0] → 宽 2、[1:0]）；letter_lsbs = {字母: 切片 lsb}，
      取值/驱动时按位拼装，不再共享同一个值（修：[1:0] 被坍塌成 [1:1] 的 bug）。
    """
    union_msb, union_lsb = 0, None
    letter_lsbs = {}
    for ltr in letters:
        b = bindings.get(ltr)
        msb = getattr(b, "slice_msb", None)
        lsb = getattr(b, "slice_lsb", None)
        if lsb is None or msb is None:
            lsb, msb = 0, widths.get(ltr, 1) - 1
        letter_lsbs[ltr] = lsb
        union_msb = max(union_msb, msb)
        union_lsb = lsb if union_lsb is None else min(union_lsb, lsb)
    return union_msb + 1, union_msb, (union_lsb or 0), letter_lsbs


def group_value_to_letters(letters, letter_lsbs, widths, value):
    """物理信号取值 → 各字母取值（按各自切片位置取位）。"""
    return {l: (value >> letter_lsbs[l]) & E.mask(widths.get(l, 1)) for l in letters}


def letters_to_group_value(letters, letter_lsbs, widths, assignments):
    """各字母取值 → 物理信号取值（按各自切片位置拼装）。"""
    val = 0
    for l in letters:
        val |= (assignments.get(l, 0) & E.mask(widths.get(l, 1))) << letter_lsbs[l]
    return val


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
    all_used = E.collect_vars(node)
    missing = [v for v in all_used if v not in widths]
    used = [v for v in all_used if v in widths]
    control = [v for v in control if v in widths]

    # ── 同名输入分组：同一物理信号占多个表达式变量(如 A、B 同为 d_pfd_en_lnmode)→共享取值 ──
    base_of = {}
    for ltr in used:
        base = getattr(bindings.get(ltr), "base", None)
        base_of[ltr] = base.lower() if base else ltr.lower()
    groups = {}                       # base -> [letters]（按发现顺序）
    for ltr in used:
        groups.setdefault(base_of[ltr], []).append(ltr)
    reps, rep_is_control, rep_width = [], {}, {}
    group_lsbs = {}                   # base -> {字母: 切片lsb}（同信号多切片时按位拼装）
    for base, letters in groups.items():
        rep = letters[0]
        reps.append(rep)
        rep_is_control[rep] = any(l in control for l in letters)
        # 组宽度 = 所有切片的并集宽度（A=[1]+B=[0] → 2 bit），不是单字母宽度的 max
        union_w, _msb, _lsb, letter_lsbs = _group_meta(letters, bindings, widths)
        rep_width[rep] = union_w
        group_lsbs[base] = letter_lsbs
    reps.sort(key=used.index)
    control_reps = [r for r in reps if rep_is_control[r]]
    data_reps = [r for r in reps if not rep_is_control[r]]

    def expand(rep_assign):
        """把每个 base(代表字母)的物理信号取值展开到同组所有字母——按各自切片位置取位。"""
        full = {}
        for base, letters in groups.items():
            val = rep_assign.get(letters[0], 0)
            full.update(group_value_to_letters(letters, group_lsbs[base], widths, val))
        for v in used:
            full.setdefault(v, 0)
        return full

    total_bits = sum(rep_width[r] for r in reps)
    meta = {
        "control": list(control_reps), "data": list(data_reps),
        "missing_vars": missing, "total_bits": total_bits,
        "truncated": False, "dropped": 0, "exhaustive": False,
    }

    vectors = []

    if exhaustive and total_bits <= exhaustive_bit_cap and reps:
        meta["exhaustive"] = True
        combos = _exhaustive_assignments(reps, rep_width)
        for idx, rep_assign in enumerate(combos):
            if idx >= max_tests:
                meta["truncated"] = True
                meta["dropped"] = len(combos) - max_tests
                break
            assign = expand(rep_assign)
            v, w = E.evaluate(node, E.Env(widths, assign), out_width)
            vectors.append(TestVector(idx, assign, v, w))
        vectors = _dedup(vectors)
        for i, vv in enumerate(vectors):
            vv.index = i
        return vectors, meta

    # 控制位（按 base 代表）全组合
    if control_reps:
        control_value_lists = [_control_levels(rep_width[r]) for r in control_reps]
        control_combos = list(itertools.product(*control_value_lists))
    else:
        control_combos = [()]

    # 数据模式主题
    if mode == "max":
        themes = ["all0", "all1", "comp", "walk", "distinct"]
    else:  # min
        themes = ["distinct"] if control_reps else ["all0", "distinct"]

    idx = 0
    for combo in control_combos:
        rep_ctrl = {r: combo[i] for i, r in enumerate(control_reps)}
        for theme in themes:
            if idx >= max_tests:
                meta["truncated"] = True
                break
            rep_assign = dict(rep_ctrl)
            for j, r in enumerate(data_reps):
                rep_assign[r] = _pattern(theme, rep_width[r], j)
            assign = expand(rep_assign)
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


# ───────────────────────────── 测试项编辑辅助（GUI 逐输入表格用） ─────────────────────────────
# 一条测试项的本质 = 各物理输入取值 → 表达式自动求出期望值。下面三个函数支撑 GUI 把测试项
# 展示成"每个物理输入一列、可逐格编辑"的表格，并能从编辑值反向构造 TestVector 回流到生成。
def input_groups(node, bindings):
    """把表达式引用到的输入按"物理信号(base)"分组——同一物理信号占多个表达式字母(如 A、B
    同为某寄存器位)时共享一列取值。返回有序 list，每项:
        {'base': 显示名(原大小写), 'letters': [字母...], 'rep': 代表字母,
         'width': 该组最大位宽, 'is_control': 是否控制位, 'kind': 'RO'/'RW'/...}
    顺序 = 各 base 在 collect_vars 里首次出现的顺序。
    """
    widths = {ltr: b.width for ltr, b in bindings.items()}
    used = [v for v in E.collect_vars(node) if v in widths]
    try:
        control, _data = E.classify_vars(node, E.Env(widths))
    except E.ExprError:
        control = []
    control_set = set(control)
    base_of = {}
    for ltr in used:
        b = bindings.get(ltr)
        base_of[ltr] = (b.base.lower() if b and b.base else ltr.lower())
    order, members = [], {}
    for ltr in used:
        bk = base_of[ltr]
        if bk not in members:
            members[bk] = []
            order.append(bk)
        members[bk].append(ltr)
    groups = []
    for bk in order:
        letters = members[bk]
        # 代表字母取组内"最宽"的——generate_vectors 把共享值按各字母位宽写回，窄字母会被截位；
        # 读最宽字母才能拿到完整值(否则同 base 不等宽切片时往返丢高位)。
        rep = max(letters, key=lambda l: widths.get(l, 1))
        b = bindings.get(rep)
        base = (b.base if b and b.base else rep)
        # 组宽度/切片 = 所有字母切片的并集（A=[1]+B=[0] → 宽 2、显示 [1:0]），不是单字母的
        union_w, union_msb, union_lsb, letter_lsbs = _group_meta(letters, bindings, widths)
        groups.append({
            "base": base,                          # 查表/取值用的纯基名(不带位宽，勿改)
            "label": base + _slice_str(b, union_w, union_msb, union_lsb),  # 显示用(带位宽)
            "letters": list(letters),
            "letter_lsbs": letter_lsbs,            # 字母 → 它在该信号里的切片 lsb（取值/驱动按位拼装）
            "rep": rep,
            "width": union_w,
            "is_control": any(l in control_set for l in letters),
            "kind": getattr(b, "kind", "?"),
        })
    return groups


def _slice_str(binding, width, union_msb=None, union_lsb=None):
    """重建位宽切片显示串：多字母时用切片并集 [union_msb:union_lsb]；标量为空。"""
    if union_msb is not None and (union_msb, union_lsb) != (0, 0):
        return "[%d:%d]" % (union_msb, union_lsb)
    msb = getattr(binding, "slice_msb", None)
    lsb = getattr(binding, "slice_lsb", None)
    if msb is not None and lsb is not None and (msb, lsb) != (0, 0):
        return "[%d:%d]" % (msb, lsb)
    if width and width > 1:
        return "[%d:0]" % (width - 1)
    return ""


def _expand_base_values(groups, base_values, widths):
    """{base_lower:int} → {字母:int}（按各自切片位置取位）。未给的输入按 0。"""
    assign = {}
    for g in groups:
        val = base_values.get(g["base"].lower(), 0)
        assign.update(group_value_to_letters(g["letters"], g["letter_lsbs"], widths, val))
    return assign


def vector_to_base_values(vec, groups):
    """把一个 TestVector(字母→值) 转成 {base_lower:int}：按切片位置把各字母的值拼回物理信号值。"""
    out = {}
    for g in groups:
        widths = {l: 64 for l in g["letters"]}      # 拼装时不截位(各字母值已按自身位宽生成)
        out[g["base"].lower()] = letters_to_group_value(g["letters"], g["letter_lsbs"],
                                                        widths, vec.assignments)
    return out


def make_vector_from_base_values(node, bindings, groups, base_values, out_width,
                                 index=0, expected_override=None, name=None):
    """从 GUI 编辑的 {base_lower:int} 构造一个 TestVector。
    期望值由表达式自动重算(永远自洽)；若给了 expected_override 且与算出值不同 →
    标负向(故意填错，复用负向机制：asserted_value 取 neg_value)。name 为自定义测试名(可选)。
    """
    widths = {ltr: b.width for ltr, b in bindings.items()}
    assign = _expand_base_values(groups, base_values, widths)
    for v in E.collect_vars(node):
        assign.setdefault(v, 0)
    exp_value, exp_width = E.evaluate(node, E.Env(widths, assign), out_width)
    if expected_override is not None:
        wrong = expected_override & E.mask(exp_width)
        if wrong != exp_value:
            return TestVector(index, assign, exp_value, exp_width,
                              is_negative=True, neg_value=wrong, neg_mode="value", name=name,
                              note="手工指定期望值(与表达式计算值不同)，预期断言应 FAIL，用于负向自检")
    return TestVector(index, assign, exp_value, exp_width, name=name)
