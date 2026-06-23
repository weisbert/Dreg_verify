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
from . import sv_writer as W
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


# ───────────────────────────── 块B：Topout 报告/账目路径（只新增） ─────────────────────────────
def compose_topout_account(wb, resolver, mode="min", max_tests=256, exhaustive=False):
    """以 **Topout B 列为外层循环** 的报告/账目路径（块B，2026-06-23，只新增，不碰旧 generator.report）。

    owner 直接取 Topout A 列（免 join 回 logic/mux）。返回 {'rows':[…], 'summary':{…}}。

    堵 3 个静默陷阱（新路径【按构造】避开，对照 refactor_notes 影响面普查）：
      ① 默认不空：直接枚举 wb.topout，**不套** top_output_only 过滤（真表 N/I 全 0 → 旧过滤会选 0 个）；
      ② 不刷 top_out=0 假警告：只列 analyze 的【真 issues】(冲突/未解析)，**不发** bare-probe/
         needs-prefix 噪声（旧 generator 按 top_out=0 触发→新模型每信号都中、淹没报告）；
      ③ for_test 回填含 mux：见 topout_fortest_rows(include_mux=True)。
    """
    results = analyze_all(wb, resolver, mode=mode, max_tests=max_tests,
                          exhaustive=exhaustive)
    rows = []
    for r in results:
        rows.append({
            "name": r.topo.name, "owner": r.topo.owner, "width": r.out_width,
            "kind": r.root.kind, "status": r.status,
            "provenance": G.NAMING_MODEL_TOPOUT if r.status != "unresolved" else "unresolved",
            "n_leaves": r.n_leaves, "n_vectors": len(r.vectors),
            "issues": list(r.issues), "note": r.note,
        })
    by_status = {}
    by_owner = {}
    for x in rows:
        by_status[x["status"]] = by_status.get(x["status"], 0) + 1
        by_owner.setdefault(x["owner"], []).append(x["name"])
    summary = {
        "n_total": len(rows),
        "n_ok": by_status.get("ok", 0),
        "n_skip": by_status.get("skip", 0),
        "n_unresolved": by_status.get("unresolved", 0),
        "n_error": by_status.get("error", 0),
        "by_status": by_status,
        "by_owner": {o: len(v) for o, v in by_owner.items()},
        # 真警告 = 有 issues 的信号（不含『top_out=0』这种新模型噪声）
        "n_with_issues": sum(1 for x in rows if x["issues"]),
    }
    return {"rows": rows, "summary": summary}


def topout_fortest_rows(wb, resolver, mode="min", max_tests=256, exhaustive=False):
    """Topout for_test 回填行（块B 陷阱③，2026-06-23）：以 Topout 名为外层，**含 mux 根**。

    复用 generator.report 产出报告表(logic+mux 都带 for_test 回填键)，再交 fortest_writer
    以 include_mux=True 渲染——堵『_logic_tables 只留 is_logic、悄悄丢所有 mux 表』那个陷阱。
    返回 fortest_writer.build_fortest_rows 的 group 列表（每个对应一个 Topout 真值表）。"""
    from . import fortest_writer
    rep = report_for_topout(wb, resolver, mode=mode, max_tests=max_tests,
                            exhaustive=exhaustive)
    return fortest_writer.build_fortest_rows(rep, include_mux=True)


