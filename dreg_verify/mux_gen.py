# -*- coding: utf-8 -*-
"""
mux_gen.py — mux 页测试生成器（2026-06-03 第九轮）。

mux 语义（真表探查 + designer .sv 解码，见 mux功能影响面分析.md）：
    G = case(B) { F行: A行; ... }      N 选 1，不是 logic 页的三元表达式

一个 MuxGroup 的测试配方（designer assert_151/164 解码）：
  ① 控制信号(B = logic 页 to_mux 行)经其 logic 输入级联驱动：
       line 路径  = force 线控 wire + 模式位/iddq 取选中线控的组合
       local 路径 = RF_WRITE 本地寄存器 + 模式位取选中本地的组合
  ② 全部数据寄存器(A 列) RF_WRITE 写互异值（否则选错路测不出 = 假绿）
  ③ #1ps; assert (`ENV_RF.<G列名> == 被选中寄存器的值)

测试集（覆盖度三档，2026-06-03 第十一轮拉开差距——每升一档多抓一类真实故障）：
  精简(min)        = line 路径扫每 case（x 位取 0）+ 1 条另一路径抽测
                     → 抓"case 接到错的寄存器"（选路错）
  全面(max)        = 精简 + case don't-care 位展开 + 每 case 一轮反码数据
                     → 多抓"数据通路位坏死"（第一轮值恰等于坏死值时测不出，反码轮每位都翻过）
  穷举(exhaustive) = 全面 + 另一条控制路径全扫每 case（取代单条抽测）
                     → 多抓"只在另一条物理驱动路径下出现的选路错"
  mux 输出不做 iddq 测试（designer 没做）。

⭐ 架构要点（绕开"per-vector 驱动切换"的结构性难题）：
  mux 的"输入集" = 控制行的 logic 输入（键 "c:<字母>"）+ 数据寄存器（键 "d:<序号>"），
  每个输入有固定的 InputBinding（kind 不变）→ line/local 切换 = 改"值"而不是改"驱动方式"
  → sv_writer.compute_drives / 反例 / owner / 汇总计数器机制全部直接复用。
"""

import itertools
import re

from . import expr as E
from . import vectors as V

# assignment 键约定（向后兼容关键——designer 手填期望/报告/GUI 都按键对号入座）：
#   控制 idx=0（B 列）：  "c:<后缀>"     —— 与 LPBT 单控制完全一致
#   控制 idx>=1（C/D/E）："c<idx>:<后缀>"
#   数据寄存器：          "d:<case 序号>"
#   上游 mux 配方（级联）："m<上游组号>." + 上述键（如 "m2.c:reg" / "m2.d:1"）
CTRL_KEY = "c:%s"
DATA_KEY = "d:%d"
MUX_CASCADE_MAX_DEPTH = 4    # mux 套 mux 最大展开深度（WL 实测 2 层；防环兜底）


def ctrl_key(idx, suffix):
    """控制赋值键：idx=0 保持 'c:<suffix>'（LPBT 兼容），其余 'c<idx>:<suffix>'。"""
    if idx == 0:
        return "c:%s" % suffix
    return "c%d:%s" % (idx, suffix)


def key_role(key):
    """赋值键的角色：'ctrl'（控制）/ 'data'（数据寄存器）/ 'upstream'（上游 mux 配方驱动）。

    generator/gui/report 共用此判断——不要在外面手写 startswith('c:')（多控制后会漏 c1:/c2:）。"""
    if "." in key.split(":")[0] and key.startswith("m"):
        return "upstream"
    if re.match(r"^c\d*:", key):
        return "ctrl"
    return "data"


# ───────────────────────────── 控制路径发现 ─────────────────────────────
def discover_ctrl_paths(node, bindings):
    """对控制信号的 logic 表达式做"透传路径发现"：哪个数据输入在哪组控制值下直通输出。

    返回 [{'var': 字母, 'kind': 'RO'/'RW', 'ctrl_assign': {字母: 值}}, ...]
      RO 路径 = line（force 线控 wire）；RW 路径 = local（RF_WRITE 本地寄存器）。

    算法（通用，不硬编码 A?C:B 形态）：对每个数据变量 D，枚举控制变量组合，
    用两个不同测试值验证 D 是否原样到达输出（两个值都透传才算，防巧合）。
    门控类表达式如 (A?C:B)&(~J)：J 也是控制变量，J=0 的组合才能透传——自动覆盖。
    """
    widths = {ltr: b.width for ltr, b in bindings.items()}
    env = E.Env(widths)
    try:
        control, data = E.classify_vars(node, env)
    except E.ExprError:
        return []
    control = [c for c in control if c in widths]
    data = [d for d in data if d in widths]
    paths = []
    for dv in data:
        dw = widths[dv]
        p1 = 0b101 & E.mask(dw)
        p2 = 0b010 & E.mask(dw)
        if p1 == p2 or p1 == 0:
            p1, p2 = 1 & E.mask(dw), 0
        combos = (itertools.product(*[_levels(widths[c]) for c in control])
                  if control else [()])
        for combo in combos:
            assign = {c: combo[i] for i, c in enumerate(control)}
            a1, a2 = dict(assign), dict(assign)
            for other in data:
                a1[other] = p1 if other == dv else p2     # 其它数据 = 干扰值
                a2[other] = p2 if other == dv else p1
            try:
                v1, _ = E.evaluate(node, E.Env(widths, a1), dw)
                v2, _ = E.evaluate(node, E.Env(widths, a2), dw)
            except E.ExprError:
                continue
            if v1 == p1 and v2 == p2:
                paths.append({"var": dv, "kind": bindings[dv].kind,
                              "ctrl_assign": dict(assign)})
                break
    return paths


