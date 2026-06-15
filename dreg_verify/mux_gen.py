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
from .resolver import _raw_has_to_mux

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


def _case_base_key(case, i, bindings, data_keys):
    """该 case 数据源的物理寄存器标识——必须与 emit 的 by_base 冲突检查【同口径】(b.base.lower())。

    同一物理寄存器喂多个 case 时（真表实证：d:0/d:1/d:2 全是同一个 *_t0 寄存器），它们在一条向量里
    只能拿【同一个值】；否则 emit 的 by_base 检查判"同寄存器被写不同值"→ 整条向量丢弃 → 全丢=空向量。
    """
    if bindings is not None and data_keys is not None and i < len(data_keys):
        b = bindings.get(data_keys[i])
        if b is not None and getattr(b, "base", None):
            return b.base.lower()
    return case.input_base.lower()


def _alloc_loop(group, bindings, data_keys, seed_fn, overrides=None, truncated=None):
    """互异值分配公共骨架：seed_fn(i, width, mask) 给初值，碰撞时 1..m 循环探测（永不取 0）。

    ⭐按【物理寄存器】分配，不是按 case 序号：同一寄存器喂多个 case 时复用同一个值。否则 emit 的
    by_base 冲突检查会因"同一寄存器被写不同值"把每条向量都丢弃 → 空向量（误报"控制无驱动路径"）。
    互异性只要求在【不同寄存器】之间成立——mux 选路只能区分到不同源，同源两 case 即便选错也输出同值
    （设计本就如此，非真故障，不可也不必区分）。collision 据此按【不同寄存器数】判，不再按 case 数。

    overrides（B2 用户手填数据值，第二十轮）：{物理基名(小写): int}。手填值【优先占位】（用户值
    sacrosanct），自动值绕开它们；手填值彼此撞 / 撞 0 / 撞自动值 → collision=True（由调用方决定
    阻断还是仅警告——手填撞值是用户的责任，按 [[gui-feedback]] 不静默）。
    返回 (values, collision)。collision=True 表示有效位宽装不下这么多互异值（或手填撞值）。
    """
    overrides = overrides or {}
    values, seen, collision = [], {0}, False
    by_base = {}                            # 物理寄存器基名 → 已分配值（同源 case 复用）
    # ── 第一遍：登记手填值（按该 base 有效位宽 mask），占住 seen 让自动值避开（用户值优先）──
    ov_norm, ov_seen = {}, set()            # 物理基名 → 已 mask 的手填值；ov_seen=手填值集合
    for i, case in enumerate(group.cases):
        bkey = _case_base_key(case, i, bindings, data_keys)
        if bkey in overrides and bkey not in ov_norm:
            w = _effective_width(case, i, bindings, data_keys)
            raw = overrides[bkey]
            masked = raw & E.mask(w)
            ov_norm[bkey] = masked
            if truncated is not None and raw >= 0 and raw != masked:
                truncated[bkey] = (raw, masked)   # 手填值超字段宽被静默截断 → 调用方出提示(#5)
    for v in ov_norm.values():
        # 仅当与【另一个手填值】相同才算撞（两路同值=不可区分=假绿）。手填值撞初始 0 占位【不算】
        # ——全1 取反=0 是 deterministic 的合法第二轮值，撞名义 0 不是真碰撞(#6 误报修复)；
        # 自动值仍靠 seen 绕开全部手填值(含 0)。
        if v in ov_seen:
            collision = True
        ov_seen.add(v)
        seen.add(v)
    # ── 第二遍：按 case 顺序分配（手填 base 用手填值，其余 seed+探测避开 seen）──
    for i, case in enumerate(group.cases):
        bkey = _case_base_key(case, i, bindings, data_keys)
        if bkey in by_base:
            values.append(by_base[bkey])    # 同一寄存器：复用同值，避开 by_base 冲突丢弃
            continue
        if bkey in ov_norm:
            by_base[bkey] = ov_norm[bkey]   # 手填值原样（已占 seen）
            values.append(ov_norm[bkey])
            continue
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
        by_base[bkey] = v
        values.append(v)
    return values, collision


def alloc_distinct_values(group, bindings=None, data_keys=None, overrides=None, truncated=None):
    """给每个数据寄存器分配互异值（designer 风格：4bit+ 从 0xA 递减；窄位宽从 1 递增）。

    互异是 mux 选路验证的命门：两条数据路同值时选错路也测不出（假绿）。
    避开 0（RTL mux 坏死输出 0 时不应误判 PASS）。overrides=用户手填值（见 _alloc_loop）；
    truncated=可选 dict，被填入"手填值超字段宽被截断"的 {base:(raw,masked)}（#5 诊断）。
    返回 (values, collision)。
    """
    return _alloc_loop(group, bindings, data_keys,
                       lambda i, w, m: ((0xA - i) & m) if w >= 4 else ((i + 1) & m),
                       overrides=overrides, truncated=truncated)


def alloc_inverted_values(group, base_values, bindings=None, data_keys=None, overrides=None):
    """反码数据轮：每个数据寄存器取第一轮值的按位取反（有效位宽内），保持互异、避 0。

    抓的故障：数据通路某位坏死(stuck-at)时，若第一轮写入值恰好等于坏死位的值则测不出；
    反码轮让每个寄存器的每一位都翻转过 → 两轮合起来每位都验过 0 和 1。
    overrides=用户手填值的【反码】（第二轮也尊重手填，保"每位翻转"覆盖，见调用方）。
    返回 (values, collision)。
    """
    return _alloc_loop(group, bindings, data_keys,
                       lambda i, w, m: (~base_values[i]) & m,
                       overrides=overrides)


