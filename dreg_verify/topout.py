# -*- coding: utf-8 -*-
"""
topout.py — 【Topout-rooted】验证引擎（新模型，2026-06-23 起，additive 新增，不动旧 logic-rooted 路径）。

旧模型：以 logic 页每一行 / mux 页每一组为『要验信号』，逐行枚举。
新模型（用户 2026-06-23 拍板，main 页版本史 + inspect dump 实证）：
  · 『要验什么』只在 **Topout 页 B 列**（~211 个顶层真名，无中间路由后缀或带 _ls）；
    logic N(top_output) / mux I(top_out) 在真表里【全 0】，旧工具拿它们当"要验输出"全判错。
  · 每个 Topout 信号 → 找回它在 logic/mux/寄存器各页的【源对象】→ 复用现有跨页 cone 引擎
    (cone.expand / mux_gen.synthesize / expand_mux_group) 展到源头寄存器 → 复用 vectors.py 出真值表。
  · 叶子分类沿用现有 resolver：RW→RF_WRITE(addr/bits)、RO/线控→force(裸名)。
  · 断言贴 Topout 顶层真名（无前后缀）——prefix/suffix 整类问题在源对象展到底后消失。

本模块【只新增】：解析 Topout B 列 → 根对象（excel_model.read_topout 已读成 wb.topout）→
分类(logic / mux / 直连寄存器 / RO 回读 / 未解析) → 建 cone/真值表。完全复用既有引擎，
不重写跨页展开；旧路径(generator 的 build/report 迭代 wb.logic/wb.mux)一字不动。
"""

from . import cone
from . import expr as E
from . import generator as G
from . import mux_gen
from . import vectors as V


# ───────────────────────────── 根对象分类 ─────────────────────────────
LOGIC = "logic"
MUX = "mux"
REGISTER = "register"          # 直连寄存器(RW)：dft 恒等观测把寄存器字段直接当顶层输出，可驱可验
RO_READBACK = "ro-readback"    # RO 回读(pll_lock_indicator 等)：模拟/FSM 状态，非寄存器组合函数→无 cone
UNRESOLVED = "unresolved"      # Topout 名在 logic/mux/寄存器各页都找不到


class TopoutRoot:
    """一个 Topout 信号解析到的【源对象】及其分类。"""

    def __init__(self, kind, obj=None, matched_name=None, reg_kind=None, note=""):
        self.kind = kind                 # LOGIC / MUX / REGISTER / RO_READBACK / UNRESOLVED
        self.obj = obj                   # LogicSignal / MuxGroup / RegmapEntry|TmmField / None
        self.matched_name = matched_name # 实际命中的候选名（out_base / _ls_name / 字段名）
        self.reg_kind = reg_kind         # 直连寄存器时 'RW'/'RO'
        self.note = note

    def __repr__(self):
        return "TopoutRoot(%s, %s)" % (self.kind, self.matched_name)


def _logic_candidates(s):
    """一个 logic 信号能被 Topout 名命中的所有候选名（小写）：基名 / _ls 顶层口 / 探针真名。"""
    names = {s.out_base.lower()}
    if getattr(s, "_ls_name", None):
        names.add(str(s._ls_name).lower())
    names.add(s.rtl_base.lower())
    names.add(s.rtl_base_full.lower())
    return names


def _mux_candidates(g):
    names = {g.out_base.lower()}
    if getattr(g, "_ls_name", None):
        names.add(str(g._ls_name).lower())
    names.add(g.rtl_base.lower())
    names.add(g.rtl_base_full.lower())
    return names


def build_index(wb):
    """建 Topout 名 → 源对象索引。返回 (logic_idx, mux_idx)，值=对象。
    同名冲突保留首个（与表内 VLOOKUP/regmap 取首个一致）。"""
    logic_idx, mux_idx = {}, {}
    for s in wb.logic:
        for nm in _logic_candidates(s):
            logic_idx.setdefault(nm, s)
    for g in (getattr(wb, "mux", None) or []):
        for nm in _mux_candidates(g):
            mux_idx.setdefault(nm, g)
    return logic_idx, mux_idx


def _register_kind(wb, low):
    """直连寄存器分类：在 tmm/regmap 找字段，判 RO/RW。返回 (kind, entry, found_in) 或 (None,None,None)。
    名字带/不带位宽都已剥过（low 是基名小写）。"""
    from .excel_model import _normalize_type
    for name, fld in (wb.tmm or {}).items():
        if name.lower() == low:
            return (fld.reg_type or None), fld, "tmm"
    for name, ent in (wb.regmap or {}).items():
        if name.lower() == low:
            return _normalize_type(ent.reg_type), ent, "regmap"
    return None, None, None