def report_for_topout(wb, resolver, mode="min", max_tests=256, exhaustive=False):
    """生成【限定到 Topout 清单】的报告(复用 generator.report，再按 Topout B 列名过滤 tables/detail)。

    新模型『要验什么』只在 Topout——旧 report 枚举全 logic/mux，这里只保留 Topout 命中的表，
    且 owner 用 Topout A 列覆盖（块B 不 join）。旧 generator.report 一字不动。"""
    from . import generator as G
    rep = G.report(wb, G.GenOptions(mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                                    include_risky=True))
    want = {t.name.lower() for t in wb.topout}
    owner_of = {t.name.lower(): t.owner for t in wb.topout}
    logic_idx, mux_idx = build_index(wb)

    def _topout_name_of(signal):
        from .excel_model import _strip_width
        base = _strip_width(signal)[0].lower()
        if base in want:
            return base
        # 经 _ls / rtl_base 命中（d_en_refbuf → d_en_refbuf_ls 等）
        s = logic_idx.get(base) or mux_idx.get(base)
        if s is not None:
            for cand in (getattr(s, "_ls_name", "") or "", s.rtl_base):
                if cand and cand.lower() in want:
                    return cand.lower()
        return None

    kept_tables = []
    for t in rep.get("tables", []):
        nm = _topout_name_of(t.get("signal", ""))
        if nm is not None:
            t = dict(t)
            t["owner"] = owner_of.get(nm, t.get("owner", ""))
            t["topout_name"] = nm                # 块B：标回 Topout B 列名（视图模型/账目 join 用，纯附加）
            kept_tables.append(t)
    rep = dict(rep)
    rep["tables"] = kept_tables
    return rep


# ═════════════════════════ 块B（续）：Topout .sv 产出路径（report_for_topout 的 .sv 孪生） ═════════════════════════
class _PassthroughSig:
    """直连寄存器(RW)根的 .sv 渲染占位（dft 恒等观测：顶层口 == 寄存器字段）。

    render_signal_block 只读 out_name/out_base/rtl_name/assert_id/owner/out_width；node 已传入
    故不读 expr。assert_id 用 'TOP<i>'（不与 logic 数字 R / mux 'mux<N>' 标号撞，保证全局唯一）。"""

    suffix = "topout-reg"
    _self_ref_suffixes = ()

    def __init__(self, name, width, owner, aid):
        self.out_name = name
        self.out_base = name
        self.rtl_name = name            # Topout 顶层真名（无前后缀，断言直接贴它）
        self.assert_id = aid
        self.owner = owner
        self.out_width = width or 1
        self.expr = "A"


def _account_block(name, kind, status, reason, owner):
    """RO 回读 / 未解析 / error / 同源已覆盖 → 块顶注释记账（护栏3：绝不静默丢、绝不崩）。
    注释行不进 SV 字符串字面量，可含中文（与 generator 现有 '// ⚠ <中文>' 一致）。"""
    lines = ["// [topout] %s (%s/%s)：%s —— 本信号不产出断言（已记账，未静默丢弃）"
             % (name, kind, status, reason)]
    stats = {"out_name": name, "rtl_name": name, "assert_id": "-", "owner": owner or "",
             "n_vectors": 0, "n_negative": 0, "n_designer": 0,
             "unresolved": [], "truncated": False, "cone_expanded": False,
             "topout_name": name, "topout_kind": kind, "topout_status": status}
    return (lines, stats)


def _register_passthrough_block(result, owner, aid, comments=False, counters=False):
    """直连寄存器根 → 平凡 passthrough .sv 块：RF_WRITE 该寄存器 + 断言 顶层口 == 写值。"""
    sig = _PassthroughSig(result.topo.name, result.out_width, owner, aid)
    meta = dict(result.meta or {})
    meta.setdefault("truncated", False)
    lines, stats = W.render_signal_block(sig, result.bindings, result.vectors, meta,
                                         comments=comments, node=result.node,
                                         counters=counters)
    stats["topout_name"] = result.topo.name
    stats["topout_kind"] = REGISTER
    stats["topout_status"] = result.status
    stats["cone_expanded"] = False
    return (lines, stats)