def _marker_values(group, target_ci, bindings, data_keys, mark="ones"):
    """『点名法』一条 case 的数据赋值：被测 case 所属【寄存器】的所有 case=标记值(非0)，
    其余寄存器的 case 全=0(对照)。

    互异值装不下时(N 路 > 字段能放的互异值数 = 假绿)的破法：不再要求"全部 N 路同时互异"，
    改成"一次只点名测一路"——被测那路给标记、其余全给对照(0)。**只需 2 个互异值(标记≠0)，
    任何位宽都成立**。routing 故障(case 错接到别的源)→ 输出从标记变 0 → 必 FAIL；标记非 0 →
    输出坏死成 0 也抓到。逐 case 各一条，所有"单路选错"全覆盖，与互异值法故障覆盖等价。
    ⭐标记按【物理寄存器】给：被测寄存器喂的所有 case 都给标记（同源必同值，否则 by_base 冲突丢弃），
    只把【别的寄存器】的 case 置 0——选错到别的源才会从标记变 0 被抓；选到同源的另一 case 输出不变
    （设计本就同值，非真故障）。
    mark='ones' → 标记=有效位宽全 1（顺带验数据通路各位 stuck-at-0）；'one' → 标记=1（第二轮换值）。
    """
    target_base = _case_base_key(group.cases[target_ci], target_ci, bindings, data_keys)
    vals = []
    for i, case in enumerate(group.cases):
        if _case_base_key(case, i, bindings, data_keys) != target_base:
            vals.append(0)
            continue
        m = E.mask(_effective_width(case, i, bindings, data_keys))
        vals.append(m if mark == "ones" else (1 & m))
    return vals


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
        pending = []     # "输入未解析"延迟上报：仅当级联与 force 兜底都不可行时才算问题
        for letter in sorted(ctrl_bindings.keys()):
            key = key_prefix + ctrl_key(idx, letter)
            driver["bindings"][key] = ctrl_bindings[letter]
            driver["keys"].append(key)
            if not ctrl_bindings[letter].resolved:
                pending.append("控制信号 %s 的输入 %s=%s 未解析: %s"
                               % (ctrl.base, letter, ctrl_bindings[letter].base,
                                  ctrl_bindings[letter].note or ""))
        paths = discover_ctrl_paths(node, ctrl_bindings)
        for p in paths:
            p["key"] = key_prefix + ctrl_key(idx, p["var"])
        driver["line"] = next((p for p in paths if p["kind"] == "RO"), None)
        driver["local"] = next((p for p in paths if p["kind"] == "RW"), None)
        if driver["line"] is not None or driver["local"] is not None:
            # 级联可行（LPBT/designer 形态）：照旧上报未解析输入（不影响已选透传路径）
            issues.extend(pending)
            return driver
        # ── 无可透传路径：若控制原文是显式 _to_mux 衔接网，改 force 该网到 case 值 ──
        #   WL 实证（2026-06-04）：控制本身是内部 logic 输出（fb_en/tx_filter_en/rx_filter_en/
        #   vga_dpd_en），门控表达式 A|B|C / A&(B|C…) 的输入又是内部信号 → 级联透传必失败；
        #   但 mux 控制列写的是显式 X_to_mux 真网 → force 它到每 case 对应 bit 即可验选路
        #   （与数据侧 force `_to_mux` 网完全对称；按结构判定，自动覆盖任何同族控制）。
        #   logic 页输入从不带 _to_mux 后缀 → 此兜底对 LPBT/logic-cone 永不触发。
        if _raw_has_to_mux(ctrl.raw):
            fkey = key_prefix + ctrl_key(idx, "reg")
            finfo = {"raw": ctrl.raw, "base": ctrl.base, "width": ctrl.width,
                     "msb": ctrl.msb, "lsb": ctrl.lsb}
            fb = resolver.resolve(fkey, finfo)
            if fb.resolved:
                driver["source"] = "mux-force"
                driver["key"] = fkey
                driver["binding"] = fb
                driver["bindings"] = {fkey: fb}    # 重置：丢掉级联失败的内部输入绑定，只 force 这根网
                driver["keys"] = [fkey]
                return driver
        # 既不能级联也不能 force（连 _to_mux 衔接网都没有）：才是真问题
        issues.extend(pending)
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
        # ⭐ 下游控制是上游输出的【切片】(如 temp_code_to_mux[3:1], slice_lsb=1)时，case 选值 N 落在
        # 上游输出的 [msb:lsb] 位段——必须把它左移 slice_lsb 写进载体（否则上游输出=N、被切片成 N>>1，
        # 选错 case；mux56→mux157 实证）。slice_lsb=0（全宽控制）时为 no-op，不影响既有级联测试。
        driver["ctrl_slice_lsb"] = ctrl.lsb or 0
        # 切片必须落在上游输出位宽内——否则高位 case 选值会被静默截断丢成 truncated（审查 #1）。
        slice_msb = ctrl.msb if ctrl.msb is not None else (driver["ctrl_slice_lsb"] + ctrl.width - 1)
        if slice_msb >= upstream.out_width:
            issues.append("下游控制 %s 的切片 [%d:%d] 超出上游 mux%s 输出 %s 的位宽 %d——"
                          "高位 case 选值会被截掉(静默丢成 truncated)，请核对 Excel 切片或上游输出位宽"
                          % (ctrl.base, slice_msb, driver["ctrl_slice_lsb"],
                             upstream.group_no, upstream.out_base, upstream.out_width))
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


def _is_linectrl_base(b):
    """该绑定的物理基名是否命中线控族(tsensor/linectrl)——item④ alt 载体判别用。"""
    nm = (getattr(b, "base", None) or "").lower()
    return ("tsensor" in nm) or ("linectrl" in nm)


def _looks_like_twins(a, b):
    """两数据源名是否像『孪生』：共同前后缀占大头、仅中段一小处不同(如 1st↔2nd / 2g↔5g)。
    命中 → 规格冲突多半是 designer 把一个输出名复制粘贴到本该是【另一个 mux】的行上、漏改输出名
    （那其实是合法的『一个控制管多个 mux』），而非真矛盾。只作软提示，不改『跳过·待核对』去向。"""
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b or a == b:
        return False
    p = 0
    while p < len(a) and p < len(b) and a[p] == b[p]:
        p += 1
    s = 0
    while s < len(a) - p and s < len(b) - p and a[-1 - s] == b[-1 - s]:
        s += 1
    mid_a, mid_b = a[p:len(a) - s], b[p:len(b) - s]   # 各自的差异中段
    common = p + s
    return (common >= 6 and common >= 0.6 * max(len(a), len(b))
            and len(mid_a) <= 4 and len(mid_b) <= 4)


def mux_expandable(group):
    """该 mux 能否被「合成 AST + generate_vectors 泛化枚举」安全【且完整】地展开。全满足才 True，
    否则 cone 跨边界回退 force 衔接网(旧行为，需探针前缀)——宁可保守不展开，绝不产假红/假绿：

      ① 每个控制信号位宽 ≤ 2：generate_vectors 对 ≤2bit 控制全枚举，>2bit 只取 {0,全1}
         → 宽 select 的中间 case 永不被驱动=【假绿】(测试 PASS 却没测到)。
      ② 各 case 值位宽 == 控制拼接总宽：否则比较错位。
      ③ 各 case 覆盖的控制取值并集 == 全部 2^总宽 个值(无 hole)：否则枚举到「不命中任何 case」的
         hole 控制值时，合成 AST 的末位 catch-all 会拿末 case 数据当期望=【假红】(真 RTL 是 latch/x)。

    （这三条是本轮保守边界；宽 select / 有 default 的 mux 留下一轮按真实 case 值枚举。）"""
    ctrls = getattr(group, "ctrls", None) or []
    cases = getattr(group, "cases", None) or []
    if not ctrls or not cases:
        return False
    if any((c.width or 1) > 2 for c in ctrls):       # ① 宽 select
        return False
    total_w = group.ctrl_total_width
    covered = set()
    for case in cases:
        try:
            cv, cw, dc = E.parse_case_literal(case.case_raw)
        except E.ExprError:
            return False
        if cw != total_w:                            # ② case 位宽不符
            return False
        covered.update(E.expand_case_values(cv, cw, dc))
    return covered == set(range(1 << total_w))        # ③ 必须无 hole 全覆盖