def resolve_root(wb, name, logic_idx=None, mux_idx=None):
    """把一个 Topout 信号名解析到它的源对象+分类。

    优先级：logic 行 > mux 组 > 直连寄存器(RW=可验 / RO=回读跳过) > 未解析。
    logic/mux 命中靠 out_base / _ls_name / rtl_base 三套候选（Topout 名是剥后缀的顶层真名，
    而 _ls 顶层口/被引用内部信号的真名带后缀，故都要试）。"""
    if logic_idx is None or mux_idx is None:
        logic_idx, mux_idx = build_index(wb)
    low = str(name).strip().lower()
    if low in logic_idx:
        return TopoutRoot(LOGIC, logic_idx[low], matched_name=low)
    if low in mux_idx:
        return TopoutRoot(MUX, mux_idx[low], matched_name=low)
    rk, ent, found = _register_kind(wb, low)
    if rk == "RW":
        return TopoutRoot(REGISTER, ent, matched_name=low, reg_kind="RW",
                          note="直连寄存器(RW，%s)——dft 恒等观测把寄存器字段直接当顶层输出" % found)
    if rk == "RO":
        return TopoutRoot(RO_READBACK, ent, matched_name=low, reg_kind="RO",
                          note="RO 回读(%s)——模拟/FSM 状态、非寄存器组合函数，无 cone，跳过+记账" % found)
    return TopoutRoot(UNRESOLVED, None, matched_name=low,
                      note="Topout 名 %r 在 logic/mux/regmap/tmm 都找不到源对象（需人工核对：可能埋件/真表⊋DUT）"
                           % name)


# ───────────────────────────── 单信号分析（建 cone + 真值表） ─────────────────────────────
class TopoutResult:
    """一个 Topout 信号的完整分析结果（供报告/账目/验证消费）。"""

    def __init__(self, topo, root):
        self.topo = topo                 # TopoutSignal
        self.root = root                 # TopoutRoot
        self.node = None                 # 展开后 AST（logic/register 根；mux 根为 None=走 case 枚举）
        self.bindings = None             # 叶子 bindings（节点路径）
        self.out_width = topo.width
        self.expanded = False            # 是否做了 cone 展开
        self.chain = []                  # 展开链（cone 显示用）
        self.vectors = []                # 真值表向量
        self.meta = {}                   # generate_vectors meta
        self.expansion = None            # mux 根：expand_mux_group 结果
        self.status = "ok"               # ok / skip / unresolved / error
        self.note = root.note
        self.issues = []                 # 解析/展开问题

    @property
    def owner(self):
        """报告/账目 owner —— 新模型直接取 Topout A 列（免 join 回 logic/mux）。"""
        return self.topo.owner

    @property
    def n_leaves(self):
        return len(self.bindings or {})


def analyze_signal(wb, resolver, topo, root=None, mode="min", max_tests=256,
                   exhaustive=False, want_vectors=True):
    """分析一个 Topout 信号：解析根 → 建 cone/真值表。返回 TopoutResult（永不抛，问题进 .issues/.status）。"""
    if root is None:
        root = resolve_root(wb, topo.name)
    res = TopoutResult(topo, root)

    if root.kind == UNRESOLVED:
        res.status = "unresolved"
        return res
    if root.kind == RO_READBACK:
        res.status = "skip"
        return res

    if root.kind == LOGIC:
        sig = root.obj
        res.out_width = sig.out_width
        try:
            node, bindings, expanded = G.expand_signal(wb, resolver, sig, chain_out=res.chain)
        except (cone.ConeError, E.ExprError) as ex:
            res.status = "error"
            res.issues.append("cone 展开失败: %s" % ex)
            return res
        res.node, res.bindings, res.expanded = node, bindings, expanded
        if want_vectors:
            res.vectors, res.meta = V.generate_vectors(
                node, bindings, sig.out_width, mode=mode, max_tests=max_tests,
                exhaustive=exhaustive)
        return res

    if root.kind == REGISTER:
        # 直连寄存器(RW)：顶层输出 = 该寄存器字段（dft 恒等观测）。建平凡节点 Var(BASE)，
        # 叶子 = 该寄存器本身(RW→RF_WRITE)，驱动它并断言 输出==写值，验证寄存器→顶层口的直连。
        base = topo.name
        info = {"raw": base, "base": base, "width": topo.width,
                "msb": topo.msb if topo.width > 1 else None,
                "lsb": topo.lsb if topo.width > 1 else None}
        b = resolver.resolve(base.upper(), info)
        key = base.upper()
        res.node = E.Var(key, info["msb"], info["lsb"])
        res.bindings = {key: b}
        if not b.resolved:
            res.issues.append("直连寄存器 %s 未解析: %s" % (base, b.note or ""))
        if want_vectors:
            res.vectors, res.meta = V.generate_vectors(
                res.node, res.bindings, topo.width, mode=mode, max_tests=max_tests,
                exhaustive=exhaustive)
        return res

    if root.kind == MUX:
        grp = root.obj
        res.out_width = grp.out_width
        try:
            expansion = mux_gen.expand_mux_group(wb, resolver, grp)
        except cone.ConeError as ex:
            res.status = "error"
            res.issues.append("mux 展开失败: %s" % ex)
            return res
        res.expansion = expansion
        res.issues.extend(expansion.get("issues", []))
        if want_vectors:
            res.vectors = mux_gen.make_mux_vectors(grp, expansion, mode=mode,
                                                   max_tests=max_tests)
            res.bindings = expansion.get("bindings", {})
        return res

    res.status = "error"
    res.issues.append("未知根类型 %r" % root.kind)
    return res