def build_for_topout(wb, mode="min", max_tests=256, exhaustive=False,
                     comments=False, sv_summary=False, owner_in_msg=False):
    """以 **Topout B 列为外层** 产出 .sv 块清单（块B，report_for_topout 的 .sv 孪生，只新增）。

    · logic/mux 根：复用 generator.build（按 Topout 源 out_name 过滤 + 按 B 列序重排 +
      owner 用 Topout A 列覆盖 stats）——所有 DFT 拍/负向/去重/警告/dup-label 检查照旧。
    · 直连寄存器(RW)根：渲染平凡 passthrough 块（顶层口 == 写值）。
    · RO 回读 / 未解析 / error / 同源已覆盖：块顶注释记账（护栏3，绝不静默丢）。

    cone 默认级联、include_risky=True（Topout 名 cone 已展到源、前后缀整类问题消失，逃生阀属『排查(旧)』）。
    sv_summary=True 时各块带计数器（counters）——调用方须 render_file(summary=True) 包一次命名块。

    返回 {'blocks':[(lines,stats)], 'results':[TopoutResult], 'accounted':[…], 'summary':{…}}。"""
    from . import resolver as R
    resolver = R.Resolver(wb)                       # 干净 cone 默认（与 generator.build 内部一致）
    results = analyze_all(wb, resolver, mode=mode, max_tests=max_tests, exhaustive=exhaustive)

    # logic/mux 根 → 源对象名集合（generator.build 据此选；out_name 与 out_base 都给，匹配两种写法）
    want_names = set()
    for r in results:
        if r.status == "ok" and r.root.kind in (LOGIC, MUX):
            want_names.add(r.root.obj.out_name.lower())
            want_names.add(r.root.obj.out_base.lower())

    opts = G.GenOptions(mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                        include_risky=True, comments=comments, sv_summary=sv_summary,
                        owner_in_msg=owner_in_msg, signals=want_names)
    built = G.build(wb, opts)
    by_src = {st["out_name"].lower(): (ln, st) for ln, st in built["blocks"]}

    blocks, accounted, seen_src = [], [], set()
    reg_i = 0
    for r in results:
        name, owner = r.topo.name, r.topo.owner
        if r.status == "ok" and r.root.kind in (LOGIC, MUX):
            src = r.root.obj.out_name.lower()
            if src in seen_src:                    # 两个 Topout 名映到同一源对象 → 只产出一次（防 dup-label）
                blk = _account_block(name, r.root.kind, "dup-source",
                                     "与已产出的同源信号共用一个源对象（避免断言标号重复）", owner)
                blocks.append(blk)
                accounted.append({"name": name, "kind": r.root.kind, "status": "dup-source",
                                  "reason": blk[1]["topout_status"]})
                continue
            blk = by_src.get(src)
            if blk is None:                        # include_risky=True 仍被 build 跳过（规格冲突/空向量等）
                reason = "; ".join(r.issues) or "generator.build 未产出该块（规格冲突/空向量/被跳过，见账目）"
                blocks.append(_account_block(name, r.root.kind, "skipped", reason, owner))
                accounted.append({"name": name, "kind": r.root.kind, "status": "skipped", "reason": reason})
                continue
            seen_src.add(src)
            ln, st = blk
            st = dict(st); st["owner"] = owner; st["topout_name"] = name
            st["topout_kind"] = r.root.kind; st["topout_status"] = "ok"
            blocks.append((list(ln), st))
        elif r.status == "ok" and r.root.kind == REGISTER:
            blk = _register_passthrough_block(r, owner, "TOP%d" % reg_i,
                                              comments=comments, counters=sv_summary)
            reg_i += 1
            blocks.append(blk)
        else:                                       # skip(RO) / unresolved / error
            reason = (r.note or "; ".join(r.issues) or r.status)
            blocks.append(_account_block(name, r.root.kind, r.status, reason, owner))
            accounted.append({"name": name, "kind": r.root.kind, "status": r.status, "reason": reason})

    n_emitted = sum(1 for _l, s in blocks if s.get("n_vectors", 0) > 0)
    summary = {
        "n_total": len(results),
        "n_emitted": n_emitted,
        "n_accounted": len(accounted),
        "n_vectors": sum(s.get("n_vectors", 0) for _l, s in blocks),
        "n_negative": sum(s.get("n_negative", 0) for _l, s in blocks),
    }
    return {"blocks": blocks, "results": results, "accounted": accounted, "summary": summary}