def synthesize_mux_expr(wb, resolver, group, _depth=0, _stack=None, chain_out=None):
    """把一个 MuxGroup 编译成【等价 expr AST + 干净叶子 bindings】，供 cone 跨 logic↔mux 边界展开。

    返回 (node, leaves)：
      node   — 仅含 expr.py 七种节点的选路 AST：case(ctrl){v0:d0; v1:d1; …}
               编成嵌套三元 cond0?d0:(cond1?d1:…:d_last)（首次匹配优先=Verilog case 语义）；
               cond_i = (sel & care_i) == (cv_i & care_i)（无 don't-care 退化为 sel == cv_i）。
      leaves — {叶子大写基名: InputBinding}，控制位 + 各 case 数据寄存器/线控；控制/数据若又是
               logic/mux 输出 → 递归 cone.expand / synthesize_mux_expr（深链一路展到真寄存器/顶层
               线控），叶子键与 AST 变量名同口径(base.upper())。

    环/超深检测与 cone 统一：mux 名加 'mux:' 前缀进【共享】_stack（与 logic 裸基名不撞），深度共用
    cone.MAX_DEPTH；环/超深 → 抛 cone.ConeError（上层 expand_signal 兜底回退 force 衔接网）。
    只在 cascade_mode=='cone'（展开模式，默认=对齐 VBA）时被 cone 调用；force 模式 cone 把 mux 输出
    当叶子 force 衔接网（旧行为逐字节不变），不进这里。
    """
    from . import cone           # 惰性 import 破循环（cone 也 import mux_gen）

    stack = list(_stack or [])
    me = "mux:" + group.out_base.lower()
    if me in stack:
        raise cone.ConeError("mux 跨界级联成环: %s" % " → ".join(stack + [me]))
    if _depth > cone.MAX_DEPTH:
        raise cone.ConeError("cone 展开深度超过 %d（链: %s）"
                             % (cone.MAX_DEPTH, " → ".join(stack + [me])))
    stack = stack + [me]
    leaves = {}

    def _subtree(raw, base, width, msb, lsb, src):
        """一个控制/数据输入 → 子 AST，并把其叶子并入 leaves。
        base 命中 logic 行 → cone.expand 递归（mux←logic 对称缺口在此自动展开）；
        命中 mux 组 → synthesize 递归；否则 = 寄存器/线控叶子 Var(base.upper())。"""
        child_logic = cone._find_logic(wb, base)
        if child_logic is not None:
            cnode, cleaves = cone.expand(child_logic, wb, resolver, _depth + 1, stack, chain_out)
            for k, lf in cleaves.items():
                cone._merge_leaf(leaves, k, lf, getattr(lf, "xl_letters", None) or [])
            return E.Part(cnode, msb, lsb) if msb is not None else cnode
        child_mux = cone._find_mux(wb, base)
        if child_mux is not None:
            # 嵌套 mux 也必须可安全展开；不可展开 → 抛错让整条信号回退 force 衔接网(不半途产错位/漏测)
            if not mux_expandable(child_mux):
                raise cone.ConeError(
                    "嵌套 mux 组 %s 不可安全展开(宽 select/有 hole/case 位宽不符)，整条回退 force 衔接网"
                    % child_mux.group_no)
            cnode, cleaves = synthesize_mux_expr(wb, resolver, child_mux, _depth + 1, stack, chain_out)
            for k, lf in cleaves.items():
                cone._merge_leaf(leaves, k, lf, getattr(lf, "xl_letters", None) or [])
            return E.Part(cnode, msb, lsb) if msb is not None else cnode
        # 叶子：寄存器(RW→RF_WRITE) / 线控(RO→force 裸名)。叶子 binding 走 cone._full_binding(全字段宽，
        # 切片在 Var 层表达)，不传 self_base（mux 内部叶子与根 logic 输出无自引用）。
        b = resolver.resolve("mux_leaf_" + str(base).lower(),
                             {"raw": raw, "base": base, "width": width, "msb": msb, "lsb": lsb})
        key = str(base).upper()
        cone._merge_leaf(leaves, key, cone._full_binding(key, b, resolver), [src])
        return E.Var(key, msb, lsb)

    if not group.ctrls:
        raise cone.ConeError("mux 组 %s 无控制信号，无法合成选路 AST" % group.group_no)
    if not group.cases:
        raise cone.ConeError("mux 组 %s 无 case，无法合成选路 AST" % group.group_no)

    # ① 控制 sel 子树（多控制 B/C/D/E 拼接，B 在最高位 = Concat parts[0]）
    ctrl_nodes = [_subtree(c.raw, c.base, c.width, c.msb, c.lsb,
                           "mux%s.ctrl%d" % (group.group_no, i))
                  for i, c in enumerate(group.ctrls)]
    sel_node = ctrl_nodes[0] if len(ctrl_nodes) == 1 else E.Concat(list(ctrl_nodes))
    total_w = group.ctrl_total_width

    # ② 逐 case 数据子树 + case 值解码（don't-care 位记入 care 掩码；case 位宽必须 == 控制拼接总宽）
    data_nodes, conds = [], []
    for i, case in enumerate(group.cases):
        data_nodes.append(_subtree(case.input_raw, case.input_base, case.input_width,
                                   case.input_msb, case.input_lsb,
                                   "mux%s.d%d" % (group.group_no, i)))
        cv, cw, dc = E.parse_case_literal(case.case_raw)
        if cw != total_w:                       # case 位宽不符 → 跨边界展开会错位，回退 force 衔接网
            raise cone.ConeError("mux 组 %s case %s 位宽(%d)≠控制拼接总宽(%d)——跨边界展开会错位"
                                 % (group.group_no, case.case_raw, cw, total_w))
        conds.append((cv, E.mask(total_w) & ~dc))

    # ③ 嵌套三元（从最后一个 case 往前 fold → 靠前 case 在外层 then = 首次匹配优先=Verilog case）。
    #    始终用掩码比较 (sel & care) == (cv & care)：care 限定在 total_w 内，把展开后可能更宽的 sel 高位
    #    屏蔽掉(防 self_width≠声明宽时错位)，don't-care 位也只比 care 位。
    node = data_nodes[-1]                       # 最内层 els = 最后一个 case（默认/catch-all）
    for i in range(len(group.cases) - 2, -1, -1):
        cv, care = conds[i]
        cond = E.Binary("==", E.Binary("&", sel_node, E.Const(care, total_w)),
                        E.Const(cv & care, total_w))
        node = E.Ternary(cond, data_nodes[i], node)

    if chain_out is not None and all(c.get("out") != "mux%s" % group.group_no for c in chain_out):
        chain_out.append({"out": "mux%s" % group.group_no, "expr": group.expr,
                          "subst": E.to_text(node)})
    return node, leaves