def _levels(width):
    """控制变量枚举电平（≤2 位全枚举，更宽 {0,全1}——与 vectors._control_levels 同策略）。"""
    if width <= 2:
        return list(range(1 << max(width, 1)))
    return [0, E.mask(width)]


# ───────────────────────────── 互异值分配 ─────────────────────────────
def _effective_width(case, i, bindings, data_keys):
    """数据寄存器 i 的有效位宽 = min(Excel A 列声明位宽, tmm/regmap 实际字段位宽)。

    写寄存器时 compute_drives 按实际字段位宽截断（sv_writer fw mask），
    互异性必须在**截断后**仍成立——按声明位宽分配再被截断会静默撞值。
    bindings/data_keys 不给时退化为按声明位宽（仅单元测试用）。
    """
    w = max(case.input_width, 1)
    if bindings is not None and data_keys is not None and i < len(data_keys):
        b = bindings.get(data_keys[i])
        if b is not None and b.reg_msb is not None and b.reg_lsb is not None:
            fw = b.reg_msb - b.reg_lsb + 1
            w = max(min(w, fw), 1)
    return w


def _alloc_loop(group, bindings, data_keys, seed_fn):
    """互异值分配公共骨架：seed_fn(i, width, mask) 给初值，碰撞时 1..m 循环探测（永不取 0）。

    返回 (values, collision)。collision=True 表示有效位宽装不下这么多互异值。
    """
    values, seen, collision = [], {0}, False
    for i, case in enumerate(group.cases):
        w = _effective_width(case, i, bindings, data_keys)
        m = E.mask(w)
        v = seed_fn(i, w, m)
        if v == 0:
            v = 1
        tries = 0
        while v in seen and tries <= m:
            v = (v % m) + 1                 # 1..m 循环探测（永不取 0）
            tries += 1
        if v in seen:
            collision = True
        seen.add(v)
        values.append(v)
    return values, collision


def alloc_distinct_values(group, bindings=None, data_keys=None):
    """给每个数据寄存器分配互异值（designer 风格：4bit+ 从 0xA 递减；窄位宽从 1 递增）。

    互异是 mux 选路验证的命门：两条数据路同值时选错路也测不出（假绿）。
    避开 0（RTL mux 坏死输出 0 时不应误判 PASS）。
    返回 (values, collision)。
    """
    return _alloc_loop(group, bindings, data_keys,
                       lambda i, w, m: ((0xA - i) & m) if w >= 4 else ((i + 1) & m))


def alloc_inverted_values(group, base_values, bindings=None, data_keys=None):
    """反码数据轮：每个数据寄存器取第一轮值的按位取反（有效位宽内），保持互异、避 0。

    抓的故障：数据通路某位坏死(stuck-at)时，若第一轮写入值恰好等于坏死位的值则测不出；
    反码轮让每个寄存器的每一位都翻转过 → 两轮合起来每位都验过 0 和 1。
    返回 (values, collision)。
    """
    return _alloc_loop(group, bindings, data_keys,
                       lambda i, w, m: (~base_values[i]) & m)