def render_topout_sv(wb, mode="min", max_tests=256, exhaustive=False,
                     comments=False, sv_summary=False, owner_in_msg=False):
    """便捷封装：build_for_topout → sv_writer.render_file → (text, build_dict)。"""
    b = build_for_topout(wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                         comments=comments, sv_summary=sv_summary, owner_in_msg=owner_in_msg)
    text = W.render_file(b["blocks"], comments=comments, summary=sv_summary)
    return text, b


# ═════════════════════════ 块B（续）：Topout GUI 视图模型（每信号一个，按 B 列序） ═════════════════════════
def _fill_register_model(m, result):
    """直连寄存器根的真值表视图（与 report 表同形：inputs/tests/auto_label/exp_label）。"""
    groups = V.input_groups(result.node, result.bindings)
    m["inputs"] = [G._input_meta(g, result.bindings) for g in groups]
    out_w = result.out_width or 1
    slc = "[%d:0]" % (out_w - 1) if out_w > 1 else ""
    m["auto_label"] = "auto_out%s" % slc
    m["exp_label"] = "期望(out)%s" % slc
    tests = []
    for vec in result.vectors:
        bv = V.vector_to_base_values(vec, groups)
        tests.append({
            "name": W.test_label(vec), "neg": vec.is_negative,
            "values": [G._fmt_cell(bv.get(g["key"], 0), g["width"]) for g in groups],
            "auto_out": G._fmt_cell(vec.exp_value, vec.exp_width),
            "expected": G._fmt_cell(vec.asserted_value, vec.exp_width),
        })
    m["tests"] = tests
    m["n_vectors"] = len(tests)


def topout_view_models(wb, mode="min", max_tests=256, exhaustive=False):
    """每个 Topout 信号一个【视图模型】（GUI / 无头测试消费），按 Topout B 列序。

    干净 cone 默认（Topout 视图不放级联/尾缀/top_output——那些属『排查(旧)』）。
      · logic/mux 根 → 复用 report_for_topout 富格式表（chain/inputs/tests，值已格式化）；
      · 直连寄存器根 → 从 TopoutResult 建平凡表；
      · RO 回读 / 未解析 / error → 只记账（无真值表，note/issues 说明原因，绝不崩）。
    每个模型：{name, owner, width, kind, status, note, issues, matched_name, n_leaves,
              chain:[{out,expr,subst}], inputs:[…], tests:[…], auto_label, exp_label, n_vectors}。"""
    from . import resolver as R
    resolver = R.Resolver(wb)
    results = analyze_all(wb, resolver, mode=mode, max_tests=max_tests, exhaustive=exhaustive)
    rep = report_for_topout(wb, resolver, mode=mode, max_tests=max_tests, exhaustive=exhaustive)
    tbl_by_topout = {}
    for t in rep.get("tables", []):
        nm = t.get("topout_name")
        if nm:
            tbl_by_topout.setdefault(nm.lower(), t)

    models = []
    for r in results:
        m = {"name": r.topo.name, "owner": r.topo.owner, "width": r.out_width,
             "kind": r.root.kind, "status": r.status, "note": r.note,
             "issues": list(r.issues), "matched_name": r.root.matched_name,
             "n_leaves": r.n_leaves, "n_vectors": len(r.vectors),
             "chain": [], "inputs": [], "tests": [], "auto_label": "", "exp_label": ""}
        t = tbl_by_topout.get(r.topo.name.lower())
        if t is not None:
            m["chain"] = t.get("chain", [])
            m["inputs"] = t.get("inputs", [])
            m["tests"] = t.get("tests", [])
            m["auto_label"] = t.get("auto_label", "")
            m["exp_label"] = t.get("exp_label", "")
            m["n_vectors"] = len(t.get("tests", []))
        elif r.root.kind == REGISTER and r.status == "ok":
            _fill_register_model(m, r)
        models.append(m)
    return models


