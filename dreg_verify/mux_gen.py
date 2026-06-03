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

测试集（覆盖度，用户拍板）：
  精简(min)         = 每 case 一个控制值（x 位取 0）  + 1 个"另一条路径"测试
  全面/穷举(max)    = 每 case × 每个 don't-care 位取值 + 1 个"另一条路径"测试
  mux 输出不做 iddq 测试（designer 没做）。

⭐ 架构要点（绕开"per-vector 驱动切换"的结构性难题）：
  mux 的"输入集" = 控制行的 logic 输入（键 "c:<字母>"）+ 数据寄存器（键 "d:<序号>"），
  每个输入有固定的 InputBinding（kind 不变）→ line/local 切换 = 改"值"而不是改"驱动方式"
  → sv_writer.compute_drives / 反例 / owner / 汇总计数器机制全部直接复用。
"""

import itertools

from . import expr as E
from . import vectors as V

# assignment 键前缀：控制行 logic 输入 = "c:<字母>"，数据寄存器 = "d:<case 序号>"
CTRL_KEY = "c:%s"
DATA_KEY = "d:%d"


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
def alloc_distinct_values(group, bindings=None, data_keys=None):
    """给每个数据寄存器分配互异值（designer 风格：4bit+ 从 0xA 递减；窄位宽从 1 递增）。

    互异是 mux 选路验证的命门：两条数据路同值时选错路也测不出（假绿）。
    避开 0（RTL mux 坏死输出 0 时不应误判 PASS）。

    ⭐ 位宽口径 = 有效位宽 = min(Excel A 列声明位宽, tmm/regmap 实际字段位宽)：
    写寄存器时 compute_drives 按实际字段位宽截断（sv_writer fw mask），
    互异性必须在**截断后**仍成立——按声明位宽分配再被截断会静默撞值。
    bindings/data_keys 不给时退化为按声明位宽（仅单元测试用）。

    返回 (values, collision)。collision=True 表示有效位宽装不下这么多互异值。
    """
    values, seen, collision = [], {0}, False
    for i, case in enumerate(group.cases):
        w = max(case.input_width, 1)
        if bindings is not None and data_keys is not None and i < len(data_keys):
            b = bindings.get(data_keys[i])
            if b is not None and b.reg_msb is not None and b.reg_lsb is not None:
                fw = b.reg_msb - b.reg_lsb + 1
                w = max(min(w, fw), 1)
        m = E.mask(w)
        v = ((0xA - i) & m) if w >= 4 else ((i + 1) & m)
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


# ───────────────────────────── 解析一个 mux 组 ─────────────────────────────
def expand_mux_group(wb, resolver, group):
    """解析一个 mux 组的全部驱动绑定与 case 值。

    返回 dict {
      'bindings':  {键: InputBinding}    控制行输入("c:<字母>") + 数据寄存器("d:<序号>")
      'used_vars': [键]                  控制输入在前、数据在后
      'data_keys': [键]                  与 group.cases 一一对应
      'ctrl_sig':  控制信号的 LogicSignal（None=衔接断裂）
      'ctrl_node': 控制表达式 AST
      'line':      line 路径 dict 或 None    {'var','kind','ctrl_assign','key'}
      'local':     local 路径 dict 或 None
      'parsed_cases': [(value,width,dc_mask) 或 None]   与 group.cases 一一对应
      'issues':    [str]                 非空 = 有解析问题（build 据此跳过并给原因）
    }
    """
    issues = []
    bindings, used_vars, data_keys = {}, [], []

    # ── ① 控制信号 → logic 页 to_mux 行（mux↔logic 衔接点）──
    ctrl_sig = None
    for s in wb.logic:
        if s.out_base.lower() == group.ctrl_base.lower():
            ctrl_sig = s
            break
    ctrl_node, line, local = None, None, None
    if ctrl_sig is None:
        issues.append("控制信号 %s 在 logic 页找不到对应行（mux<->logic 衔接断裂，核对 mux 页 B 列拼写）"
                      % group.ctrl_base)
    else:
        try:
            ctrl_node = E.parse(ctrl_sig.expr)
        except E.ExprError as ex:
            ctrl_node = None
            issues.append("控制信号 %s 的表达式 %r 解析失败: %s"
                          % (group.ctrl_base, ctrl_sig.expr, ex))
        if ctrl_node is not None:
            ctrl_bindings = resolver.resolve_signal_inputs(ctrl_sig)
            for letter in sorted(ctrl_bindings.keys()):
                key = CTRL_KEY % letter
                bindings[key] = ctrl_bindings[letter]
                used_vars.append(key)
                if not ctrl_bindings[letter].resolved:
                    issues.append("控制信号 %s 的输入 %s=%s 未解析: %s"
                                  % (group.ctrl_base, letter, ctrl_bindings[letter].base,
                                     ctrl_bindings[letter].note or ""))
            paths = discover_ctrl_paths(ctrl_node, ctrl_bindings)
            for p in paths:
                p["key"] = CTRL_KEY % p["var"]
            line = next((p for p in paths if p["kind"] == "RO"), None)
            local = next((p for p in paths if p["kind"] == "RW"), None)
            if line is None and local is None:
                issues.append("控制信号 %s 的表达式 %r 没有可透传的驱动路径（无法把控制驱到指定 case 值）"
                              % (group.ctrl_base, ctrl_sig.expr))

    # ── ② 数据寄存器（A 列，剥 _to_mux 后查 tmm/regmap → RF_WRITE）──
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
        elif b.kind != "RW" or b.address is None:
            issues.append("数据输入 %s 不是带地址的 RW 寄存器(kind=%s, addr=%s)——写不进互异值，选路不可验证"
                          % (case.input_base, b.kind, b.address))

    # ── ③ case 值解析（保留 don't-care 位）──
    parsed_cases = []
    for case in group.cases:
        try:
            cv, cw, dc = E.parse_case_literal(case.case_raw)
            parsed_cases.append((cv, cw, dc))
            if group.ctrl_width and group.ctrl_width > 1 and cw != group.ctrl_width:
                issues.append("case 值 %s 位宽(%d)与控制信号 %s 位宽(%d)不一致——case 命中会错位"
                              % (case.case_raw, cw, group.ctrl_base, group.ctrl_width))
        except E.ExprError as ex:
            parsed_cases.append(None)
            issues.append("case 值 %r 解析失败: %s" % (case.case_raw, ex))

    return {"bindings": bindings, "used_vars": used_vars, "data_keys": data_keys,
            "ctrl_sig": ctrl_sig, "ctrl_node": ctrl_node, "line": line, "local": local,
            "parsed_cases": parsed_cases, "issues": issues}


# ───────────────────────────── 向量生成 ─────────────────────────────
def make_mux_vectors(group, expansion, mode="min", max_tests=256):
    """为一个 mux 组生成测试向量（vectors.TestVector，assignments 键 = "c:*"/"d:*"）。

    扫描路径优先 line（designer 配方：force 线控扫 case）；没有 line 用 local 扫。
    末尾追加一个"另一条路径"测试（designer 的 T16：控制走 local 路径也要通）。
    返回 (vectors, meta)。
    """
    line, local = expansion["line"], expansion["local"]
    data_keys = expansion["data_keys"]
    parsed = expansion["parsed_cases"]
    bindings = expansion["bindings"]

    scan_path = line or local
    other_path = local if scan_path is line else line

    # 互异值按"有效位宽"分配（声明位宽与 tmm/regmap 字段位宽取小），保证截断后仍互异
    data_values, collision = alloc_distinct_values(group, bindings, data_keys)

    meta = {
        "control": [p["key"] for p in (line, local) if p],
        "data": list(data_keys),
        "missing_vars": [], "total_bits": 0,
        "truncated": False, "dropped": 0, "exhaustive": mode != "min",
        "scan_path": ("line" if scan_path is line else "local") if scan_path else None,
        "value_collision": collision,
        "case_map": [(group.cases[i].case_raw, group.cases[i].input_base, data_values[i])
                     for i in range(len(group.cases))],
    }

    vectors = []
    if scan_path is None:
        return vectors, meta

    widths = {k: b.width for k, b in bindings.items()}
    out_mask = E.mask(group.out_width)
    idx = 0
    truncated = False

    # ── case 扫描 ──
    scan_key = scan_path["key"]
    scan_width = widths.get(scan_key, 1)
    for ci, pc in enumerate(parsed):
        if pc is None:
            continue
        cval, cw, dc = pc
        ctrl_values = E.expand_case_values(cval, cw, dc)
        if mode == "min":
            ctrl_values = ctrl_values[:1]        # 精简：don't-care 位取 0（用户拍板）
        for cv in ctrl_values:
            if idx >= max_tests:
                truncated = True
                break
            # ⭐ 截断校验：控制值经值寄存器位宽截断后必须仍命中本 case。
            # case 写得比控制信号宽时(位宽不一致)，截断后会命中别的 case → 期望错 → 误报失败。
            # 这种"确定错误的向量"直接丢弃并记录（不能静默生成污染仿真 log）。
            driven = cv & E.mask(scan_width)
            if not E.case_matches(cval, cw, dc, driven):
                meta["dropped"] += 1
                meta["truncated"] = True
                meta.setdefault("dropped_reasons", []).append(
                    "case %s: ctrl value 0x%X truncated by %d-bit register no longer hits this case"
                    % (group.cases[ci].case_raw, cv, scan_width))
                continue
            assignments = _path_assignments(scan_path, other_path, cv, widths,
                                            data_keys, data_values)
            exp = data_values[ci] & out_mask
            note = ("case %s -> %s (ctrl=0x%X via %s path)"
                    % (group.cases[ci].case_raw, group.cases[ci].input_base,
                       cv, meta["scan_path"]))
            vectors.append(V.TestVector(idx, assignments, exp, group.out_width, note=note))
            idx += 1
        if truncated and idx >= max_tests:
            break

    # ── 另一条路径验证（designer 的 T16：控制级联的两条物理路径都要通）──
    if other_path is not None and parsed and parsed[0] is not None and not truncated:
        cval, cw, dc = parsed[0]
        cv = E.expand_case_values(cval, cw, dc)[0]
        assignments = _path_assignments(other_path, scan_path, cv, widths,
                                        data_keys, data_values)
        exp = data_values[0] & out_mask
        other_kind = "local" if other_path is local else "line"
        note = ("ctrl via %s path: case %s -> %s (verify the other physical path of the ctrl cascade)"
                % (other_kind, group.cases[0].case_raw, group.cases[0].input_base))
        vectors.append(V.TestVector(idx, assignments, exp, group.out_width, note=note))

    meta["truncated"] = meta["truncated"] or truncated   # 丢弃向量(截断校验)与 max_tests 截断都算
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