# ───────────────────────────── 解析一个 mux 组 ─────────────────────────────
def _resolve_ctrl_driver(wb, resolver, ctrl, idx, key_prefix, _stack, _depth, issues):
    """解析一个控制信号的驱动器（三来源分发）。

    ctrl: MuxCtrl；idx: 控制序号(0=B列)；key_prefix: 赋值键前缀（""=本组，"m<N>."=上游配方）。
    返回 driver dict：
      {'source': 'logic'|'reg'|'mux'|'mux-force'|'unknown',
       'idx', 'base', 'width', 'letter',
       'keys': [赋值键], 'bindings': {键: InputBinding},
       # source='logic'（LPBT 形态）: 'ctrl_sig','ctrl_node','line','local'
       # source='reg'/'mux-force':    'key','binding'
       # source='mux'（级联展开）:     'upstream','recipe'}
    """
    low = ctrl.base.lower()
    driver = {"source": "unknown", "idx": idx, "base": ctrl.base,
              "width": ctrl.width, "letter": ctrl.letter, "key_prefix": key_prefix,
              "bindings": {}, "keys": []}

    # ── (a) logic 页行（LPBT 形态）：经 logic 表达式级联驱动（line/local 透传路径）──
    ctrl_sig = next((s for s in wb.logic if s.out_base.lower() == low), None)
    if ctrl_sig is not None:
        driver["source"] = "logic"
        driver["ctrl_sig"] = ctrl_sig
        driver["ctrl_node"] = None
        driver["line"] = driver["local"] = None
        try:
            node = E.parse(ctrl_sig.expr)
        except E.ExprError as ex:
            issues.append("控制信号 %s 的表达式 %r 解析失败: %s" % (ctrl.base, ctrl_sig.expr, ex))
            return driver
        driver["ctrl_node"] = node
        ctrl_bindings = resolver.resolve_signal_inputs(ctrl_sig)
        for letter in sorted(ctrl_bindings.keys()):
            key = key_prefix + ctrl_key(idx, letter)
            driver["bindings"][key] = ctrl_bindings[letter]
            driver["keys"].append(key)
            if not ctrl_bindings[letter].resolved:
                issues.append("控制信号 %s 的输入 %s=%s 未解析: %s"
                              % (ctrl.base, letter, ctrl_bindings[letter].base,
                                 ctrl_bindings[letter].note or ""))
        paths = discover_ctrl_paths(node, ctrl_bindings)
        for p in paths:
            p["key"] = key_prefix + ctrl_key(idx, p["var"])
        driver["line"] = next((p for p in paths if p["kind"] == "RO"), None)
        driver["local"] = next((p for p in paths if p["kind"] == "RW"), None)
        if driver["line"] is None and driver["local"] is None:
            issues.append("控制信号 %s 的表达式 %r 没有可透传的驱动路径（无法把控制驱到指定 case 值）"
                          % (ctrl.base, ctrl_sig.expr))
        return driver

    # ── (b) 寄存器直出（WL 主要形态）：RW → RF_WRITE 写控制值 / RO 线控 → force ──
    key = key_prefix + ctrl_key(idx, "reg")
    info = {"raw": ctrl.raw, "base": ctrl.base, "width": ctrl.width,
            "msb": ctrl.msb, "lsb": ctrl.lsb}
    b = resolver.resolve(key, info)
    if b.resolved and b.found_in in ("tmm", "regmap"):
        driver["source"] = "reg"
        driver["key"] = key
        driver["binding"] = b
        driver["bindings"][key] = b
        driver["keys"].append(key)
        return driver

    # ── (c) mux 级联：控制信号是另一个 mux 组的输出 ──
    upstream = next((g for g in wb.mux if g.out_base.lower() == low), None)
    if upstream is not None:
        if resolver.cascade_mode == "force":
            # force 级联网模式：直接 force 上游输出的衔接网（resolve 已命中 mux-output 分支）
            driver["source"] = "mux-force"
            driver["key"] = key
            driver["binding"] = b
            driver["bindings"][key] = b
            driver["keys"].append(key)
            return driver
        # cone 展开上游模式（默认）：反解上游 mux → 上游控制驱动 + 载体数据寄存器写目标值
        driver["source"] = "mux"
        driver["upstream"] = upstream
        recipe = resolve_upstream_recipe(wb, resolver, upstream, _stack, _depth + 1)
        driver["recipe"] = recipe
        for msg in recipe["issues"]:
            issues.append("控制信号 %s ← 上游 mux%s(%s): %s"
                          % (ctrl.base, upstream.group_no, upstream.out_base, msg))
        driver["bindings"].update(recipe["bindings"])
        driver["keys"].extend(recipe["keys"])
        return driver

    # ── 兜底：resolve 的绑定能用就用（wire 兜底 force 等），否则 issue ──
    if b.resolved:
        driver["source"] = "reg"
        driver["key"] = key
        driver["binding"] = b
        driver["bindings"][key] = b
        driver["keys"].append(key)
        return driver
    issues.append("控制信号 %s 无法解析：不是 logic 页行、不是带地址的寄存器、也不是其它 mux 组的输出（%s）"
                  % (ctrl.base, b.note or "tmm/regmap 查无此字段"))
    return driver