# ═════════════════════════ 块B（续）：Topout 报告（write_report 兼容，全 Topout 限定） ═════════════════════════
_VERIF_OF = {"ok": "clean", "unresolved": "unresolved", "error": "parse-err"}


def _vm_to_report_table(vm):
    """register 平凡视图模型 → report 表 dict（HTML/CSV 真值表 tab 消费）。"""
    return {"R": "", "signal": vm["name"], "owner": vm["owner"], "type": vm["kind"],
            "expr": "", "is_logic": True, "out_width": vm["width"] or 1,
            "chain": vm["chain"], "supplement": "", "inputs": vm["inputs"],
            "tests": vm["tests"], "auto_label": vm["auto_label"], "exp_label": vm["exp_label"],
            "topout_name": vm["name"]}


def _account_error_text(row):
    """账目行 → 报告 summary 的『错误/原因』列文案（RO 回读/未解析/error 不空，ok 留空）。"""
    if row["status"] == "ok":
        return "; ".join(row["issues"]) if row["issues"] else ""
    if row["status"] == "skip":
        return "跳过(RO 回读，无 cone)：%s" % (row["note"] or "")
    if row["status"] == "unresolved":
        return "未解析：%s" % (row["note"] or "")
    return "; ".join(row["issues"]) or row["note"] or row["status"]


def topout_report(wb, mode="min", max_tests=256, exhaustive=False):
    """Topout 限定报告（write_report 兼容：summary/detail/tables/verifiability），堵 3 静默陷阱：
      ① 默认不空：summary 直接来自 compose_topout_account（12 行全分类，不套 top_output_only）；
      ② 不刷 top_out=0 假警告：error 列只放真原因（RO/未解析/冲突），无 bare-probe/needs-prefix 噪声；
      ③ for_test 回填含 mux：tables 来自 report_for_topout（已含 mux）+ register 平凡表。

    summary/detail/verifiability 全限定到 Topout 清单（不像 report_for_topout 只过滤 tables）。"""
    from . import resolver as R
    resolver = R.Resolver(wb)
    rep = report_for_topout(wb, resolver, mode=mode, max_tests=max_tests, exhaustive=exhaustive)
    acc = compose_topout_account(wb, resolver, mode=mode, max_tests=max_tests, exhaustive=exhaustive)
    vms = topout_view_models(wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive)

    # tables：logic/mux 富表（report_for_topout）+ register 平凡表（视图模型）
    tables = list(rep.get("tables", []))
    for vm in vms:
        if vm["kind"] == REGISTER and vm["tests"]:
            tables.append(_vm_to_report_table(vm))
    # detail：限定到存活 logic/mux 表的源信号（per-test 行；register/RO 无 per-test）
    kept_src = {str(t["signal"]).lower() for t in rep.get("tables", [])}
    detail = [d for d in rep.get("detail", []) if str(d.get("signal", "")).lower() in kept_src]

    # summary + verifiability：以 Topout 账目为准（覆盖全 12，含 RO/未解析）
    summary, verif_signals, counts = [], [], {}
    for row in acc["rows"]:
        summary.append({
            "R": "", "signal": row["name"], "owner": row["owner"], "type": row["kind"],
            "top": "", "expr": "", "n_tests": row["n_vectors"], "n_neg": 0,
            "control": "", "data": "", "unresolved": "; ".join(row["issues"]),
            "supplement": "", "error": _account_error_text(row)})
        vst = _VERIF_OF.get(row["status"], row["status"])     # ok→clean / skip 原样(RO 回读)
        counts[vst] = counts.get(vst, 0) + 1
        verif_signals.append({
            "R": "", "signal": row["name"], "owner": row["owner"], "type": row["kind"],
            "top": "", "status": vst, "detail": row["note"] or "; ".join(row["issues"]),
            "out_net": "`ENV_RF.%s" % row["name"]})
    return {"summary": summary, "detail": detail, "tables": tables,
            "verifiability": {"counts": counts, "signals": verif_signals}}