def resolve_upstream_recipe(wb, resolver, upstream, _stack, _depth):
    """上游 mux 的『驱到任意目标值』配方（mux 级联，cone「展开上游」思路）。

    要让上游 mux 输出 == V：
      ① 选载体 case：第一个数据输入是位宽足够的干净寄存器(RW/RO)的 case
      ② 上游控制驱到载体 case 的值（递归三来源——上游控制还是 mux 就继续展开）
      ③ 载体数据寄存器写 V（_apply_ctrl_driver 在出向量时做）
    全部赋值键带 "m<上游组号>." 前缀与下游隔离。

    item④（第二十二轮）级联两分支：designer for_test 把级联 trim 的两支都当基线——
    mode=1 写 RW local(=主载体 carrier) + mode=0 force RO 线控(=备用载体 carrier_alt)。
    本函数并列产出两套载体；alt 轮只在 mux_mode≥全面 才由 _make_general_vectors 生成。

    返回 {'upstream','prefix','carrier_ci','carrier_key','carrier_eff_width',
          'carrier_alt_ci','carrier_alt_key','carrier_alt_eff_width','alt_skipped_reason',
          'ctrl_drivers'(上游的),'ctrl_values'(主载体子值),'ctrl_values_alt'(alt载体子值),
          'bindings','keys','issues'}
    """
    issues = []
    prefix = "m%s." % upstream.group_no
    out = {"upstream": upstream, "prefix": prefix,
           "carrier_ci": None, "carrier_key": None, "carrier_eff_width": 0,
           "carrier_alt_ci": None, "carrier_alt_key": None, "carrier_alt_eff_width": 0,
           "alt_skipped_reason": "", "alt_bare_prefix": "",
           "ctrl_drivers": [], "ctrl_values": [], "ctrl_values_alt": [],
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
    alt_prefix_needed = []   # 同形 5-tuple —— mode=0 备用载体是 logic→mux/级联衔接网(needs-prefix)：
                             #   force 基名钉不住准确层级，但它【正是 mode=0 要 force 的线控网】。配了 scan_rtl
                             #   探针前缀→解析成 prefixed-wire 落进 candidates；没配也【按裸名 force 生成】mode=0
                             #   (与 bare-probe 同口径：先出 testcase, 仿真真 CUVUNF 再配前缀)，下面标 alt_bare_prefix。
    for ci, case in enumerate(upstream.cases):
        key = prefix + (DATA_KEY % ci)
        info = {"raw": case.input_raw, "base": case.input_base, "width": case.input_width,
                "msb": case.input_msb, "lsb": case.input_lsb}
        b = resolver.resolve(key, info)
        if not b.resolved:
            continue
        if b.found_in in ("needs-prefix", "mux-output", "wire"):
            # 主载体必须稳定可写，这类网不当【主】载体；但非 RW 的线控/级联衔接网恰是 mode=0 force 的对象——
            # 留作"待前缀的 alt 候选"，没干净 alt 时用它【裸名 force 生成】mode=0(band_trim/mixer2g_cap_sw 实证)。
            eff_np = _effective_width(case, 0, {key: b}, [key])
            if (b.found_in in ("needs-prefix", "mux-output")
                    and not (b.kind == "RW" and b.address is not None)
                    and eff_np >= upstream.out_width):
                alt_prefix_needed.append((False, ci, key, b, eff_np))   # 与 candidates 同形 5-tuple
            continue            # 级联网/查无的 wire 不当【主】载体——主载体必须稳定可写
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

    # ── ①' 备用载体（item④ 级联两分支）：主载体优先 RW；alt = 另一个【非 RW】且命中
    #    tsensor/linectrl 的载体（mode=0 走 RO 线控 force）。M1 实证上游恰好 2 case
    #    (tsensor RO + local RW) → alt 唯一无歧义。多个非 RW 候选无法判别 → 跳过 alt 并记
    #    alt_skipped_reason（不进 issues、不阻断主轮；不靠 case 行序静默选错）。
    alt_cands = [c for c in candidates if c[1] != out["carrier_ci"] and not c[0]]   # 非主载体、非 RW
    named = [c for c in alt_cands if _is_linectrl_base(c[3])]
    alt_pick = None
    if len(named) == 1:
        alt_pick = named[0]
    elif not named and len(alt_cands) == 1:
        alt_pick = alt_cands[0]
    elif alt_cands:
        out["alt_skipped_reason"] = (
            "上游 mux%s 有 %d 个非 RW 载体候选，mode=0 备用分支(item④ RO 线控)无法判别，已跳过 alt 轮"
            % (upstream.group_no, len(alt_cands)))
    # 没有干净 alt 载体、但 mode=0 源是 logic→mux/级联衔接网(needs-prefix) → 仍【生成】mode=0：
    # 按裸名 force 该线控网(与 bare-probe 同口径——先把正确 testcase 出给 designer 看，仿真真 CUVUNF 再跑
    # scan_rtl 配前缀)，并标 alt_bare_prefix 警告(不静默、不跳过)。用户拍板"没前缀也要看见正确 testcase"。
    if alt_pick is None and not out["alt_skipped_reason"] and alt_prefix_needed:
        named_np = [c for c in alt_prefix_needed if _is_linectrl_base(c[3])]
        if len(named_np) == 1:
            alt_pick = named_np[0]
        elif not named_np and len(alt_prefix_needed) == 1:
            alt_pick = alt_prefix_needed[0]
        else:
            out["alt_skipped_reason"] = (
                "上游 mux%s 有 %d 个 needs-prefix 线控载体候选，mode=0 无法判别，已跳过 alt 轮"
                % (upstream.group_no, len(alt_prefix_needed)))
        if alt_pick is not None:
            out["alt_bare_prefix"] = alt_pick[3].base

    # ── ② 上游控制驱到载体 case 的值（递归三来源解析）；alt 载体同算一份上游子值 ──
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
    # alt 载体的上游控制子值（驱到 mode=0 那条 case）；解析/位宽不符 → 放弃 alt（不阻断主轮）
    alt_subs = None
    if alt_pick is not None:
        alt_case = upstream.cases[alt_pick[1]]
        try:
            acv, acw, adc = E.parse_case_literal(alt_case.case_raw)
            if not (upstream.ctrls and acw != upstream.ctrl_total_width):
                alt_subs = E.split_case_value(acv, adc, widths) if widths else []
        except E.ExprError:
            alt_subs = None
        if alt_subs is None and not out["alt_skipped_reason"]:
            out["alt_skipped_reason"] = ("上游 mux%s 的 mode=0 case 值无法解析，已跳过 alt 轮"
                                         % upstream.group_no)
    if alt_pick is not None and alt_subs is not None:
        _ar, aci, akey, ab, aeff = alt_pick
        out["carrier_alt_ci"], out["carrier_alt_key"] = aci, akey
        out["carrier_alt_eff_width"] = aeff
        out["bindings"][akey] = ab
        out["keys"].append(akey)
    for idx, ctrl in enumerate(upstream.ctrls):
        d = _resolve_ctrl_driver(wb, resolver, ctrl, idx, prefix, stack, _depth, issues)
        out["ctrl_drivers"].append(d)
        out["ctrl_values"].append(subs[idx][0])      # don't-care 位取 0
        if out["carrier_alt_ci"] is not None:
            out["ctrl_values_alt"].append(alt_subs[idx][0])
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


def _apply_ctrl_driver(assignments, driver, value, use_alt=False):
    """把一个控制驱动器驱到指定值（写 assignments）。

    返回 (实际驱到的值, ok, why_not)——实际值可能因寄存器位宽截断而不等于请求值，
    调用方用它做 case 命中回验（截断后命中错 case 的向量必须丢弃，不能静默生成）。

    use_alt（item④ 级联两分支）：仅对 src=='mux' 生效——True 时走备用载体 carrier_alt
    （mode=0、RO 线控 force），否则走主载体 carrier（mode=1、RW local）。非 mux 来源忽略此参。
    嵌套级联的上游控制仍走各自主载体（两分支只针对最近一层级联，与 M1 两级实证一致）。
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
        # item④：use_alt 走备用载体(mode=0 RO 线控)；否则主载体(mode=1 RW local)。
        # ⭐ 深层级联(第二十四轮)：mode mux 可能不在最近一层，而在更上游(如 lpf_cmain←corner_sel←bwctrl，
        #    mode mux=bwctrl 在第 2 层)。规则：本层有 alt 载体 → 在此消费 alt、上游控制走各自主载体；
        #    本层无 alt 但更深层有 → 本层走主载体，把 use_alt 透传给上游驱动，让它在持有 alt 的那层消费。
        used_alt_here = use_alt and recipe.get("carrier_alt_ci") is not None
        if used_alt_here:
            ckey, cci = recipe["carrier_alt_key"], recipe["carrier_alt_ci"]
            ceff, cvals = recipe["carrier_alt_eff_width"], recipe["ctrl_values_alt"]
        else:
            ckey, cci = recipe["carrier_key"], recipe["carrier_ci"]
            ceff, cvals = recipe["carrier_eff_width"], recipe["ctrl_values"]
        if ckey is None:
            return 0, False, ("控制信号 %s 的上游 mux%s 没有可用载体"
                              % (driver["base"], up.group_no))
        # 上游控制驱到载体 case（递归）；alt 已在本层消费则上游走主载体，否则把 use_alt 透传到更深层
        descend_alt = use_alt and not used_alt_here
        up_parts = []
        for ud, uv in zip(recipe["ctrl_drivers"], cvals):
            udv, ok, why = _apply_ctrl_driver(assignments, ud, uv, use_alt=descend_alt)
            if not ok:
                return 0, False, why
            up_parts.append((udv, ud["width"], 0))
        # ⭐ 回验上游是否真的命中载体 case：若上游控制被寄存器位宽截断成别的值，
        # 上游 mux 会选错 case（载体没被选中）→ 该向量必须丢弃，不能静默生成（不变量 B）。
        if up_parts:
            up_cv, _w, _dc = E.concat_case_parts(up_parts)
            carrier_case = up.cases[cci]
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
        # ⭐ slice 复合（mux56→mux157 实证）：下游控制是上游输出的切片 [msb:lsb] 时，case 选值 N
        # 落在上游输出的 [msb:lsb] 段——写载体前先左移 slice_lsb（选值 N → 上游输出 N<<slice_lsb），
        # 否则上游输出=N、被下游切成 N>>slice_lsb → 选错 case。slice_lsb=0（全宽）时为 no-op。
        slice_lsb = driver.get("ctrl_slice_lsb", 0)
        m = E.mask(min(ceff, up.out_width))
        driven = (value << slice_lsb) & m
        assignments[ckey] = driven
        # 回报值要【投影回切片前的坐标系】(>> slice_lsb)，与 case 字面量(post-slice 选值)同口径——
        # 否则 emit 的截断回验拿 driven(=N<<slice_lsb) 比 case(=N) 会全部判 miss 而误丢弃整组。
        return (driven >> slice_lsb), True, ""

    return 0, False, "控制信号 %s 来源未知（无法驱动）" % driver["base"]


def _recipe_has_alt(recipe):
    """级联配方树里【任意一层】有 mode=0 备用载体？深层级联(第二十四轮)：mode mux 可能不在最近
    一层(如 lpf_cmain←corner_sel←bwctrl 的 bwctrl 在第 2 层)——只查最近层会漏掉这半张表。"""
    if not recipe:
        return False
    if recipe.get("carrier_alt_ci") is not None:
        return True
    return any(d.get("source") == "mux" and _recipe_has_alt(d.get("recipe"))
               for d in recipe.get("ctrl_drivers", []))


def _recipe_alt_skips(recipe):
    """递归收集级联树里所有 alt 被跳过的原因（含深层 mode mux），供报告缺口可见(M2)。"""
    if not recipe:
        return []
    out = []
    if recipe.get("alt_skipped_reason"):
        out.append(recipe["alt_skipped_reason"])
    for d in recipe.get("ctrl_drivers", []):
        if d.get("source") == "mux":
            out.extend(_recipe_alt_skips(d.get("recipe")))
    return out


def _recipe_alt_bare(recipe):
    """递归收集级联树里『mode=0 用裸名 force 的线控源』基名（需 scan_rtl 配前缀确认层级）。"""
    if not recipe:
        return []
    out = []
    if recipe.get("alt_bare_prefix"):
        out.append(recipe["alt_bare_prefix"])
    for d in recipe.get("ctrl_drivers", []):
        if d.get("source") == "mux":
            out.extend(_recipe_alt_bare(d.get("recipe")))
    return out


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
    seen_data_bases = set()              # used_vars 按【物理寄存器】收拢：同一寄存器只列一行
    seen_unres_bases = set()
    for i, case in enumerate(group.cases):
        key = DATA_KEY % i
        info = {"raw": case.input_raw, "base": case.input_base, "width": case.input_width,
                "msb": case.input_msb, "lsb": case.input_lsb}
        b = resolver.resolve(key, info)
        bindings[key] = b
        data_keys.append(key)            # 逐 case 保留——向量生成按 ci 对齐（含死分支占位）
        # 显示/驱动渲染（used_vars）只列【唯一物理寄存器】：死分支重复行 / LUT 型同源多 case 不再重复
        # 显示成多行（与 for_test 一致，t0~t7 各一行）。compute_drives 本就按寄存器合并 → .sv 完全不变。
        bkey = (b.base or "").lower() or key
        if bkey not in seen_data_bases:
            seen_data_bases.add(bkey)
            used_vars.append(key)
        if not b.resolved and bkey not in seen_unres_bases:
            seen_unres_bases.add(bkey)   # 同一寄存器的"未解析"只报一次（不被死分支刷屏）
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

    # ── ④ 死分支/矛盾检测（Verilog case 先到先得 = 首次匹配优先，按【具体控制值】判，覆盖 don't-care 子集）──
    #    每个具体控制值由【最靠前】匹配它的 case 拥有。靠后 case 的具体值若已被更早 case 拥有：
    #      整条都被【同源】更早 case 拥有 → 纯死分支：shadowed 跳过 + 软提示（不阻断）。
    #      任一具体值被【不同源】更早 case 拥有 → 规格矛盾：同输入两期望必假红 → 硬报 issue
    #        （整条都死则也 shadowed，让 include_risky 放行时按先到先得只留前者、不产假红）。
    #      部分重叠且全同源 → 冗余无害（输出相同），仅软提示、照常生成。
    #    精确同键是"全覆盖"的特例（沿用 mixer2g_trim 实证行为）；并堵住"x 位 case 与窄 case 部分重叠"
    #    的隐藏假红（审查 #2/#3）。解析失败(None)的 case 不参与。
    shadowed = set()
    shadow_pairs = []                    # 同源全死分支 [(后 row, 先 row)]
    redundant_pairs = []                 # 同源部分重叠 [(后 row, 先 row)]（冗余无害）
    contradictions = []                  # 不同源重叠 [(后 row, 先 row, 后源, 先源)]
    claimed = {}                         # 具体控制值 -> (拥有它的 ci, 该 ci 的物理源 base_low)
    for ci, pc in enumerate(parsed_cases):
        if pc is None:
            continue
        cv, cw, dc = pc
        base_low = group.cases[ci].input_base.lower()
        try:
            vals = E.expand_case_values(cv, cw, dc)
        except E.ExprError:
            vals = [cv]
        owners = [claimed[v] for v in vals if v in claimed]      # 已被更早 case 拥有的具体值的 owner
        diff = next((o for o in owners if o[1] != base_low), None)
        full = owners and len(owners) == len(vals)
        if diff is not None:
            contradictions.append((group.cases[ci].row, group.cases[diff[0]].row,
                                   group.cases[ci].input_base, group.cases[diff[0]].input_base))
            if full:
                shadowed.add(ci)         # 整条死：先到先得只留前者（include_risky 放行时不产假红）
        elif full:
            shadowed.add(ci)             # 整条被同源更早 case 覆盖 → 纯死分支
            shadow_pairs.append((group.cases[ci].row, group.cases[owners[0][0]].row))
        elif owners:
            redundant_pairs.append((group.cases[ci].row, group.cases[owners[0][0]].row))
        for v in vals:
            claimed.setdefault(v, (ci, base_low))      # 首次匹配认领（不覆盖更早拥有者）
    owner = (getattr(group, "owner", "") or "").strip()
    # 结构化导出规格冲突：账目/报告/GUI 据此把"规格冲突"和缺前缀/假绿等其它跳过区分开（不靠串匹配）。
    # 两种成因都报给 designer：①真矛盾(同一 mux 同选择值两期望)→改数据源；②两个 mux 撞了同一输出名
    # (合法的『一个控制管多个 mux』，被复制粘贴漏改名)→改输出名。源名孪生时优先提示 ②(实证占多数)。
    spec_conflicts = []
    for later, first, lb, fb in contradictions:
        twins = _looks_like_twins(lb, fb)
        if twins:
            hint = ("两源名形如孪生——多半是同一输出名被复制粘贴到本该是【另一个 mux】的行上、漏改了输出名"
                    "（『一个控制信号管多个 mux』本身合法）：若属实，把靠后那段的输出名改对即可；"
                    "否则才是真规格矛盾，应改数据源。")
        else:
            hint = ("同一选择值 RTL 只能输出一个，规格矛盾：要么是真冲突(改数据源)，"
                    "要么是两个 mux 撞了同一输出名(『一个控制管多个 mux』合法，改输出名即可)。")
        issues.append("mux 页第 %s 行与第 %s 行有相同的控制选择值却选不同数据源（%s vs %s）——%s请核对 Excel%s"
                      % (later, first, lb, fb, hint, ("（owner: %s）" % owner) if owner else ""))
        spec_conflicts.append({"later_row": later, "first_row": first, "later_src": lb,
                               "first_src": fb, "owner": owner,
                               "likely_dup_name": twins, "hint": hint})
    note_bits = []
    if shadow_pairs:
        note_bits.append("死分支 %d 条（%s）：控制值被更靠前的同源 case 完全覆盖，Verilog 先到先得轮不到，已跳过其测试"
                         % (len(shadow_pairs), "、".join("第%s行←第%s行" % p for p in shadow_pairs)))
    if redundant_pairs:
        note_bits.append("冗余重叠 %d 条（%s）：与更靠前的同源 case 有重叠控制值（输出相同、无害），照常生成"
                         % (len(redundant_pairs), "、".join("第%s行∩第%s行" % p for p in redundant_pairs)))
    shadowed_note = "；".join(note_bits)
    contradiction_note = ("规格矛盾 %d 处：%s" % (len(contradictions),
                          "、".join("第%s行/第%s行选择值重叠却选不同源(%s vs %s)" % c for c in contradictions))
                          if contradictions else "")

    return {"bindings": bindings, "used_vars": used_vars, "data_keys": data_keys,
            "ctrl_drivers": ctrl_drivers, "contradiction_note": contradiction_note,
            "ctrl_sig": ctrl_sig, "ctrl_node": ctrl_node, "line": line, "local": local,
            "parsed_cases": parsed_cases, "issues": issues, "spec_conflicts": spec_conflicts,
            "shadowed": shadowed, "shadowed_note": shadowed_note}


# ───────────────────────────── 向量生成 ─────────────────────────────
def coverage_mode(mode, exhaustive):
    """覆盖度三档 → mux 生成模式（generator/GUI 共用此映射，口径必须一致）。

    精简(mode=min) → "min"；全面(mode=max) → "max"；穷举(exhaustive=True) → "exhaustive"。
    """
    if exhaustive:
        return "exhaustive"
    return "min" if str(mode).lower() == "min" else "max"


def make_mux_vectors(group, expansion, mode="min", max_tests=256, data_overrides=None):
    """为一个 mux 组生成测试向量（vectors.TestVector，assignments 键 = "c:*"/"d:*"）。

    data_overrides（B2，第二十轮）：{物理基名(小写): int} 用户手填的数据值，替换自动互异/标记值；
    撞值降级为非阻断警告（meta['override_collision']，用户负责）。None=全自动（行为不变）。

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
        return _make_general_vectors(group, expansion, mode, max_tests, data_overrides)

    line, local = expansion["line"], expansion["local"]
    data_keys = expansion["data_keys"]
    parsed = expansion["parsed_cases"]
    bindings = expansion["bindings"]
    shadowed = expansion.get("shadowed") or set()    # 死分支 case：跳过、不生成冗余向量

    scan_path = line or local
    other_path = local if scan_path is line else line

    # 互异值按"有效位宽"分配（声明位宽与 tmm/regmap 字段位宽取小），保证截断后仍互异
    has_ov = bool(data_overrides)
    ov_trunc = {}
    data_values, collision = alloc_distinct_values(group, bindings, data_keys,
                                                   overrides=data_overrides, truncated=ov_trunc)
    # ⭐ 字段容量碰撞独立于手填判定（审查 #4/#10：单个手填不该掩盖窄字段假绿）：用【无 override】的
    # 自动分配复测——auto_cap=True 表示字段本身装不下互异值（与手填无关），仍按容量假绿阻断。
    auto_cap = collision if not has_ov else alloc_distinct_values(group, bindings, data_keys)[1]
    # 反码数据轮（max/exhaustive）：互异值取反后仍互异、避 0；手填值第二轮也用其反码
    if mode == "min":
        inv_values, inv_collision = None, False
    else:
        inv_ov = {b: (~v) for b, v in (data_overrides or {}).items()}
        inv_values, inv_collision = alloc_inverted_values(group, data_values, bindings, data_keys,
                                                          overrides=inv_ov)

    scan_kind = ("line" if scan_path is line else "local") if scan_path else None
    other_kind = None if other_path is None else ("local" if other_path is local else "line")
    meta = {
        "control": [p["key"] for p in (line, local) if p],
        "data": list(data_keys),
        "missing_vars": [], "total_bits": 0,
        "truncated": False, "dropped": 0, "exhaustive": mode != "min",
        "scan_path": scan_kind,
        # 容量碰撞(auto_cap)=字段太窄装不下互异值 → 阻断(假绿)，手填掩盖不了；
        # 仅当碰撞【非容量、纯手填值之间引起】才降级为非阻断 override_collision 警告（用户负责）。
        "value_collision": (collision or inv_collision) if (auto_cap or not has_ov) else False,
        "override_collision": bool(has_ov and (collision or inv_collision) and not auto_cap),
        "override_truncated": dict(ov_trunc),
        "data_overrides": dict(data_overrides or {}),
        "case_map": [(group.cases[i].case_raw, group.cases[i].input_base, data_values[i])
                     for i in range(len(group.cases))],
        "shadowed": sorted(shadowed),                 # 死分支 case 序号（GUI/HTML 标注用）
        "shadowed_note": expansion.get("shadowed_note", ""),
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
                                    group.out_width, note=note, case_index=ci))
        state["idx"] += 1
        return True

    # ── ① line 路径 case 扫描（min：x 位取 0；max/exhaustive：don't-care 位展开）──
    for ci, pc in enumerate(parsed):
        if pc is None or ci in shadowed:           # 死分支：先到先得，靠后同值 case 不生成
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
            if pc is None or ci in shadowed:
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
                if pc is None or stop or ci in shadowed:
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
def _make_general_vectors(group, expansion, mode, max_tests, data_overrides=None):
    """通用向量生成：多控制拼接 / 寄存器直出控制 / mux 级联控制 / RO 线控数据。

    data_overrides（B2）：{物理基名(小写): int} 用户手填数据值——尊重用户字面值，不走点名法，
    撞值降级为非阻断警告（meta['override_collision']）。

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
    shadowed = expansion.get("shadowed") or set()    # 死分支 case：跳过、不生成冗余向量
    out_mask = E.mask(group.out_width)
    widths = [d["width"] for d in drivers]

    # 互异值（与 LPBT 同一套分配器：有效位宽、避 0、碰撞检测）
    has_ov = bool(data_overrides)
    ov_trunc = {}
    data_values, collision = alloc_distinct_values(group, bindings, data_keys,
                                                   overrides=data_overrides, truncated=ov_trunc)
    # ⭐ 字段容量碰撞独立于手填判定（审查 #4/#8/#10：单个手填不该关掉窄字段的点名法保护→假绿）：
    # 用【无 override】的自动分配复测——auto_cap=True 表示字段本身装不下互异值（与手填无关）。
    auto_cap = collision if not has_ov else alloc_distinct_values(group, bindings, data_keys)[1]
    # 装不下(auto_cap) → 点名法逐 case 生成(被测=标记/其余=0)，任何位宽都成立、不跳过。点名法对
    # 字段容量假绿的保护【不因手填关闭】；此时手填值在点名法下不生效（用 0/标记，下面 note 提示）。
    marker_method = auto_cap
    if mode == "min" or marker_method:
        inv_values, inv_collision = None, False
    else:
        inv_ov = {b: (~v) for b, v in (data_overrides or {}).items()}
        inv_values, inv_collision = alloc_inverted_values(group, data_values, bindings, data_keys,
                                                          overrides=inv_ov)

    ctrl_keys = []
    for d in drivers:
        ctrl_keys.extend(d["keys"])

    meta = {
        "control": ctrl_keys,
        "data": list(data_keys),
        "missing_vars": [], "total_bits": sum(widths),
        "truncated": False, "dropped": 0, "exhaustive": mode != "min",
        "scan_path": "direct",          # 通用形态：直接驱动控制（不是 line/local 双路径）
        # 容量假绿走点名法(marker_method)保护、不跳过；仅当碰撞【非容量、纯手填值之间】才降级为
        # 非阻断 override_collision 警告（用户负责）。marker_method 不因手填关闭(审查 #4/#8/#10)。
        "value_collision": False if (marker_method or has_ov) else (collision or inv_collision),
        "override_collision": bool(has_ov and (collision or inv_collision) and not auto_cap),
        "override_ignored_marker": bool(has_ov and marker_method),   # 手填值因点名法保护未生效
        "override_truncated": dict(ov_trunc),
        "data_overrides": dict(data_overrides or {}),
        "marker_method": marker_method,  # True=字段太窄、改点名法生成（替代假绿跳过）
        "case_map": [(group.cases[i].case_raw, group.cases[i].input_base,
                      (E.mask(_effective_width(group.cases[i], i, bindings, data_keys))
                       if marker_method else data_values[i]))
                     for i in range(len(group.cases))],
        "shadowed": sorted(shadowed),                 # 死分支 case 序号（GUI/HTML 标注用）
        "shadowed_note": expansion.get("shadowed_note", ""),
        "data_rounds": (1 if mode == "min" else 2),
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

    def emit(ci, cv, vals, note, use_alt=False):
        """产出一条向量。三道闸：max_tests 上限；控制驱动失败丢弃；截断回验丢弃；寄存器冲突丢弃。
        use_alt（item④）：级联控制改走备用载体(mode=0、RO 线控 force)——透传给 _apply_ctrl_driver。"""
        if state["idx"] >= max_tests:
            state["capped"] = True
            return False
        # ① 拆分到各控制信号并按来源驱动（cv 是具体值，x 位已展开/取 0）
        subs = E.split_case_value(cv, 0, widths)
        assignments = {}
        driven_parts = []
        for d, (sv, _sdc) in zip(drivers, subs):
            driven, ok, why = _apply_ctrl_driver(assignments, d, sv, use_alt=use_alt)
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
                                    group.out_width, note=note, case_index=ci))
        state["idx"] += 1
        return True

    # ── ① case 扫描（min：x 位取 0；max/exhaustive：don't-care 位展开）──
    for ci, pc in enumerate(parsed):
        if pc is None or ci in shadowed:             # 死分支：先到先得，靠后同值 case 不生成
            continue
        cval, cw, dc = pc
        ctrl_values = E.expand_case_values(cval, cw, dc)
        if mode == "min":
            ctrl_values = ctrl_values[:1]            # 精简：don't-care 位取 0（用户拍板）
        vals = (_marker_values(group, ci, bindings, data_keys, "ones")
                if marker_method else data_values)
        dsc = "点名法:被测=标记/其余=0" if marker_method else src_desc
        ok = True
        for cv in ctrl_values:
            ok = emit(ci, cv, vals,
                      "case %s -> %s (ctrl=0x%X, %s)"
                      % (group.cases[ci].case_raw, group.cases[ci].input_base, cv, dsc))
            if not ok:
                break
        if not ok:
            break

    # ── ② 第二轮（max/exhaustive）：反码数据轮；点名法则换第二个标记值(位翻转覆盖) ──
    second = ("marker" if (marker_method and mode != "min")
              else ("inv" if inv_values is not None else None))
    if second and not state["capped"]:
        for ci, pc in enumerate(parsed):
            if pc is None or ci in shadowed:
                continue
            cval, cw, dc = pc
            cv = E.expand_case_values(cval, cw, dc)[0]
            vals = (_marker_values(group, ci, bindings, data_keys, "one")
                    if second == "marker" else inv_values)
            tag = "点名法第二轮:标记=1" if second == "marker" else "inverted data"
            if not emit(ci, cv, vals,
                        "case %s -> %s (ctrl=0x%X, %s)"
                        % (group.cases[ci].case_raw, group.cases[ci].input_base, cv, tag)):
                break

    # ── ③ 级联 alt 分支轮（item④，全面/穷举）：cascade 控制改走【备用载体】(mode=0、RO 线控 force)──
    #    designer for_test 把级联 trim 两支都当基线：主轮①②走 mode=1 写 RW local；这轮补 mode=0
    #    force tsensor/linectrl 的另半张表，对齐 16 拍签核表。仅当存在带 carrier_alt 的 mux 控制时跑；
    #    全面档 alt 仅主选值(DC=0)、穷举档 alt 才 DC 全展(与主支对称)。RO 源网 force 由其绑定 kind
    #    在渲染层决定（与数据侧 RO 线控同口径）。
    has_alt = any(d["source"] == "mux" and _recipe_has_alt(d["recipe"]) for d in drivers)
    if mode != "min" and has_alt and not state["capped"]:
        meta["cascade_alt"] = True
        for ci, pc in enumerate(parsed):
            if pc is None or ci in shadowed:
                continue
            cval, cw, dc = pc
            alt_cvs = E.expand_case_values(cval, cw, dc)
            if mode != "exhaustive":
                alt_cvs = alt_cvs[:1]                  # 全面：DC 位取 0；穷举：DC 全展
            vals = (_marker_values(group, ci, bindings, data_keys, "ones")
                    if marker_method else data_values)
            stop = False
            for cv in alt_cvs:
                if not emit(ci, cv, vals,
                            "case %s -> %s (ctrl=0x%X, alt载体 mode=0/RO 线控 force)"
                            % (group.cases[ci].case_raw, group.cases[ci].input_base, cv),
                            use_alt=True):
                    stop = True
                    break
            if stop:
                break
    # mode=0 用裸名 force 的线控源（需 scan_rtl 配前缀确认层级）汇总进 meta（.sv 块顶 + 报告警告用）
    alt_bare = []
    for d in drivers:
        if d["source"] == "mux":
            alt_bare.extend(_recipe_alt_bare(d["recipe"]))
    if alt_bare:
        meta["cascade_alt_bare"] = "、".join(sorted(set(alt_bare)))
    # alt 被跳过(多候选无法判别 / mode=0 case 不可解析)的原因汇总进 meta（M2 可见性）
    alt_skips = []
    for d in drivers:
        if d["source"] == "mux":
            alt_skips.extend(_recipe_alt_skips(d["recipe"]))
    if alt_skips:
        meta["cascade_alt_skipped"] = "；".join(sorted(set(alt_skips)))

    meta["truncated"] = meta["truncated"] or state["capped"]
    return vectors, meta