def resolve_upstream_recipe(wb, resolver, upstream, _stack, _depth):
    """上游 mux 的『驱到任意目标值』配方（mux 级联，cone「展开上游」思路）。

    要让上游 mux 输出 == V：
      ① 选载体 case：第一个数据输入是位宽足够的干净寄存器(RW/RO)的 case
      ② 上游控制驱到载体 case 的值（递归三来源——上游控制还是 mux 就继续展开）
      ③ 载体数据寄存器写 V（_apply_ctrl_driver 在出向量时做）
    全部赋值键带 "m<上游组号>." 前缀与下游隔离。

    返回 {'upstream','prefix','carrier_ci','carrier_key','carrier_eff_width',
          'ctrl_drivers'(上游的),'ctrl_values'(上游控制子值),'bindings','keys','issues'}
    """
    issues = []
    prefix = "m%s." % upstream.group_no
    out = {"upstream": upstream, "prefix": prefix,
           "carrier_ci": None, "carrier_key": None, "carrier_eff_width": 0,
           "ctrl_drivers": [], "ctrl_values": [],
           "bindings": {}, "keys": [], "issues": issues}
    low = upstream.out_base.lower()
    if low in _stack:
        issues.append("mux 级联成环: %s" % " → ".join(list(_stack) + [low]))
        return out
    if _depth > MUX_CASCADE_MAX_DEPTH:
        issues.append("mux 级联深度超过上限 %d" % MUX_CASCADE_MAX_DEPTH)
        return out
    stack = list(_stack) + [low]

    # ── ① 载体 case：『位宽足够、稳定可写』的数据输入；优先 RW(RF_WRITE)，RO(force) 兜底 ──
    #    designer 风格：要让上游 mux 输出 = N，走 local/lut 寄存器路径（软件可写），线控 force 是后备。
    candidates = []      # (是否RW, ci, key, binding, eff_width)
    for ci, case in enumerate(upstream.cases):
        key = prefix + (DATA_KEY % ci)
        info = {"raw": case.input_raw, "base": case.input_base, "width": case.input_width,
                "msb": case.input_msb, "lsb": case.input_lsb}
        b = resolver.resolve(key, info)
        if not b.resolved:
            continue
        if b.found_in in ("needs-prefix", "mux-output", "wire"):
            continue            # 级联网/查无的 wire 不当载体——载体必须稳定可写
        # 有效位宽 = min(声明位宽, 寄存器实际字段位宽)。bindings/data_keys 是单元素 {key}/[key]，
        # 唯一项的索引固定是 0——绝不能传 ci（上游 case 序号），否则 ci>=1 时 _effective_width
        # 的 i<len(data_keys) 守卫为假、字段位宽截断被跳过 → 窄字段载体被误判够宽 → 假向量。
        eff = _effective_width(case, 0, {key: b}, [key])
        if eff < upstream.out_width:
            continue            # 载体位宽装不下任意目标值
        is_rw = b.kind == "RW" and b.address is not None
        candidates.append((is_rw, ci, key, b, eff))
    chosen = (next((c for c in candidates if c[0]), None)      # 第一个 RW
              or (candidates[0] if candidates else None))      # 否则第一个可用(RO force)
    if chosen is not None:
        _is_rw, ci, key, b, eff = chosen
        out["carrier_ci"], out["carrier_key"] = ci, key
        out["carrier_eff_width"] = eff
        out["bindings"][key] = b
        out["keys"].append(key)
    if out["carrier_ci"] is None:
        issues.append("没有可用的载体数据寄存器（需要一个位宽≥%d 的 RW/RO 数据输入承载目标值）"
                      % upstream.out_width)
        return out

    # ── ② 上游控制驱到载体 case 的值（递归三来源解析）──
    carrier_case = upstream.cases[out["carrier_ci"]]
    try:
        cval, cw, dc = E.parse_case_literal(carrier_case.case_raw)
    except E.ExprError as ex:
        issues.append("载体 case 值 %r 解析失败: %s" % (carrier_case.case_raw, ex))
        return out
    widths = [c.width for c in upstream.ctrls]
    if upstream.ctrls and cw != upstream.ctrl_total_width:
        issues.append("载体 case %s 位宽(%d)与上游控制拼接总宽(%d)不一致"
                      % (carrier_case.case_raw, cw, upstream.ctrl_total_width))
        return out
    subs = E.split_case_value(cval, dc, widths) if widths else []
    for idx, ctrl in enumerate(upstream.ctrls):
        d = _resolve_ctrl_driver(wb, resolver, ctrl, idx, prefix, stack, _depth, issues)
        out["ctrl_drivers"].append(d)
        out["ctrl_values"].append(subs[idx][0])      # don't-care 位取 0
        out["bindings"].update(d["bindings"])
        out["keys"].extend(d["keys"])
    return out


def _driver_eff_mask(driver, key, fallback_width):
    """控制驱动值的有效掩码 = min(声明位宽, 绑定寄存器字段位宽)——写入截断后回验 case 命中用。"""
    b = driver["bindings"].get(key)
    w = max(fallback_width, 1)
    if b is not None:
        if b.reg_msb is not None and b.reg_lsb is not None:
            w = max(min(w, b.reg_msb - b.reg_lsb + 1), 1)
        elif b.width:
            w = max(min(w, b.width), 1)
    return E.mask(w)


def _apply_ctrl_driver(assignments, driver, value):
    """把一个控制驱动器驱到指定值（写 assignments）。

    返回 (实际驱到的值, ok, why_not)——实际值可能因寄存器位宽截断而不等于请求值，
    调用方用它做 case 命中回验（截断后命中错 case 的向量必须丢弃，不能静默生成）。
    """
    src = driver["source"]
    if src in ("reg", "mux-force"):
        m = _driver_eff_mask(driver, driver["key"], driver["width"])
        driven = value & m
        assignments[driver["key"]] = driven
        return driven, True, ""

    if src == "logic":
        path = driver.get("line") or driver.get("local")
        if path is None:
            return 0, False, "控制信号 %s 没有可透传的驱动路径" % driver["base"]
        other = driver.get("local") if path is driver.get("line") else driver.get("line")
        kp = driver.get("key_prefix", "")
        for var, val in path["ctrl_assign"].items():
            assignments[kp + ctrl_key(driver["idx"], var)] = val
        akey = path["key"]
        m = _driver_eff_mask(driver, akey, driver["width"])
        driven = value & m
        assignments[akey] = driven
        # 另一条物理路径写反值干扰（RTL 错选另一路 → case 命中不同 → 被抓）
        if other is not None and other["key"] not in assignments:
            om = _driver_eff_mask(driver, other["key"], driver["width"])
            assignments[other["key"]] = (~value) & om
        return driven, True, ""

    if src == "mux":
        recipe = driver["recipe"]
        up = recipe["upstream"]
        if recipe["carrier_key"] is None:
            return 0, False, ("控制信号 %s 的上游 mux%s 没有可用载体"
                              % (driver["base"], up.group_no))
        # 上游控制驱到载体 case（递归）；收集各上游控制【实际驱到的值】（可能被其寄存器位宽截断）
        up_parts = []
        for ud, uv in zip(recipe["ctrl_drivers"], recipe["ctrl_values"]):
            udv, ok, why = _apply_ctrl_driver(assignments, ud, uv)
            if not ok:
                return 0, False, why
            up_parts.append((udv, ud["width"], 0))
        # ⭐ 回验上游是否真的命中载体 case：若上游控制被寄存器位宽截断成别的值，
        # 上游 mux 会选错 case（载体没被选中）→ 该向量必须丢弃，不能静默生成（不变量 B）。
        if up_parts:
            up_cv, _w, _dc = E.concat_case_parts(up_parts)
            carrier_case = up.cases[recipe["carrier_ci"]]
            try:
                ccv, ccw, cdc = E.parse_case_literal(carrier_case.case_raw)
                if not E.case_matches(ccv, ccw, cdc, up_cv):
                    return 0, False, ("上游 mux%s 的控制被寄存器位宽截断后无法选中载体 case %s"
                                      "（载体没被选中，下游控制收到的是别的 case 的值）"
                                      % (up.group_no, carrier_case.case_raw))
            except E.ExprError:
                pass
        # 载体数据寄存器写目标值。下游看到的是上游 mux【输出】(out_width 位)，不是载体寄存器
        # (carrier_eff_width 位)——按两者取小截断，emit 的截断回验才能正确丢弃越界 case（不变量 E）。
        m = E.mask(min(recipe["carrier_eff_width"], up.out_width))
        driven = value & m
        assignments[recipe["carrier_key"]] = driven
        return driven, True, ""

    return 0, False, "控制信号 %s 来源未知（无法驱动）" % driver["base"]