def analyze_all(wb, resolver, mode="min", max_tests=256, exhaustive=False,
                want_vectors=True):
    """对 wb.topout 全清单逐信号分析。返回 list[TopoutResult]（外层枚举源=Topout B 列）。"""
    logic_idx, mux_idx = build_index(wb)
    out = []
    for topo in wb.topout:
        root = resolve_root(wb, topo.name, logic_idx, mux_idx)
        out.append(analyze_signal(wb, resolver, topo, root=root, mode=mode,
                                  max_tests=max_tests, exhaustive=exhaustive,
                                  want_vectors=want_vectors))
    return out


# ───────────────────────────── 等价性对照（值验证，节点路径） ─────────────────────────────
def evaluate_at(result, base_values):
    """在给定输入取值上对一个【节点根】(logic/register) Topout 结果求期望输出值。

    base_values: {输入信号基名(小写): int}。对 mux 根无效（mux 走 case 枚举、无单一 AST）。
    复用 vectors.input_groups + make_vector_from_base_values —— 与 GUI/报告同一条求值路径，
    保证『引擎算的期望』就是产物里断言的期望。返回 (exp_value, exp_width)。"""
    if result.node is None or result.bindings is None:
        raise ValueError("evaluate_at 仅支持节点根(logic/register)，该信号根=%s" % result.root.kind)
    groups = V.input_groups(result.node, result.bindings)
    bv = {}
    for g in groups:
        # 组键 = 基名+切片(小写)；输入金标准按基名给值，命中基名即填
        for cand in (g["key"], g["base"].lower()):
            if cand in base_values:
                bv[g["key"]] = base_values[cand] & E.mask(g["width"])
                break
    vec = V.make_vector_from_base_values(
        result.node, result.bindings, groups, bv, result.out_width)
    return vec.exp_value, vec.exp_width


def load_fortest_golden(path, sheet=None):
    """打开 Excel 取 for_test 页金标准块（独立于 load_workbook，等价性对照专用）。"""
    import openpyxl
    from .excel_model import read_fortest_golden, _find_sheet
    raw = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = _find_sheet(raw, sheet) if sheet else _find_sheet(raw, "for_test")
    blocks = read_fortest_golden(ws)
    raw.close()
    return blocks


def validate_against_golden(wb, resolver, golden_blocks):
    """等价性对照（判据一）：对每个 for_test 金标准块，引擎生成的期望值 == 该列金标准。

    返回 list[dict]，每项：
      {'out': 输出基名, 'root': 根类型, 'cols': [{'label','got','exp','ok'}…],
       'n_ok','n_bad','status': 'checked'/'skip-mux'/'no-cone'/'unresolved'}。
    mux 根（无单一 AST）/ RO 回读 / 未解析 → 记原因跳过，不计 ok/bad。
    逐列：从金标准块 inputs 各列取值组 base_values → evaluate_at → 比 expected。"""
    reports = []
    for blk in golden_blocks:
        root = resolve_root(wb, blk["out"])
        rep = {"out": blk["out"], "root": root.kind, "cols": [],
               "n_ok": 0, "n_bad": 0, "status": "checked"}
        if root.kind == MUX:
            rep["status"] = "skip-mux"
            reports.append(rep)
            continue
        if root.kind == RO_READBACK:
            rep["status"] = "no-cone"
            reports.append(rep)
            continue
        if root.kind == UNRESOLVED:
            rep["status"] = "unresolved"
            reports.append(rep)
            continue
        result = analyze_signal(wb, resolver, _topo_for(blk), root=root,
                                want_vectors=False)
        labels = blk.get("labels") or []
        for ci in range(blk["ncol"]):
            exp = blk["expected"][ci]
            if exp is None:
                continue
            base_values = {}
            for ip in blk["inputs"]:
                vals = ip["values"]
                if ci < len(vals) and vals[ci] is not None:
                    base_values[ip["base"]] = vals[ci]
            got, _w = evaluate_at(result, base_values)
            ok = (got == exp)
            rep["cols"].append({"label": labels[ci] if ci < len(labels) else "T%d" % ci,
                                "got": got, "exp": exp, "ok": ok})
            rep["n_ok" if ok else "n_bad"] += 1
        reports.append(rep)
    return reports


def _topo_for(blk):
    """金标准块 → 临时 TopoutSignal（仅 validate 内部用，名/宽足够建 cone）。"""
    from .excel_model import TopoutSignal
    return TopoutSignal(row=0, name=blk["out"], width=blk.get("out_width", 1), owner="")