def expand_mux_group(wb, resolver, group):
    """解析一个 mux 组的全部驱动绑定与 case 值。

    返回 dict {
      'bindings':  {键: InputBinding}    控制驱动输入 + 数据寄存器("d:<序号>")
      'used_vars': [键]                  控制输入在前、数据在后
      'data_keys': [键]                  与 group.cases 一一对应
      'ctrl_drivers': [driver]           与 group.ctrls 一一对应（三来源，_resolve_ctrl_driver）
      'ctrl_sig':  第一个 logic 来源控制的 LogicSignal（None=没有）       [LPBT 兼容别名]
      'ctrl_node': 其表达式 AST                                            [LPBT 兼容别名]
      'line':      其 line 路径 dict 或 None    {'var','kind','ctrl_assign','key'}
      'local':     其 local 路径 dict 或 None
      'parsed_cases': [(value,width,dc_mask) 或 None]   与 group.cases 一一对应
      'issues':    [str]                 非空 = 有解析问题（build 据此跳过并给原因）
    }

    控制信号三来源（WL_RFTRX 2026-06-03 第十四轮实证）：
      (a) logic 页行（LPBT 形态）→ 经 logic 表达式级联（line/local 透传路径）
      (b) 寄存器直出（WL 65/81）→ RW: RF_WRITE / RO 线控: force
      (c) 别的 mux 的输出（WL ~12/81）→ 展开上游 mux（cone）或 force 衔接网（force 模式）
    数据输入三来源：RW → RF_WRITE / RO 线控 → force / 级联衔接网 → force（需探针前缀）。
    """
    issues = []
    bindings, used_vars, data_keys = {}, [], []

    # 同组各行控制列与首行不一致（read_mux 记下的）→ 不静默吞掉
    if getattr(group, "ctrl_mismatch_rows", None):
        issues.append("mux 页第 %s 行的控制列(B~E)与本组首行不一致——同一输出的所有行控制信号必须相同，请核对 Excel"
                      % ",".join(str(r) for r in group.ctrl_mismatch_rows))

    # ── ① 控制信号：三来源分发（每个控制一个 driver）──
    ctrl_drivers = []
    if not group.ctrls:
        issues.append("没有控制信号（mux 页 B~E 列全空）——无法确定选路条件")
    for idx, ctrl in enumerate(group.ctrls):
        driver = _resolve_ctrl_driver(wb, resolver, ctrl, idx, "",
                                      [group.out_base.lower()], 0, issues)
        ctrl_drivers.append(driver)
        for k in driver["keys"]:
            bindings[k] = driver["bindings"][k]
            used_vars.append(k)

    # LPBT 兼容别名：第一个 logic 来源控制的 ctrl_sig/ctrl_node/line/local
    first_logic = next((d for d in ctrl_drivers if d["source"] == "logic"), None)
    ctrl_sig = first_logic["ctrl_sig"] if first_logic else None
    ctrl_node = first_logic.get("ctrl_node") if first_logic else None
    line = first_logic.get("line") if first_logic else None
    local = first_logic.get("local") if first_logic else None

    # ── ② 数据输入（A 列，剥 _to_mux 后解析）——三来源放行 ──
    #   RW 寄存器(*_local/*_lut) → RF_WRITE；RO 寄存器(线控 linectrl_*) → force；
    #   级联(logic/mux 输出的衔接网) → force 字面 _to_mux 网（需探针前缀，build 层把关）。
    #   旧版"必须 RW"硬门已按 WL 实证放开——LPBT 数据全 RW，行为不变。
    for i, case in enumerate(group.cases):
        key = DATA_KEY % i
        info = {"raw": case.input_raw, "base": case.input_base, "width": case.input_width,
                "msb": case.input_msb, "lsb": case.input_lsb}
        b = resolver.resolve(key, info)
        bindings[key] = b
        used_vars.append(key)
        data_keys.append(key)
        if not b.resolved:
            issues.append("数据输入 %s 未解析: %s" % (case.input_base, b.note or ""))

    # ── ③ case 值解析（保留 don't-care 位；位宽对控制拼接总宽校验）──
    parsed_cases = []
    total_w = group.ctrl_total_width
    for case in group.cases:
        try:
            cv, cw, dc = E.parse_case_literal(case.case_raw)
            parsed_cases.append((cv, cw, dc))
            if total_w and total_w > 1 and cw != total_w:
                if group.is_multi_ctrl:
                    issues.append("case 值 %s 位宽(%d)与控制信号拼接总宽(%d=%s)不一致——case 命中会错位"
                                  % (case.case_raw, cw, total_w,
                                     "+".join("%s[%d]" % (c.base, c.width) for c in group.ctrls)))
                else:
                    issues.append("case 值 %s 位宽(%d)与控制信号 %s 位宽(%d)不一致——case 命中会错位"
                                  % (case.case_raw, cw, group.ctrl_base, total_w))
        except E.ExprError as ex:
            parsed_cases.append(None)
            issues.append("case 值 %r 解析失败: %s" % (case.case_raw, ex))

    return {"bindings": bindings, "used_vars": used_vars, "data_keys": data_keys,
            "ctrl_drivers": ctrl_drivers,
            "ctrl_sig": ctrl_sig, "ctrl_node": ctrl_node, "line": line, "local": local,
            "parsed_cases": parsed_cases, "issues": issues}


# ───────────────────────────── 向量生成 ─────────────────────────────
def coverage_mode(mode, exhaustive):
    """覆盖度三档 → mux 生成模式（generator/GUI 共用此映射，口径必须一致）。

    精简(mode=min) → "min"；全面(mode=max) → "max"；穷举(exhaustive=True) → "exhaustive"。
    """
    if exhaustive:
        return "exhaustive"
    return "min" if str(mode).lower() == "min" else "max"


def make_mux_vectors(group, expansion, mode="min", max_tests=256):
    """为一个 mux 组生成测试向量（vectors.TestVector，assignments 键 = "c:*"/"d:*"）。

    形态分发（2026-06-03 第十四轮）：
      LPBT 形态（单控制 + logic 来源）→ 本函数的 line/local 双路径生成器（行为与历史版本完全一致）
      通用形态（WL：多控制 / 寄存器直出 / mux 级联 / RO 数据）→ _make_general_vectors

    LPBT 覆盖度三档（每升一档多抓一类真实故障）：
      "min"(精简)        = line 路径扫每 case（x 位取 0）+ 1 条另一路径抽测
                           → 抓"case 接到错的寄存器"
      "max"(全面)        = min + case don't-care 位展开 + 每 case 一轮反码数据
                           → 多抓"数据通路位坏死"
      "exhaustive"(穷举) = max + 另一条控制路径全扫每 case（取代单条抽测）
                           → 多抓"只在另一条物理驱动路径下出现的选路错"

    扫描路径优先 line（designer 配方：force 线控扫 case）；没有 line 用 local 扫。
    返回 (vectors, meta)。
    """
    drivers = expansion.get("ctrl_drivers")
    if drivers is not None and not (len(drivers) == 1 and drivers[0]["source"] == "logic"):
        return _make_general_vectors(group, expansion, mode, max_tests)

    line, local = expansion["line"], expansion["local"]
    data_keys = expansion["data_keys"]
    parsed = expansion["parsed_cases"]
    bindings = expansion["bindings"]

    scan_path = line or local
    other_path = local if scan_path is line else line

    # 互异值按"有效位宽"分配（声明位宽与 tmm/regmap 字段位宽取小），保证截断后仍互异
    data_values, collision = alloc_distinct_values(group, bindings, data_keys)
    # 反码数据轮（max/exhaustive）：互异值取反后仍互异、避 0
    if mode == "min":
        inv_values, inv_collision = None, False
    else:
        inv_values, inv_collision = alloc_inverted_values(group, data_values, bindings, data_keys)

    scan_kind = ("line" if scan_path is line else "local") if scan_path else None
    other_kind = None if other_path is None else ("local" if other_path is local else "line")
    meta = {
        "control": [p["key"] for p in (line, local) if p],
        "data": list(data_keys),
        "missing_vars": [], "total_bits": 0,
        "truncated": False, "dropped": 0, "exhaustive": mode != "min",
        "scan_path": scan_kind,
        "value_collision": collision or inv_collision,
        "case_map": [(group.cases[i].case_raw, group.cases[i].input_base, data_values[i])
                     for i in range(len(group.cases))],
        # 覆盖度三档的内容标记（报告/GUI 据此说明"当前档生成了什么"）
        "data_rounds": 1 if mode == "min" else 2,
        "other_path_scan": (None if other_path is None
                            else ("full" if mode == "exhaustive" else "probe")),
    }

    vectors = []
    if scan_path is None:
        return vectors, meta

    widths = {k: b.width for k, b in bindings.items()}
    out_mask = E.mask(group.out_width)
    state = {"idx": 0, "capped": False}

    def emit(path, alt_path, ci, cv, values, note):
        """生成一条向量。含两道闸：max_tests 上限；截断校验——控制值经路径寄存器位宽截断后
        必须仍命中本 case（case 比寄存器宽时截断会命中别的 case → 期望必错 → 丢弃并记录，
        不能静默生成污染仿真 log）。返回 False = 已到 max_tests 上限（调用方应停止）。"""
        if state["idx"] >= max_tests:
            state["capped"] = True
            return False
        cval, cw, dc = parsed[ci]
        pwidth = widths.get(path["key"], 1)
        driven = cv & E.mask(pwidth)
        if not E.case_matches(cval, cw, dc, driven):
            meta["dropped"] += 1
            meta["truncated"] = True
            meta.setdefault("dropped_reasons", []).append(
                "case %s: ctrl value 0x%X truncated by %d-bit register no longer hits this case"
                % (group.cases[ci].case_raw, cv, pwidth))
            return True
        assignments = _path_assignments(path, alt_path, cv, widths, data_keys, values)
        vectors.append(V.TestVector(state["idx"], assignments, values[ci] & out_mask,
                                    group.out_width, note=note))
        state["idx"] += 1
        return True

    # ── ① line 路径 case 扫描（min：x 位取 0；max/exhaustive：don't-care 位展开）──
    for ci, pc in enumerate(parsed):
        if pc is None:
            continue
        cval, cw, dc = pc
        ctrl_values = E.expand_case_values(cval, cw, dc)
        if mode == "min":
            ctrl_values = ctrl_values[:1]        # 精简：don't-care 位取 0（用户拍板）
        ok = True
        for cv in ctrl_values:
            ok = emit(scan_path, other_path, ci, cv,
                      data_values, "case %s -> %s (ctrl=0x%X via %s path)"
                      % (group.cases[ci].case_raw, group.cases[ci].input_base, cv, scan_kind))
            if not ok:
                break
        if not ok:
            break

    # ── ② 反码数据轮（max/exhaustive）：每 case 再测一次，数据寄存器写反码互异值 ──
    if inv_values is not None and not state["capped"]:
        for ci, pc in enumerate(parsed):
            if pc is None:
                continue
            cval, cw, dc = pc
            cv = E.expand_case_values(cval, cw, dc)[0]
            if not emit(scan_path, other_path, ci, cv,
                        inv_values, "case %s -> %s (ctrl=0x%X via %s path, inverted data)"
                        % (group.cases[ci].case_raw, group.cases[ci].input_base, cv, scan_kind)):
                break

    # ── ③ 另一条控制路径（exhaustive：全扫每 case；min/max：抽测 case[0]——designer 的 T16）──
    if other_path is not None and not state["capped"]:
        if mode == "exhaustive":
            stop = False
            for ci, pc in enumerate(parsed):
                if pc is None or stop:
                    continue
                cval, cw, dc = pc
                for cv in E.expand_case_values(cval, cw, dc):
                    if not emit(other_path, scan_path, ci, cv,
                                data_values, "ctrl via %s path: case %s -> %s (ctrl=0x%X, "
                                "full scan of the other physical path)"
                                % (other_kind, group.cases[ci].case_raw,
                                   group.cases[ci].input_base, cv)):
                        stop = True
                        break
        elif parsed and parsed[0] is not None:
            cval, cw, dc = parsed[0]
            cv = E.expand_case_values(cval, cw, dc)[0]
            emit(other_path, scan_path, 0, cv,
                 data_values, "ctrl via %s path: case %s -> %s (verify the other physical "
                 "path of the ctrl cascade)"
                 % (other_kind, group.cases[0].case_raw, group.cases[0].input_base))

    meta["truncated"] = meta["truncated"] or state["capped"]   # 丢弃向量与 max_tests 截断都算
    return vectors, meta


def _path_assignments(active_path, inactive_path, ctrl_value, widths, data_keys, data_values):
    """构造一个测试向量的全部输入赋值。

    active_path:   控制值从这条路送进去（控制变量按 ctrl_assign 选路，值变量 = ctrl_value）
    inactive_path: 另一条路的值变量 = ~ctrl_value（干扰值——若 RTL 错选了另一条路，
                   控制值不同 → case 命中不同 → 输出不同 → 测试抓到）
    数据寄存器 = 互异值。
    """
    assignments = {}
    for var, val in active_path["ctrl_assign"].items():
        assignments[CTRL_KEY % var] = val
    akey = active_path["key"]
    assignments[akey] = ctrl_value & E.mask(widths.get(akey, 1))
    if inactive_path is not None:
        ikey = inactive_path["key"]
        if ikey not in assignments:
            assignments[ikey] = (~ctrl_value) & E.mask(widths.get(ikey, 1))
    for k, v in zip(data_keys, data_values):
        assignments[k] = v
    return assignments


# ───────────────────────────── 通用向量生成（WL 形态） ─────────────────────────────
def _make_general_vectors(group, expansion, mode, max_tests):
    """通用向量生成：多控制拼接 / 寄存器直出控制 / mux 级联控制 / RO 线控数据。

    覆盖度三档（与 LPBT 口径对齐——每升一档多抓一类真实故障）：
      "min"(精简)        = 每 case 1 条（don't-care 位取 0）
                           → 抓"case 接到错的寄存器"
      "max"(全面)        = min + don't-care 位展开 + 每 case 一轮反码数据
                           → 多抓"数据通路位坏死"
      "exhaustive"(穷举) = 同全面（通用形态没有 LPBT 的"另一条物理控制路径"概念，
                           x 位已在全面档全展开；logic 来源控制用其可用的 line/local 任一路径驱动）

    向量结构与 LPBT 完全同形（TestVector / assignments / exp_value），
    只是控制赋值键按来源不同：'c:reg'（寄存器直出）/ 'c:A'（logic 路径）/ 'm<N>.*'（上游 mux 配方）。
    """
    drivers = expansion["ctrl_drivers"]
    data_keys = expansion["data_keys"]
    bindings = expansion["bindings"]
    parsed = expansion["parsed_cases"]
    out_mask = E.mask(group.out_width)
    widths = [d["width"] for d in drivers]

    # 互异值（与 LPBT 同一套分配器：有效位宽、避 0、碰撞检测）
    data_values, collision = alloc_distinct_values(group, bindings, data_keys)
    if mode == "min":
        inv_values, inv_collision = None, False
    else:
        inv_values, inv_collision = alloc_inverted_values(group, data_values, bindings, data_keys)

    ctrl_keys = []
    for d in drivers:
        ctrl_keys.extend(d["keys"])

    meta = {
        "control": ctrl_keys,
        "data": list(data_keys),
        "missing_vars": [], "total_bits": sum(widths),
        "truncated": False, "dropped": 0, "exhaustive": mode != "min",
        "scan_path": "direct",          # 通用形态：直接驱动控制（不是 line/local 双路径）
        "value_collision": collision or inv_collision,
        "case_map": [(group.cases[i].case_raw, group.cases[i].input_base, data_values[i])
                     for i in range(len(group.cases))],
        "data_rounds": 1 if mode == "min" else 2,
        "other_path_scan": None,
        "multi_ctrl": len(drivers) > 1,
        "ctrl_sources": [d["source"] for d in drivers],
    }

    vectors = []
    # 没有可用控制驱动（来源 unknown）→ 空向量（与 LPBT scan_path=None 同语义，build 据此跳过）
    if not drivers or any(d["source"] == "unknown" for d in drivers):
        meta["scan_path"] = None
        return vectors, meta

    state = {"idx": 0, "capped": False}
    src_desc = "+".join(sorted(set(meta["ctrl_sources"])))

    def emit(ci, cv, vals, note):
        """产出一条向量。三道闸：max_tests 上限；控制驱动失败丢弃；截断回验丢弃；寄存器冲突丢弃。"""
        if state["idx"] >= max_tests:
            state["capped"] = True
            return False
        # ① 拆分到各控制信号并按来源驱动（cv 是具体值，x 位已展开/取 0）
        subs = E.split_case_value(cv, 0, widths)
        assignments = {}
        driven_parts = []
        for d, (sv, _sdc) in zip(drivers, subs):
            driven, ok, why = _apply_ctrl_driver(assignments, d, sv)
            if not ok:
                meta["dropped"] += 1
                meta.setdefault("dropped_reasons", []).append(
                    "case %s: %s" % (group.cases[ci].case_raw, why))
                return True             # 丢弃这条但继续
            driven_parts.append((driven, d["width"], 0))
        # ② 截断回验：各控制实际驱到的值（可能被寄存器位宽截断）重拼后必须仍命中本 case
        driven_cv, _tw, _dc = E.concat_case_parts(driven_parts)
        cval, cw, dc = parsed[ci]
        if not E.case_matches(cval, cw, dc, driven_cv):
            meta["dropped"] += 1
            meta["truncated"] = True
            meta.setdefault("dropped_reasons", []).append(
                "case %s: ctrl value 0x%X truncated to 0x%X no longer hits this case"
                % (group.cases[ci].case_raw, cv, driven_cv))
            return True
        # ③ 数据互异值；同一物理寄存器被下游数据与上游载体写不同值 → 冲突丢弃（不静默生成）
        for k, v in zip(data_keys, vals):
            assignments[k] = v
        by_base = {}
        for k, v in assignments.items():
            b = bindings.get(k)
            if b is None:
                continue
            prev = by_base.get(b.base.lower())
            if prev is not None and prev != v:
                meta["dropped"] += 1
                meta.setdefault("dropped_reasons", []).append(
                    "case %s: 寄存器 %s 同时被下游数据与上游级联配方写不同值（冲突），该向量丢弃"
                    % (group.cases[ci].case_raw, b.base))
                return True
            by_base[b.base.lower()] = v
        vectors.append(V.TestVector(state["idx"], assignments, vals[ci] & out_mask,
                                    group.out_width, note=note))
        state["idx"] += 1
        return True

    # ── ① case 扫描（min：x 位取 0；max/exhaustive：don't-care 位展开）──
    for ci, pc in enumerate(parsed):
        if pc is None:
            continue
        cval, cw, dc = pc
        ctrl_values = E.expand_case_values(cval, cw, dc)
        if mode == "min":
            ctrl_values = ctrl_values[:1]            # 精简：don't-care 位取 0（用户拍板）
        ok = True
        for cv in ctrl_values:
            ok = emit(ci, cv, data_values,
                      "case %s -> %s (ctrl=0x%X, %s)"
                      % (group.cases[ci].case_raw, group.cases[ci].input_base, cv, src_desc))
            if not ok:
                break
        if not ok:
            break

    # ── ② 反码数据轮（max/exhaustive）：每 case 再测一次，数据寄存器写反码互异值 ──
    if inv_values is not None and not state["capped"]:
        for ci, pc in enumerate(parsed):
            if pc is None:
                continue
            cval, cw, dc = pc
            cv = E.expand_case_values(cval, cw, dc)[0]
            if not emit(ci, cv, inv_values,
                        "case %s -> %s (ctrl=0x%X, inverted data)"
                        % (group.cases[ci].case_raw, group.cases[ci].input_base, cv)):
                break

    meta["truncated"] = meta["truncated"] or state["capped"]
    return vectors, meta
