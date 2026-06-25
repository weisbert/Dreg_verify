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

import contextlib

from . import cone
from . import expr as E
from . import forms as FORMS
from . import generator as G
from . import mux_gen
from . import sv_writer as W
from . import vectors as V


@contextlib.contextmanager
def _with_logic_overrides(wb, logic_overrides):
    """Topout 分析期临时把 wb.logic 换成【应用 RTL 补充后】的 logic 列表(M8)，退出还原（守 R32 不原地改）。

    analyze_all/build_index/resolve_root 都据 wb.logic 建索引、定根，故 RTL 补充必须在【建索引前】换进去，
    否则根仍指向补充前旧对象 → Topout 真值表显示补充前逻辑（静默假绿，M8 症状）。无 override →
    yield 原 wb.logic、逐字节不变。嵌套安全：内层不传 override（读外层已换好的 wb.logic）。"""
    if not logic_overrides:
        yield
        return
    saved = wb.logic
    wb.logic = G._logic_with_overrides(wb, G.GenOptions(logic_overrides=logic_overrides))
    try:
        yield
    finally:
        wb.logic = saved


# ───────────────────────────── 根对象分类 ─────────────────────────────
LOGIC = "logic"
MUX = "mux"
REGISTER = "register"          # 直连寄存器(RW)：dft 恒等观测把寄存器字段直接当顶层输出，可驱可验
RO_READBACK = "ro-readback"    # RO 回读(pll_lock_indicator 等)：模拟/FSM 状态，非寄存器组合函数→无 cone
UNRESOLVED = "unresolved"      # Topout 名在 logic/mux/寄存器各页都找不到


class TopoutRoot:
    """一个 Topout 信号解析到的【源对象】及其分类。"""

    def __init__(self, kind, obj=None, matched_name=None, reg_kind=None, note="",
                 probe_name=None, source_name=None, dft_obs_name=None):
        self.kind = kind                 # LOGIC / MUX / REGISTER / RO_READBACK / UNRESOLVED
        self.obj = obj                   # LogicSignal / MuxGroup / RegmapEntry|TmmField / None
        self.matched_name = matched_name # 实际命中的候选名（out_base / _ls_name / 字段名）
        self.reg_kind = reg_kind         # 直连寄存器时 'RW'/'RO'
        self.note = note
        # 改名链途经的【带门(iddq) dft 观测输出名】(keys wb.dft)：桥到 logic 源后，门控(B?0:A)那层
        # 不在 logic 表达式里、而在 dft 观测层——analyze_signal 据它 pin_dft_gate 把 iddq 叠成显式输入
        # （否则只验 logic 选路、漏掉 iddq 门 = 与 for_test 金标准的输入集不符）。None=无门。
        self.dft_obs_name = dft_obs_name
        # dft 改名桥接（2026-06-24）：dft 页把 logic/寄存器源 <source_name>(如 A_sm) 观测后改名成顶层
        # <probe_name>(如 A)，Topout B 列写的是 probe_name。非空 = 这是改名信号：逻辑/驱动来自 source，
        # 但断言探针必须贴顶层真名 probe_name(=A，不是 A_sm)。两者都 None = 普通信号(名字没变)。
        self.probe_name = probe_name     # 断言探针贴的顶层真名（None=用源对象自身名）
        self.source_name = source_name   # 实际提供逻辑/驱动的源名（寄存器根用它定字段；None=用 topo 名）

    @property
    def renamed(self):
        return self.probe_name is not None

    def __repr__(self):
        return "TopoutRoot(%s, %s%s)" % (
            self.kind, self.matched_name,
            (" →probe %s" % self.probe_name) if self.probe_name else "")


def _dft_rename_map(wb):
    """从 dft 页（read 成 wb.dft_rows）建【改名表】{顶层输出名low → 功能源名low}。

    dft 行 = 一个被观测输出 D + 表达式 E（透传 A / 门控 B?0:A）+ 输入 A/B/C。
    『改名』判据：该行**唯一的功能输入**（剥 _to_dft + 去掉门控位后）基名 ≠ 输出基名
    （如 D=A、输入=A_sm_to_dft → A_sm ≠ A ⟹ 改名 A_sm→A）。
    透传到同名（mirror 的 clk_force_on=clk_force_on_to_dft）= 恒等、非改名 → 不入表（旧表零影响）。"""
    from .excel_model import _strip_width, _dft_strip
    out = {}
    gates = wb.dft or {}
    for d in (getattr(wb, "dft_rows", None) or []):
        ob = _strip_width(d.out_name)[0].lower()
        gate_base = (gates.get(ob) or {}).get("gate_base")
        funcs = []
        for info in d.inputs.values():
            b = _dft_strip(info.get("raw", "")).lower()    # 剥位宽 + _to_dft
            if not b or b == ob or (gate_base and b == gate_base):
                continue                                   # 恒等 / 门控位 → 不是功能源
            funcs.append(b)
        if len(funcs) == 1:                                # 唯一功能源且与输出异名 = 改名
            out.setdefault(ob, funcs[0])
    return out


def _ls_rename_map(wb):
    """level_shift 页改名表 {顶层口名low → 输入源基名low}（read_level_shift 的逆）。

    level_shift 通常由 excel_model._apply_level_shift 给【能对上的 logic/mux 输出】设 _ls_name 处理掉；
    但顶层口的输入源【本身又是上一层改名】(如 d_vco_en_faston 是 dft 改名、非直接 logic 输出)时
    _apply_level_shift 对不上 → 桥接据此把顶层 _ls 名映回输入源名，再链式接 dft 改名/直接解析。"""
    out = {}
    for in_base, info in (getattr(wb, "level_shift", None) or {}).items():
        ob = str((info or {}).get("out", "")).strip().lower()
        if ob and ob != str(in_base).lower():
            out.setdefault(ob, str(in_base).lower())
    return out


def _rename_map(wb):
    """合并改名表 {输出名low → 源名low}：dft 观测改名 + level_shift 电平移位改名。
    resolve_root 据它【链式回溯】(level_shift → dft → logic)。dft 优先(同名时不被 ls 覆盖)。"""
    m = dict(_dft_rename_map(wb))
    for o, s in _ls_rename_map(wb).items():
        m.setdefault(o, s)
    return m


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


def _resolve_direct(wb, low, logic_idx, mux_idx):
    """直接解析（不走 dft 改名桥接）→ TopoutRoot；找不到返回 None（供桥接复用，防递归）。"""
    if low in logic_idx:
        return TopoutRoot(LOGIC, logic_idx[low], matched_name=low)
    if low in mux_idx:
        return TopoutRoot(MUX, mux_idx[low], matched_name=low)
    rk, ent, found = _register_kind(wb, low)
    if rk == "RW":
        return TopoutRoot(REGISTER, ent, matched_name=low, reg_kind="RW", note=found)
    if rk == "RO":
        return TopoutRoot(RO_READBACK, ent, matched_name=low, reg_kind="RO", note=found)
    return None


def resolve_root(wb, name, logic_idx=None, mux_idx=None, rename=None):
    """把一个 Topout 信号名解析到它的源对象+分类。

    优先级：logic 行 > mux 组 > 直连寄存器(RW=可验 / RO=回读跳过) > **dft 改名桥接** > 未解析。
    logic/mux 命中靠 out_base / _ls_name / rtl_base 三套候选（Topout 名是剥后缀的顶层真名，
    而 _ls 顶层口/被引用内部信号的真名带后缀，故都要试）。

    改名桥接（2026-06-24）：若顶层名直接找不到、但它是中间页【观测/电平移位改名】出来的，
    就【链式回溯】到真正的 logic/mux/寄存器源（level_shift → dft → logic 可多跳），拿逻辑/驱动，
    但断言探针贴顶层真名（不是源名）。例：d_vco_en_faston_ls ←(level_shift) d_vco_en_faston
    ←(dft) d_vco_en_faston_fsm(logic)。"""
    if logic_idx is None or mux_idx is None:
        logic_idx, mux_idx = build_index(wb)
    low = str(name).strip().lower()
    _gates = getattr(wb, "dft", None) or {}      # {dft 观测输出名: 门控}（带 iddq 门=非恒等观测）
    direct = _resolve_direct(wb, low, logic_idx, mux_idx)
    if direct is not None:
        if direct.kind == REGISTER:
            # 同名【带门】dft 观测撞同名寄存器(d_bt_lp_pmu_test_en 类)：顶层=iddq?0:寄存器，不是裸寄存器透传。
            # 记下门观测名(=本名)，analyze_signal 给寄存器透传也叠 iddq 门(否则漏 iddq=假绿，同 d_vco_en_faston)。
            if low in _gates:
                direct.dft_obs_name = low
                direct.note = "直连寄存器(RW，%s)——dft 带 iddq 门观测(顶层=iddq?0:寄存器)" % direct.note
            else:
                direct.note = "直连寄存器(RW，%s)——dft 恒等观测把寄存器字段直接当顶层输出" % direct.note
        elif direct.kind == RO_READBACK:
            direct.note = "RO 回读(%s)——模拟/FSM 状态、非寄存器组合函数，无 cone，跳过+记账" % direct.note
        return direct

    # ── 改名桥接：低优先级（直接命中不到才尝试）；链式跟随 + 环/深度护栏 ──
    if rename is None:
        rename = _rename_map(wb)
    dft_gates = getattr(wb, "dft", None) or {}   # {dft 观测输出名: 门控} —— 途经它=带 iddq 门的观测层
    seen, cur, hops = {low}, low, 0
    reg_fallback = None                  # 链上撞到的同名寄存器（链尾够不到 logic/mux 才退回它，绝不静默丢）
    dft_obs = None                       # 途经的带门 dft 观测名（供 pin_dft_gate 叠 iddq）
    while hops < 8:
        nxt = rename.get(cur)
        if nxt is None or nxt in seen:
            break
        gated_here = cur in dft_gates    # cur 是被 iddq 门控的 dft 观测输出（门控那层在此跳）
        seen.add(nxt); hops += 1
        sub = _resolve_direct(wb, nxt, logic_idx, mux_idx)
        if sub is not None and sub.kind in (LOGIC, MUX):
            sub.probe_name = name        # 顶层真名（断言探针贴它，原大小写）
            sub.source_name = nxt        # 最终 logic/mux 源名
            sub.matched_name = low
            sub.dft_obs_name = dft_obs or (cur if gated_here else None)
            sub.note = ("改名桥接：Topout 名 %s ← 源 %s（%s 根，经 %d 跳改名%s）；逻辑/驱动来自源，"
                        "断言探针贴顶层真名 %s"
                        % (name, nxt, sub.kind, hops,
                           ("，含 dft 门 %s 作输入" % sub.dft_obs_name) if sub.dft_obs_name else "", name))
            return sub
        if sub is not None and sub.kind == REGISTER:
            # 撞到同名寄存器：若它【还能被继续改名】(=它其实是上一层观测输出，同名寄存器只是撞名的
            # 输入)→ 优先顺着 dft 链找真正的 logic/mux 源；够不到再退回这个寄存器（兜底）。修：
            # d_vco_en_faston_ls ←ls← d_vco_en_faston(=dft 观测输出 iddq?0:fsm，又恰有同名 RW 输入寄存器)
            # ←dft← d_vco_en_faston_fsm(logic 选路)，旧版在此撞寄存器即停 → 漏掉 logic+iddq+fll_active。
            if rename.get(nxt) and rename.get(nxt) not in seen:
                if reg_fallback is None:
                    reg_fallback = (sub, nxt)
                if gated_here:
                    dft_obs = cur
                cur = nxt
                continue
            sub.probe_name = name
            sub.source_name = nxt
            sub.matched_name = low
            sub.dft_obs_name = dft_obs or (cur if gated_here else (nxt if nxt in dft_gates else None))
            sub.note = ("改名桥接：Topout 名 %s ← 源 %s（register 根，经 %d 跳改名%s）；逻辑/驱动来自源，"
                        "断言探针贴顶层真名 %s"
                        % (name, nxt, hops,
                           ("，含 dft 门 %s 作输入" % sub.dft_obs_name) if sub.dft_obs_name else "", name))
            return sub
        if gated_here:                   # 此跳解析不到但途经带门观测层 → 记下门，继续回溯
            dft_obs = cur
        cur = nxt                        # 源仍是改名（如 ls→dft）→ 继续回溯
    if reg_fallback is not None:         # 链上有同名寄存器、但终点无 logic/mux 源 → 退回寄存器（不静默丢）
        sub, nxt = reg_fallback
        sub.probe_name = name
        sub.source_name = nxt
        sub.matched_name = low
        sub.dft_obs_name = dft_obs or (nxt if nxt in dft_gates else None)
        sub.note = ("改名桥接：Topout 名 %s ← 源 %s（register 根，改名链上无 logic/mux 源%s）；"
                    "逻辑/驱动来自源，断言探针贴顶层真名 %s"
                    % (name, nxt, ("，含 dft 门 %s 作输入" % sub.dft_obs_name) if sub.dft_obs_name else "",
                       name))
        return sub
    if hops > 0:                             # 跟过改名但终点仍解析不了（埋件/RO）
        return TopoutRoot(UNRESOLVED, None, matched_name=low,
                          note="改名链 %s→…→%s 的终点在 logic/mux/regmap 仍找不到源（或为 RO）——需人工核对"
                               % (name, cur))
    return TopoutRoot(UNRESOLVED, None, matched_name=low,
                      note="Topout 名 %r 在 logic/mux/regmap/tmm/改名(dft+level_shift) 都找不到源对象"
                           "（需人工核对：可能埋件/真表⊋DUT）" % name)


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
        self.dft_gate = None             # 钉上的 iddq 门 (binding, 透传值) 或 None——供 GUI 显示门输入行
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


# ───────────────────────────── 形态分类 + 门控展开链（#1/#2/#3 form-driven 接入） ─────────────────────────────
form_label = FORMS.form_label    # 复用 forms 的人读『逻辑类型』映射（避免重复，兼容 T.form_label 引用）


def _result_gate_info(wb, res):
    """该 TopoutResult 是否被 iddq 门控 → forms.dft_gate_info（门键与 analyze_signal pin 同口径：
    改名桥接途经的带门观测名 dft_obs_name 优先，否则源对象 out_base / 寄存器根用 topo 名）。"""
    root = res.root
    ob = (getattr(root, "dft_obs_name", None)
          or (getattr(root.obj, "out_base", None) if root.obj is not None else None)
          or res.topo.name)
    return FORMS.dft_gate_info(wb, ob)


def result_form(wb, res):
    """分类一个【已分析的】Topout 信号的展开后表达式形态(F0-F4)——覆盖/显示按形态派发的统一口径。
    mux 根 node=None→SELECT(expandable 取 mux 可展性)；带门→GATED 套内层。永不抛(分类失败退 None)。"""
    try:
        expandable = None
        if res.root.kind == MUX:
            exp = res.expansion or {}
            # 可展性：expand_mux_group 标了 expandable 就用，否则 None(未知)
            expandable = exp.get("expandable")
        return FORMS.classify(res.node, res.bindings, gate=_result_gate_info(wb, res),
                              expandable=expandable)
    except Exception:    # noqa: BLE001 —— 分类绝不崩
        return None


def _gate_chain_entry(top_name, gate_base, inner_ref, transp):
    """门控级展开链条目（#1/#4-6：把 iddq 折进 cone 展开链显示）：<top> = iddq ? 0 : <inner>
    （透传值 transp 决定常量支 0 在哪支：transp=0→iddq 选 0、功能走 inner）。纯展开链【显示】用，
    不入 .sv 向量(byte-safe)——让跨页 cone 展开页/真表上方能看到 iddq_mode 这一级。"""
    expr = ("%s ? 0 : %s" % (gate_base, inner_ref) if int(transp) == 0
            else "%s ? %s : 0" % (gate_base, inner_ref))
    return {"out": top_name, "expr": expr, "subst": expr}


def _prepend_gate_chain(res, root, inner_ref):
    """若本结果带 iddq 门，把门控级作为【最外层】展开链条目插到链首（#1/#4-6）。"""
    if res.dft_gate is None:
        return
    top = (root.probe_name if (getattr(root, "renamed", False) and root.probe_name)
           else res.topo.name)
    res.chain.insert(0, _gate_chain_entry(top, res.dft_gate[0].base, inner_ref, res.dft_gate[1]))


def analyze_signal(wb, resolver, topo, root=None, mode="min", max_tests=256,
                   exhaustive=False, want_vectors=True, mux_data=None):
    """分析一个 Topout 信号：解析根 → 建 cone/真值表。返回 TopoutResult（永不抛，问题进 .issues/.status）。
    mux_data：mux 根的【数据值手填】{物理基名(小写): int}（B2/N8），喂 make_mux_vectors data_overrides。"""
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
            res.node, res.bindings, res.expanded = node, bindings, expanded
            if want_vectors:
                res.vectors, res.meta = V.generate_vectors(
                    node, bindings, sig.out_width, mode=mode, max_tests=max_tests,
                    exhaustive=exhaustive)
                # dft 门控的 logic 输出：把门(iddq)叠成显式输入(每条向量 force 透传值)+补 DFT 拍。
                # 门键 = dft_obs_name(改名桥接途经的带门观测层) **或** 本 logic 行自己的 out_base
                # ——后者修『_ls 经 level_shift 直接命中 logic 行(不走桥)→dft_obs_name=None，而该 logic
                # out_base 本身就是 dft 页门控观测(d_en_vco_fc 类)→analyze 漏门、与 generator.build/report
                # (按 out_base 钉门)不一致』(2026-06-25 实证 d_en_vco_fc_ls)。无门时 pin/append 均 no-op。
                obs = getattr(root, "dft_obs_name", None) or sig.out_base.lower()
                if obs:
                    _ib = {b.base.lower() for b in bindings.values()
                           if b is not None and getattr(b, "base", None)}
                    _skip = G._append_dft_vectors(obs, res.vectors, wb, resolver, input_bases=_ib)
                    if _skip:
                        res.meta["iddq_skipped"] = _skip
                    res.dft_gate = G.pin_dft_gate(obs, res.vectors, wb, resolver, input_bases=_ib)
                    _prepend_gate_chain(res, root, sig.out_base)   # #1/#4-6：iddq 折进展开链显示
        except (cone.ConeError, E.ExprError) as ex:
            res.status = "error"
            res.issues.append("cone 展开失败: %s" % ex)
        except Exception as ex:   # noqa: BLE001 —— 护栏3：永不抛，意外异常记账成 error 不连累整批
            res.status = "error"
            res.issues.append("分析异常: %r" % ex)
        return res

    if root.kind == REGISTER:
        # 直连寄存器(RW)：顶层输出 = 该寄存器字段（dft 恒等观测）。建平凡节点 Var(BASE)，
        # 叶子 = 该寄存器本身(RW→RF_WRITE)，驱动它并断言 输出==写值，验证寄存器→顶层口的直连。
        # dft 改名时 source_name(源寄存器名,如 A_sm)≠ topo.name(顶层名 A)——解析驱动用源名，探针贴顶层名。
        base = root.source_name or topo.name
        try:
            # ⭐位宽以解析到的寄存器字段为准（修『Topout 名无 [msb:lsb] 切片→只验 bit0』假绿）：
            # 裸名写法 _strip_width 给 width=1，但字段是多 bit 时只 RF_WRITE bit0、高位静默不验。
            obj = root.obj
            bm, bl = getattr(obj, "bit_msb", None), getattr(obj, "bit_lsb", None)
            field_w = (int(bm) - int(bl) + 1) if (bm is not None and bl is not None) else 1
            eff_w = max(int(topo.width or 1), max(int(field_w), 1))
            info = {"raw": base, "base": base, "width": eff_w,
                    "msb": (eff_w - 1) if eff_w > 1 else None,
                    "lsb": 0 if eff_w > 1 else None}
            b = resolver.resolve(base.upper(), info)
            if not b.resolved:
                # 未解析的直连寄存器（如 RW 字段缺地址）→ error，别发『ok』绿块驱不存在的网（假绿）
                res.status = "error"
                res.issues.append("直连寄存器 %s 未解析: %s" % (base, b.note or ""))
                return res
            if eff_w > int(topo.width or 1):
                res.issues.append("Topout 名 %s 无位宽切片但寄存器字段宽 %d——按字段全宽验"
                                  "(否则只验 bit0、高位假绿)" % (base, eff_w))
            key = base.upper()
            res.out_width = eff_w
            res.node = E.Var(key, info["msb"], info["lsb"])
            res.bindings = {key: b}
            if want_vectors:
                res.vectors, res.meta = V.generate_vectors(
                    res.node, res.bindings, eff_w, mode=mode, max_tests=max_tests,
                    exhaustive=exhaustive)
                # 同名【带门】dft 观测撞同名寄存器(d_bt_lp_pmu_test_en 类)：顶层=iddq?0:寄存器，
                # 给寄存器透传也叠 iddq 门(force 透传值)+DFT 拍——否则漏 iddq=假绿(同 d_vco_en_faston)。
                obs = getattr(root, "dft_obs_name", None)
                if obs:
                    _ib = {b.base.lower()} if getattr(b, "base", None) else set()
                    _skip = G._append_dft_vectors(obs, res.vectors, wb, resolver, input_bases=_ib)
                    if _skip:
                        res.meta["iddq_skipped"] = _skip
                    res.dft_gate = G.pin_dft_gate(obs, res.vectors, wb, resolver, input_bases=_ib)
                    _prepend_gate_chain(res, root, base)   # #1/#4-6：iddq 折进展开链显示(寄存器根)
        except Exception as ex:   # noqa: BLE001 —— 护栏3：永不抛
            res.status = "error"
            res.issues.append("分析异常: %r" % ex)
        return res

    if root.kind == MUX:
        grp = root.obj
        res.out_width = grp.out_width
        try:
            expansion = mux_gen.expand_mux_group(wb, resolver, grp)
            res.expansion = expansion
            res.issues.extend(expansion.get("issues", []))
            if want_vectors:
                # make_mux_vectors 返回 (vecs, meta)——必须解包（旧代码误把整个元组塞 res.vectors，
                # build/report 不读 res.vectors 故没暴露；GUI 编辑器直接读它才发现，2026-06-24 修）。
                # 用 coverage_mode 归一档位，让『穷举』mux 也真扫另一条控制路径（旧 mode=max 退化成全面）。
                res.vectors, res.meta = mux_gen.make_mux_vectors(
                    grp, expansion, mode=mux_gen.coverage_mode(mode, exhaustive),
                    max_tests=max_tests, data_overrides=(mux_data or None))
                res.bindings = expansion.get("bindings", {})
                # M1（缝A 第三分支）：dft 门控的 mux 输出补 iddq DFT 拍 + 把门当显式输入——与 build mux
                # 循环(generator.build:1380/1383)【完全同口径】(同 obs 键、不传 input_bases)，使 res.vectors
                # ==.sv 的 mux 块向量。此前 MUX 分支从不调 → GUI 真表少一列、n_vectors 与 .sv 差 1(刚修
                # iddq bug 的第三个未修分支，logic/register 早已补)。无门时两调用均 no-op。
                obs = getattr(root, "dft_obs_name", None) or grp.out_base.lower()
                _skip = G._append_dft_vectors(obs, res.vectors, wb, resolver)
                if _skip:
                    res.meta["iddq_skipped"] = _skip
                res.dft_gate = G.pin_dft_gate(obs, res.vectors, wb, resolver)
        except cone.ConeError as ex:
            res.status = "error"
            res.issues.append("mux 展开失败: %s" % ex)
        except Exception as ex:   # noqa: BLE001 —— 护栏3：永不抛
            res.status = "error"
            res.issues.append("分析异常: %r" % ex)
        return res

    res.status = "error"
    res.issues.append("未知根类型 %r" % root.kind)
    return res


def _cov_for(sig_cov, name, mode, exhaustive):
    """单点覆盖度(R25)：sig_cov[name_low]∈{min,max,exhaustive} 命中则压全局，返回 (mode, exhaustive)；
    不命中=跟随全局。Topout 单点档按【Topout 名】键，会话内临时档（GUI 不存盘，见 R25 教训）。"""
    c = (sig_cov or {}).get(str(name).lower())
    if c in ("min", "max", "exhaustive"):
        return G._decompose_cov(c)
    return mode, exhaustive


def analyze_all(wb, resolver, mode="min", max_tests=256, exhaustive=False,
                want_vectors=True, sig_cov=None):
    """对 wb.topout 全清单逐信号分析。返回 list[TopoutResult]（外层枚举源=Topout B 列）。
    sig_cov：单点覆盖度 {Topout名低: min/max/exhaustive}，命中则该信号压全局档（N3）。"""
    logic_idx, mux_idx = build_index(wb)
    rename = _rename_map(wb)            # 合并改名表(dft+level_shift)，一次建好，逐信号复用
    out = []
    for topo in wb.topout:
        m, ex = _cov_for(sig_cov, topo.name, mode, exhaustive)
        root = resolve_root(wb, topo.name, logic_idx, mux_idx, rename=rename)
        out.append(analyze_signal(wb, resolver, topo, root=root, mode=m,
                                  max_tests=max_tests, exhaustive=ex,
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
        # 组键 = 基名+切片(小写)；输入金标准按【组键】给值时直接用，按【基名】给值时是整寄存器值，
        # 要按该组的 slice_lsb 取出本组那几位（否则同一寄存器的两个切片组会都拿到整值、装错——R27 位拆分坑）。
        if g["key"] in base_values:
            bv[g["key"]] = base_values[g["key"]] & E.mask(g["width"])
        elif g["base"].lower() in base_values:
            rep_b = result.bindings.get(g.get("rep"))
            lsb = (getattr(rep_b, "slice_lsb", 0) or 0) if rep_b is not None else 0
            bv[g["key"]] = (base_values[g["base"].lower()] >> lsb) & E.mask(g["width"])
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
        # cone 展开失败/未建出节点 → 标 no-cone 记账，别让 evaluate_at 抛 ValueError 把【整批】对照
        # 拖崩（护栏3：永不崩；真表有成环/不可解析信号时，单信号失败不连累其余金标准列）。
        if result.node is None or result.status != "ok":
            rep["status"] = "no-cone"
            rep["note"] = "; ".join(result.issues) or result.status
            reports.append(rep)
            continue
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
    # M5：生成期三检查计数（selfaudit 假绿 / regmap 重名 / RTL 补充）——账目此前只数 cone issues，
    # 把『RW 写值截断假绿 / regmap 重名 / 补充偏离』全漏报。复用 build_for_topout 的警告通道补三独立列。
    pp = getattr(resolver, "wire_prefixes", None)
    _bs = build_for_topout(wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                           probe_prefixes=pp)["summary"]
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
        # 生成期假绿可疑计数（M5）：独立于 cone issues，让账目把『读错对象/写值截断/重名/补充』也亮出来
        "n_selfaudit_warnings": _bs.get("n_selfaudit_warnings", 0),
        "n_regmap_warnings": _bs.get("n_regmap_warnings", 0),
        "n_supplement": _bs.get("n_supplement", 0),
    }
    return {"rows": rows, "summary": summary}


def topout_fortest_rows(wb, resolver, mode="min", max_tests=256, exhaustive=False,
                        probe_prefixes=None):
    """Topout for_test 回填行（块B 陷阱③，2026-06-23）：以 Topout 名为外层，**含 mux 根**。

    复用 generator.report 产出报告表(logic+mux 都带 for_test 回填键)，再交 fortest_writer
    以 include_mux=True 渲染——堵『_logic_tables 只留 is_logic、悄悄丢所有 mux 表』那个陷阱。
    返回 fortest_writer.build_fortest_rows 的 group 列表（每个对应一个 Topout 真值表）。"""
    from . import fortest_writer
    rep = report_for_topout(wb, resolver, mode=mode, max_tests=max_tests,
                            exhaustive=exhaustive, probe_prefixes=probe_prefixes)
    return fortest_writer.build_fortest_rows(rep, include_mux=True)


def _register_report_table(result):
    """直连寄存器根 → 完整表 dict：视图字段(inputs/tests values/auto_label) + for_test 字段(tests
    raw/writes/exp_num，走 compute_drives 拿权威写值) + HTML 字段(is_logic 等)。M7：register 根此前不进
    report_for_topout → for_test 回填整批丢；本表统一供 view_models / HTML / for_test 三处消费。"""
    groups = V.input_groups(result.node, result.bindings)
    used = E.collect_vars(result.node)
    out_w = result.out_width or 1
    slc = "[%d:0]" % (out_w - 1) if out_w > 1 else ""
    # m2：iddq 门作只读输入行（门在向量 extra_forces、不在 cone groups）——HTML/CSV/for_test 都带上，
    # 与 logic 根报告同口径(generator._report_core 的门行)。无门(gate=None)→ 不加、逐字节同 M7 原表。
    gate = getattr(result, "dft_gate", None)
    inputs = [G._input_meta(g, result.bindings) for g in groups]
    if gate:
        gb = gate[0]
        inputs.append({"label": gb.base, "letters": "dft门", "base": gb.base, "kind": "RO",
                       "ro": True, "addr": None, "reg_lsb": None, "reg_msb": None,
                       "slice_lsb": None, "slice_msb": None, "wire": gb.wire, "width": 1})
    tests = []
    for vec in result.vectors:
        bv = V.vector_to_base_values(vec, groups)
        _forces, writes, _unres = W.compute_drives(vec, result.bindings, used)
        vals = [G._fmt_cell(bv.get(g["key"], 0), g["width"]) for g in groups]
        raw = [bv.get(g["key"], 0) for g in groups]
        if gate:                                    # 门值：功能拍=透传值、DFT 拍=force 的常量支值
            gv = (1 - gate[1]) if getattr(vec, "dft_pitch", False) else gate[1]
            vals.append(G._fmt_cell(gv, 1)); raw.append(gv)
        tests.append({
            "name": W.test_label(vec), "neg": vec.is_negative, "values": vals,
            "auto_out": G._fmt_cell(vec.exp_value, vec.exp_width),
            "expected": G._fmt_cell(vec.asserted_value, vec.exp_width),
            # for_test 回填字段(M7)：raw=逐输入整数、writes=compute_drives 权威 RF_WRITE、exp_num=断言期望
            "raw": raw, "writes": writes, "exp_num": vec.asserted_value,
        })
    disp = _topout_disp_name(result.topo, result.out_width)
    return {
        # sv_net=寄存器根顶层口真名(裸名，不含测试台前缀——见 _sv_net_for 注，对抗 review F1)
        "R": "", "signal": disp, "sv_net": disp,
        "owner": result.topo.owner, "type": REGISTER, "expr": "",
        "is_logic": True, "out_width": out_w, "chain": [], "supplement": "",
        "inputs": inputs, "tests": tests,
        "auto_label": "auto_out%s" % slc, "exp_label": "期望(out)%s" % slc,
        "topout_name": result.topo.name,
    }


def report_for_topout(wb, resolver, mode="min", max_tests=256, exhaustive=False,
                      probe_prefixes=None, only=None, sig_cov=None,
                      neg_all=False, neg_signals=None, neg_which="first",
                      neg_mode="invert", neg_value=None):
    """生成【限定到 Topout 清单】的报告(复用 generator.report，再按 Topout B 列名过滤 tables/detail)。

    新模型『要验什么』只在 Topout——旧 report 枚举全 logic/mux，这里只保留 Topout 命中的表，
    且 owner 用 Topout A 列覆盖（块B 不 join）。旧 generator.report 一字不动。
    probe_prefixes：force 叶子 + 探针层级前缀（与 .sv 同口径，让 GUI 富表/for_test 回填也带前缀）。
    only：限定只保留这些 Topout 名（GUI 勾选项过滤；None=全部，与 .sv 导出 only 同口径，N6）。
    sig_cov：单点覆盖度 {Topout名低: min/max/exhaustive}，映成源名键喂 generator.report（N3）。
    neg_*(m1)：全局/批量负向——logic/mux 经 G.report、register 经 add_negatives，让报告与 .sv 同口径。"""
    from . import generator as G
    logic_idx, mux_idx = build_index(wb)
    rename = _rename_map(wb)
    # 单点覆盖度：Topout 名 → 源对象 out_name（generator.report 按源名键 sig_cov），改名根用 resolve_root 求源。
    gen_sig_cov = {}
    for t in wb.topout:
        c = (sig_cov or {}).get(t.name.lower())
        if c in ("min", "max", "exhaustive"):
            rt = resolve_root(wb, t.name, logic_idx, mux_idx, rename=rename)
            if rt.kind in (LOGIC, MUX) and getattr(rt, "obj", None) is not None:
                gen_sig_cov[rt.obj.out_name.lower()] = c
    rep = G.report(wb, G.GenOptions(mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                                    include_risky=True, probe_prefixes=probe_prefixes,
                                    sig_cov=gen_sig_cov or None, neg_all=neg_all,
                                    neg_signals=neg_signals, neg_which=neg_which,
                                    neg_mode=neg_mode, neg_value=neg_value))
    only_low = {str(n).lower() for n in only} if only is not None else None
    want = {t.name.lower() for t in wb.topout
            if only_low is None or t.name.lower() in only_low}
    owner_of = {t.name.lower(): t.owner for t in wb.topout}
    topo_by_name = {t.name.lower(): t for t in wb.topout}
    # 改名反查：最终源名 → 顶层 Topout 名（report 的表按源名产出，这里桥回顶层名，否则改名信号的
    # 真值表会被当『不属 Topout』丢掉 → GUI/报告空表，违『绝不静默丢』）。链式改名(ls→dft→logic)用
    # resolve_root 求最终源名，覆盖多跳。
    src_to_top = {}
    for t in wb.topout:
        rt = resolve_root(wb, t.name, logic_idx, mux_idx, rename=rename)
        if rt.renamed and rt.source_name:
            src_to_top.setdefault(rt.source_name, t.name.lower())

    def _topout_name_of(signal):
        from .excel_model import _strip_width
        base = _strip_width(signal)[0].lower()
        if base in want:
            return base
        # 经 _ls / rtl_base / rtl_base_full / out_base 命中（d_en_refbuf → d_en_refbuf_ls 等）——
        # 候选集须与 build_index 的 _logic/_mux_candidates 对称(含 rtl_base_full)，否则 build_index 能
        # 命中、这里反查不到 → 信号被分析了却报告表被丢(空真值表的『ok』信号，违『绝不静默丢』)。
        s = logic_idx.get(base) or mux_idx.get(base)
        if s is not None:
            for cand in (getattr(s, "_ls_name", "") or "", s.rtl_base,
                         getattr(s, "rtl_base_full", "") or "", s.out_base):
                if cand and cand.lower() in want:
                    return cand.lower()
        if base in src_to_top:        # dft 改名：源表桥回顶层名
            return src_to_top[base]
        return None

    def _sv_net_for(nm, out_width):
        """该 Topout 信号的【顶层网真名】(for_test D 列/真表标题用，m3：取代源内部名)：
        改名根=probe_name+slice(passthrough 贴顶层真名)；普通/_ls logic/mux 根=源对象 rtl_name(含
        _ls/尾缀)。修的是【源内部名 vs 顶层真名】维度(d_en_vco_fc_fsm vs 顶层 d_en_vco_fc_ls)，否则
        designer 对不上。
        ⚠ 不含【测试台层级前缀】(ENV_RF.<probe_prefix>)——那是 .sv emit 时按 probe_prefixes 单独加的；
        for_test 页输入/输出网名【统一用裸名】(与输入 force 网名同口径，实证：未配前缀的输入行也裸名)，
        前缀维度与本字段正交(对抗 review F1：sv_net 非完整 .sv LHS、是顶层网名分量)。仅作附加字段，
        不动 t['signal']（保 report join/detail 用源名）。"""
        rt = resolve_root(wb, nm, logic_idx, mux_idx, rename=rename)
        topo = topo_by_name.get(nm)
        if rt.renamed and rt.probe_name and topo is not None:
            return _topout_disp_name(topo, out_width)
        if rt.kind in (LOGIC, MUX) and getattr(rt, "obj", None) is not None:
            return getattr(rt.obj, "rtl_name", None) or nm
        return nm

    kept_tables = []
    for t in rep.get("tables", []):
        nm = _topout_name_of(t.get("signal", ""))
        if nm is not None:
            t = dict(t)
            t["owner"] = owner_of.get(nm, t.get("owner", ""))
            t["topout_name"] = nm                # 块B：标回 Topout B 列名（视图模型/账目 join 用，纯附加）
            t["sv_net"] = _sv_net_for(nm, t.get("out_width", 1))   # m3：.sv 断言真名(for_test D 列用)
            kept_tables.append(t)
    # M7：直连寄存器根 G.report 不产(只有 logic/mux) → for_test 回填/HTML 真值表整批丢。按 TopoutResult
    # 走 compute_drives 组装 for_test schema 表补进 tables，统一供 view_models/HTML/for_test 三处消费。
    for topo in wb.topout:
        if only_low is not None and topo.name.lower() not in only_low:
            continue
        rt = resolve_root(wb, topo.name, logic_idx, mux_idx, rename=rename)
        if rt.kind != REGISTER:
            continue
        m, ex = _cov_for(sig_cov, topo.name, mode, exhaustive)
        r = analyze_signal(wb, resolver, topo, root=rt, mode=m, max_tests=max_tests, exhaustive=ex)
        if r.status == "ok" and r.vectors:
            if _topout_neg_enabled(topo.name, neg_all, neg_signals):    # m1：register 报告也带负向
                r.vectors = _apply_passthrough_negatives(r.vectors, neg_mode, neg_which, neg_value)
            kept_tables.append(_register_report_table(r))
    rep = dict(rep)
    rep["tables"] = kept_tables
    return rep


# ═════════════════════════ 块B（续）：Topout .sv 产出路径（report_for_topout 的 .sv 孪生） ═════════════════════════
class _PassthroughSig:
    """自定义 .sv 渲染占位：断言探针贴 Topout 顶层真名（直连寄存器 dft 恒等观测 / dft 改名信号共用）。

    render_signal_block 只读 out_name/out_base/rtl_name/assert_id/owner/out_width；node 已传入
    故不读 expr。assert_id 用 'TOP<i>'（不与 logic 数字 R / mux 'mux<N>' 标号撞，保证全局唯一）。"""

    _self_ref_suffixes = ()

    def __init__(self, name, width, owner, aid, suffix="topout-reg", rtl_name=None):
        self.out_name = name
        self.out_base = name
        # 断言 LHS 真名：默认=Topout 顶层真名（无前后缀，断言直接贴它）；register 直连 / dft 改名根
        # 带位宽切片时由 _topout_probe_block 传入 name+[msb:lsb]（否则断言丢切片 → 只验 bit0=假绿，
        # 且与信号清单显示名 aac_ctf_bit_sel[2:0] 不一致——2026-06-24 修）。
        self.rtl_name = name if rtl_name is None else rtl_name
        self.assert_id = aid
        self.owner = owner
        self.out_width = width or 1
        self.expr = "A"
        self.suffix = suffix


class _InjectSig:
    """供 generator._inject_block_warnings 的轻量信号（register / dft 改名 passthrough 根，S1 缝B 收口）。

    M6：register/改名根的 .sv 此前只 render_signal_block、绕过 build 的块顶 ⚠ 注入圈——本类把它们
    接回同一注入层(iddq_skipped/regmap_dup/selfaudit/claims)。两点保证金标准逐字节不变：
      · out_name 带位宽切片 → _selfaudit_output_width 视为有意为之而跳过(passthrough 断言本就贴全宽
        切片、非『漏位宽只验 bit0』假绿)；
      · _self_ref_suffixes 空 + 非 supplement → 探针自读/supplement 检查恒不触发(passthrough 无此隐患)。
    rtl_base 留裸名、rtl_name 带切片 → collect_claims 的 net_base/slice 与 .sv assert LHS 同口径。"""

    _self_ref_suffixes = ()
    _is_supplement = False
    _supplement_note = ""
    _ls_name = None
    ref_suffix = ""

    def __init__(self, probe_name, aid, out_width, slice_suffix, is_top=True):
        self.out_name = probe_name + slice_suffix
        self.out_base = probe_name
        self.rtl_base = probe_name
        self.rtl_name = probe_name + slice_suffix
        self.rtl_base_full = probe_name
        self.rtl_name_full = probe_name + slice_suffix
        self.assert_id = aid or ""
        self.out_width = out_width or 1
        self.is_top = is_top                     # Topout B 列=顶层真名 → claims top_output=True


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


def _topout_slice_suffix(topo, out_width):
    """Topout 断言探针 LHS / 信号清单显示名共用的位宽切片后缀（2026-06-24 抽出，单一口径）：
    B 列写了显式切片 → 用它（忠于真表，含 [14:12] 这种非零起始）；裸名但有效位宽>1（寄存器字段宽
    推断出的多 bit，如 d_vco_en_faston）→ 补 [w-1:0]；1 bit 裸名不加（返回空串）。
    register 直连 / dft 改名根的 passthrough .sv 块据此把断言贴成 name[msb:lsb]（否则丢切片）。"""
    if topo.msb is not None and topo.lsb is not None:
        return "[%d:%d]" % (topo.msb, topo.lsb)
    w = int(out_width or getattr(topo, "width", 1) or 1)
    return "[%d:0]" % (w - 1) if w > 1 else ""


def _topout_probe_block(result, probe_name, owner, aid, kind, comments=False, counters=False,
                        probe_prefix="", owner_in_msg=False):
    """自定义 .sv 块：驱动来自源(result.node/bindings/vectors)，断言探针贴顶层真名 probe_name。
    用于直连寄存器根(probe=topo 名) 与 dft 改名根(probe=顶层名 A，驱动来自源 A_sm)。
    probe_prefix：顶层探针网若埋在子模块(罕见，顶层口一般在 ENV_RF 顶层)时的层级前缀。
    owner_in_msg(m5)：True 时断言消息尾追加 owner（与 logic 根同口径，否则半数 Topout 信号 log 无 owner）。
    位宽切片：断言 LHS 贴 probe_name+[msb:lsb]（与信号清单显示名同口径，否则多 bit 信号丢切片只验 bit0）。"""
    rtl = probe_name + _topout_slice_suffix(result.topo, result.out_width)
    sig = _PassthroughSig(probe_name, result.out_width, owner, aid, rtl_name=rtl)
    meta = dict(result.meta or {})
    meta.setdefault("truncated", False)
    lines, stats = W.render_signal_block(sig, result.bindings, result.vectors, meta,
                                         comments=comments, node=result.node,
                                         counters=counters, probe_prefix=probe_prefix,
                                         owner_in_msg=owner_in_msg)
    stats["topout_name"] = result.topo.name
    stats["topout_kind"] = kind
    stats["topout_status"] = result.status
    stats["cone_expanded"] = bool(result.expanded)
    return (lines, stats)


def _passthrough_block(result, probe_name, owner, aid, kind, opts, wb,
                       comments=False, counters=False, probe_prefix=""):
    """passthrough 根(register / dft 改名) → .sv 块 **+ generator 块顶 ⚠/claims 注入圈**（S1 缝B 收口 M6）。

    render_signal_block 出块后跑 generator._inject_block_warnings：register/改名根与 logic 根共用同一
    警告注入层（iddq_skipped/regmap_dup/selfaudit + claims，is_topout=True→provenance 标 topout），
    杜绝『裸渲染绕过 build 后处理』。金标准无门/无重名/无截断 → 注入零行 → 逐字节不变（实证）。
    返回 (lines, stats, warnings{regmap/supplement/selfaudit}, claims)。"""
    lines, stats = _topout_probe_block(result, probe_name, owner, aid, kind,
                                       comments=comments, counters=counters,
                                       probe_prefix=probe_prefix,
                                       owner_in_msg=getattr(opts, "owner_in_msg", False))
    ssuf = _topout_slice_suffix(result.topo, result.out_width)
    sig = _InjectSig(probe_name, aid, result.out_width, ssuf)
    lines, warnings, claims = G._inject_block_warnings(
        sig, result.node, result.bindings, lines, dict(result.meta or {}), opts, wb,
        is_topout=True)
    return lines, stats, warnings, claims


def _register_passthrough_block(result, owner, aid, opts, wb, comments=False, counters=False,
                                probe_prefix=""):
    """直连寄存器根 → 平凡 passthrough .sv 块：RF_WRITE 该寄存器 + 断言 顶层口 == 写值。
    经 _passthrough_block 跑同一警告注入圈（M6）。返回 (lines, stats, warnings, claims)。"""
    return _passthrough_block(result, result.topo.name, owner, aid, REGISTER, opts, wb,
                              comments=comments, counters=counters, probe_prefix=probe_prefix)


def _scope_empty_reason(scope):
    """导出范围过滤后该信号无用例 → 记账文案（neg=无负向 / pos=无正向，绝不静默丢，护栏3）。"""
    return ("仅负向导出：本信号无负向用例(不产断言)" if scope == "neg"
            else "仅正向导出：本信号无正向用例(不产断言)")


def _probe_prefix_for_name(probe_prefixes, name):
    """顶层探针名的层级前缀（probe_prefixes dict 按名小写键，命中返回前缀、否则空串）。
    顶层口通常在 ENV_RF 顶层(无需前缀)；个别埋子模块的才配，与 force 叶子共用同一 probe_prefixes。"""
    if not probe_prefixes or not name:
        return ""
    pp = {k.strip().lower(): v.strip() for k, v in probe_prefixes.items() if v and str(v).strip()}
    return pp.get(str(name).strip().lower(), "")


def build_for_topout(wb, mode="min", max_tests=256, exhaustive=False,
                     comments=False, sv_summary=False, owner_in_msg=False, only=None,
                     edit_overrides=None, probe_prefixes=None, scope="all", sig_cov=None,
                     logic_overrides=None, neg_all=False, neg_signals=None,
                     neg_which="first", neg_mode="invert", neg_value=None):
    """Topout .sv 产出（公共入口）。logic_overrides(M8)：RTL 补充逻辑 {基名: spec}，分析期临时换
    wb.logic(应用补充)再产出、退出还原（守 R32）；无 → 逐字节不变。
    neg_all/neg_signals/neg_which/neg_mode/neg_value(m1)：全局/批量负向——logic/mux 根经 GenOptions、
    register/dft 改名根经 add_negatives 补自检负向（此前 Topout 负向只能逐信号编辑）。详见 _core。"""
    with _with_logic_overrides(wb, logic_overrides):
        return _build_for_topout_core(
            wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive, comments=comments,
            sv_summary=sv_summary, owner_in_msg=owner_in_msg, only=only,
            edit_overrides=edit_overrides, probe_prefixes=probe_prefixes, scope=scope,
            sig_cov=sig_cov, neg_all=neg_all, neg_signals=neg_signals,
            neg_which=neg_which, neg_mode=neg_mode, neg_value=neg_value)


def _topout_neg_enabled(name, neg_all, neg_signals):
    """该 Topout 信号是否要补全局/批量负向（m1）：neg_all 全开 / 名字在 neg_signals 集合里。"""
    if neg_all:
        return True
    ns = neg_signals or ()
    return str(name).lower() in {str(s).strip().lower() for s in ns if s}


def _apply_passthrough_negatives(vecs, neg_mode, neg_which, neg_value):
    """register/dft 改名 passthrough 根补全局自检负向（m1）：只对功能正向加负向、DFT 拍(iddq=1 压0)
    不补(它本身是常量支自检)，与 build 的 logic/mux 同口径([pos,neg,pitch] 排序)，再重排 T 编号。"""
    pitch = [v for v in vecs if getattr(v, "dft_pitch", False)]
    func = [v for v in vecs if not getattr(v, "dft_pitch", False)]
    out = V.add_negatives(func, mode=neg_mode, which=neg_which, fixed_value=neg_value) + pitch
    for i, v in enumerate(out):
        v.index = i
    return out


def _build_for_topout_core(wb, mode="min", max_tests=256, exhaustive=False,
                           comments=False, sv_summary=False, owner_in_msg=False, only=None,
                           edit_overrides=None, probe_prefixes=None, scope="all", sig_cov=None,
                           neg_all=False, neg_signals=None, neg_which="first",
                           neg_mode="invert", neg_value=None):
    """以 **Topout B 列为外层** 产出 .sv 块清单（块B，report_for_topout 的 .sv 孪生，只新增）。

    · logic/mux 根：复用 generator.build（按 Topout 源 out_name 过滤 + 按 B 列序重排 +
      owner 用 Topout A 列覆盖 stats）——所有 DFT 拍/负向/去重/警告/dup-label 检查照旧。
    · 直连寄存器(RW)根：渲染平凡 passthrough 块（顶层口 == 写值）。
    · RO 回读 / 未解析 / error / 同源已覆盖：块顶注释记账（护栏3，绝不静默丢）。

    only：限定只产出这些 Topout 名（GUI 导出/预览勾选项用；None=全部）。
    edit_overrides：GUI 真值表编辑回流（2026-06-24，additive；None=无编辑、逐字节同旧）。dict：
        {"vector_overrides": {源out_name低: [TestVector]}（logic 源；空列表=清零）,
         "reg_overrides":    {Topout名低: [TestVector]}（直连寄存器根；空列表=清零）,
         "mux_user_vecs"/"mux_expected"/"mux_dropped"/"mux_cleared"/"mux_data": 透传 GenOptions（mux 源）}
    cone 默认级联、include_risky=True（Topout 名 cone 已展到源、前后缀整类问题消失，逃生阀属『排查(旧)』）。
    sv_summary=True 时各块带计数器（counters）——调用方须 render_file(summary=True) 包一次命名块。

    返回 {'blocks':[(lines,stats)], 'results':[TopoutResult], 'accounted':[…], 'summary':{…}}。"""
    from . import resolver as R
    eo = edit_overrides or {}
    # 导出范围 scope(all/pos/neg)：Topout 负向全来自 eo、自动向量恒正向 → 过滤 eo 即覆盖正/负；
    # neg 时 neg_src=有负向的源名集合，下面把 generator.build 信号集限到它们(否则冒出正向自动信号)。
    scope = scope if scope in ("pos", "neg") else "all"
    eo, neg_src = G.scope_filter_overrides(eo, scope)
    # 干净 cone 默认 + force 叶子(RO 输入/iddq 门)层级前缀(wire_prefixes)——顶层探针展到底后无前缀问题，
    # 但 cone 叶子的 RO readback / iddq 门若埋子模块仍需前缀，否则 force `ENV_RF.<裸名> CUVUNF。
    resolver = R.Resolver(wb, wire_prefixes=probe_prefixes)
    results = analyze_all(wb, resolver, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                          sig_cov=sig_cov)
    only_low = {str(n).lower() for n in only} if only is not None else None
    if only_low is not None:
        results = [r for r in results if r.topo.name.lower() in only_low]

    # logic/mux 根 → 源对象名集合（generator.build 据此选；out_name 与 out_base 都给，匹配两种写法）。
    # dft 改名根【不】走 generator.build（它会按源名 A_sm 探针、且占标号）——改名根走下面 _topout_probe_block
    # 自定义块（探针贴顶层名 A），故这里排除，避免 build 白产一个探 A_sm 的多余块。
    want_names = set()
    gen_sig_cov = {}            # 单点覆盖度：Topout 名键 → 映成 generator.build 要的【源 out_name】键（N3）
    for r in results:
        if r.status == "ok" and r.root.kind in (LOGIC, MUX) and not r.root.renamed:
            want_names.add(r.root.obj.out_name.lower())
            want_names.add(r.root.obj.out_base.lower())
            c = (sig_cov or {}).get(r.topo.name.lower())
            if c in ("min", "max", "exhaustive"):
                gen_sig_cov[r.root.obj.out_name.lower()] = c
    if scope == "neg":
        # 仅负向：只 build【有负向的源】(neg_src=源 out_name)，其余正向自动信号不出现（仅记账）。
        want_names &= (neg_src or set())

    opts = G.GenOptions(mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                        include_risky=True, comments=comments, sv_summary=sv_summary,
                        owner_in_msg=owner_in_msg, signals=want_names,
                        suppress_mux_bare_probe=True,           # t1：Topout .sv 抑制 mux 裸名探针噪声
                        neg_all=neg_all, neg_signals=neg_signals,    # m1：logic/mux 根全局/批量负向
                        neg_which=neg_which, neg_mode=neg_mode, neg_value=neg_value,
                        probe_prefixes=probe_prefixes, sig_cov=gen_sig_cov or None,
                        vector_overrides=eo.get("vector_overrides") or None,
                        mux_user_vecs=eo.get("mux_user_vecs"),
                        mux_expected=eo.get("mux_expected"),
                        mux_data=eo.get("mux_data"),
                        mux_dropped=eo.get("mux_dropped"),
                        mux_cleared=eo.get("mux_cleared"))
    built = G.build(wb, opts)
    by_src = {st["out_name"].lower(): (ln, st) for ln, st in built["blocks"]}
    reg_ov = {str(k).lower(): v for k, v in (eo.get("reg_overrides") or {}).items()}

    blocks, accounted, seen_src = [], [], set()
    reg_i = 0
    # S1 警告通道 + claims 聚合(M3/M4)：logic/mux 根来自 built（G.build 已算好 selfaudit/regmap/
    # supplement/claims）；register/dft 改名根来自 _passthrough_block 的注入圈（M6，与 logic 根同一层）。
    regmap_warnings = list(built.get("regmap_warnings") or [])
    supplement_warnings = list(built.get("supplement_warnings") or [])
    selfaudit_warnings = list(built.get("selfaudit_warnings") or [])
    claims = []
    claims_by_src = {}        # 源 out_name低 → 该块 claims（只把【真正 emit 的】源 claim 计入，不含被去重/记账的）
    for _c in (built.get("claims") or []):
        claims_by_src.setdefault(str(_c.get("signal", "")).lower(), []).append(_c)
    for r in results:
        name, owner = r.topo.name, r.topo.owner
        if r.status == "ok" and r.root.renamed:
            # dft 改名信号：逻辑/驱动来自源(node/bindings/vectors 已是源的)，断言探针贴顶层名 probe_name。
            ov = reg_ov.get(name.lower())
            if r.node is not None and r.bindings is not None:
                if ov is not None and len(ov) == 0:        # GUI 清零 → 记账
                    _why = "用户已清空(零用例，本信号不产出测试)"
                    blocks.append(_account_block(name, r.root.kind, "cleared", _why, owner))
                    accounted.append({"name": name, "kind": r.root.kind, "status": "cleared", "reason": _why})
                    continue
                if ov is not None:
                    r.vectors = list(ov)
                elif _topout_neg_enabled(name, neg_all, neg_signals):    # m1：全局/批量负向
                    r.vectors = _apply_passthrough_negatives(r.vectors, neg_mode, neg_which, neg_value)
                r.vectors = G.scope_filter_vectors(r.vectors, scope)
                if not r.vectors:                          # 仅正向/仅负向过滤后无用例 → 记账
                    _why = _scope_empty_reason(scope)
                    blocks.append(_account_block(name, r.root.kind, "scope-empty", _why, owner))
                    accounted.append({"name": name, "kind": r.root.kind, "status": "scope-empty", "reason": _why})
                    continue
                ln, st, _w, _c = _passthrough_block(
                    r, r.root.probe_name, owner, "TOP%d" % reg_i, r.root.kind, opts, wb,
                    comments=comments, counters=sv_summary,
                    probe_prefix=_probe_prefix_for_name(probe_prefixes, r.root.probe_name))
                reg_i += 1
                blocks.append((ln, st))
                regmap_warnings.extend(_w["regmap"]); supplement_warnings.extend(_w["supplement"])
                selfaudit_warnings.extend(_w["selfaudit"]); claims.extend(_c)
            else:                                          # 改名指向 mux 源(无单一 node)→ 暂记账（少见）
                _why = "dft 改名指向 mux 源——探针名覆盖暂未支持改名 mux（记账，不静默丢）"
                blocks.append(_account_block(name, r.root.kind, "renamed-mux", _why, owner))
                accounted.append({"name": name, "kind": r.root.kind, "status": "renamed-mux", "reason": _why})
            continue
        if r.status == "ok" and r.root.kind in (LOGIC, MUX):
            src = r.root.obj.out_name.lower()
            if scope == "neg" and src not in (neg_src or set()):  # 仅负向：该源无负向 → 记账，不产出
                _why = _scope_empty_reason(scope)
                blocks.append(_account_block(name, r.root.kind, "scope-empty", _why, owner))
                accounted.append({"name": name, "kind": r.root.kind, "status": "scope-empty", "reason": _why})
                continue
            if src in seen_src:                    # 两个 Topout 名映到同一源对象 → 只产出一次（防 dup-label）
                _why = "与已产出的同源信号共用一个源对象（避免断言标号重复）"
                blocks.append(_account_block(name, r.root.kind, "dup-source", _why, owner))
                accounted.append({"name": name, "kind": r.root.kind, "status": "dup-source",
                                  "reason": _why})
                continue
            blk = by_src.get(src)
            if blk is None:                        # include_risky=True 仍被 build 跳过（规格冲突/空向量等）
                _vov = (eo.get("vector_overrides") or {})
                if src in _vov and len(_vov[src]) == 0:   # 用户在 GUI 清零了该 logic 信号 → 明确记账
                    _why = "用户已清空(零用例，本信号不产出测试)"
                    blocks.append(_account_block(name, r.root.kind, "cleared", _why, owner))
                    accounted.append({"name": name, "kind": r.root.kind, "status": "cleared", "reason": _why})
                    continue
                reason = "; ".join(r.issues) or "generator.build 未产出该块（规格冲突/空向量/被跳过，见账目）"
                blocks.append(_account_block(name, r.root.kind, "skipped", reason, owner))
                accounted.append({"name": name, "kind": r.root.kind, "status": "skipped", "reason": reason})
                continue
            seen_src.add(src)
            claims.extend(claims_by_src.get(src, []))     # 该源块的 logic/mux claims（M4，只计 emit 的）
            ln, st = blk
            st = dict(st); st["owner"] = owner; st["topout_name"] = name
            st["topout_kind"] = r.root.kind; st["topout_status"] = "ok"
            blocks.append((list(ln), st))
        elif r.status == "ok" and r.root.kind == REGISTER:
            ov = reg_ov.get(name.lower())
            if ov is not None and len(ov) == 0:    # 用户清零该直连寄存器 → 记账（零用例，不静默丢）
                _why = "用户已清空(零用例，本信号不产出测试)"
                blocks.append(_account_block(name, REGISTER, "cleared", _why, owner))
                accounted.append({"name": name, "kind": REGISTER, "status": "cleared", "reason": _why})
                continue
            if ov is not None:                     # 用户编辑过该直连寄存器的真值表 → 用编辑后的向量
                r.vectors = list(ov)
            elif _topout_neg_enabled(name, neg_all, neg_signals):    # m1：全局/批量负向
                r.vectors = _apply_passthrough_negatives(r.vectors, neg_mode, neg_which, neg_value)
            r.vectors = G.scope_filter_vectors(r.vectors, scope)
            if not r.vectors:                      # 仅正向/仅负向过滤后无用例 → 记账
                _why = _scope_empty_reason(scope)
                blocks.append(_account_block(name, REGISTER, "scope-empty", _why, owner))
                accounted.append({"name": name, "kind": REGISTER, "status": "scope-empty", "reason": _why})
                continue
            ln, st, _w, _c = _register_passthrough_block(
                r, owner, "TOP%d" % reg_i, opts, wb,
                comments=comments, counters=sv_summary,
                probe_prefix=_probe_prefix_for_name(probe_prefixes, r.topo.name))
            reg_i += 1
            blocks.append((ln, st))
            regmap_warnings.extend(_w["regmap"]); supplement_warnings.extend(_w["supplement"])
            selfaudit_warnings.extend(_w["selfaudit"]); claims.extend(_c)
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
        "n_designer": sum(s.get("n_designer", 0) for _l, s in blocks),
        # S1 生成期警告计数（M3/M5）：assert 假绿可疑 / regmap 重名 / RTL 补充偏离——账目/汇总据此不再恒 0
        "n_selfaudit_warnings": len(selfaudit_warnings),
        "n_regmap_warnings": len(regmap_warnings),
        "n_supplement": len(supplement_warnings),
    }
    # 重复 assert 标号(非法 SV)：generator.build 已算好(logic/mux 共用同一 R 时)，原样透出供导出前确认，
    # 别像旧 Topout 路径那样丢弃 → 静默导出会 elaboration 失败的 .sv（N9）。
    # 警告通道 + claims 透出（S1 M3/M4）：让 CLI/GUI 账目看到假绿/重名/补充，并能 --export-claims。
    return {"blocks": blocks, "results": results, "accounted": accounted, "summary": summary,
            "dup_labels": built.get("dup_labels") or [],
            "regmap_warnings": regmap_warnings, "supplement_warnings": supplement_warnings,
            "selfaudit_warnings": selfaudit_warnings, "claims": claims}


def render_topout_sv(wb, mode="min", max_tests=256, exhaustive=False,
                     comments=False, sv_summary=False, owner_in_msg=False, only=None,
                     edit_overrides=None, probe_prefixes=None, scope="all", sig_cov=None,
                     logic_overrides=None, neg_all=False, neg_signals=None,
                     neg_which="first", neg_mode="invert", neg_value=None):
    """便捷封装：build_for_topout → sv_writer.render_file → (text, build_dict)。
    logic_overrides(M8)/neg_*(m1)：透传 build_for_topout。"""
    b = build_for_topout(wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                         comments=comments, sv_summary=sv_summary, owner_in_msg=owner_in_msg,
                         only=only, edit_overrides=edit_overrides, probe_prefixes=probe_prefixes,
                         scope=scope, sig_cov=sig_cov, logic_overrides=logic_overrides,
                         neg_all=neg_all, neg_signals=neg_signals, neg_which=neg_which,
                         neg_mode=neg_mode, neg_value=neg_value)
    text = W.render_file(b["blocks"], comments=comments, summary=sv_summary)
    return text, b


# ═════════════════════════ 块B（续）：Topout GUI 视图模型（每信号一个，按 B 列序） ═════════════════════════
def _fill_register_model(m, result):
    """直连寄存器根的真值表视图（与 report 表同形：inputs/tests/auto_label/exp_label）。
    委托 _register_report_table 单一构造器（M7 后 view_models 多走 report_for_topout 表，本函数为兜底）。"""
    t = _register_report_table(result)
    m["inputs"] = t["inputs"]
    m["tests"] = t["tests"]
    m["auto_label"] = t["auto_label"]
    m["exp_label"] = t["exp_label"]
    m["n_vectors"] = len(t["tests"])


def _topout_disp_name(topo, out_width):
    """信号清单【显示名】：带位宽切片(aac_ctf_bit_sel[2:0])。name(key)仍是剥位宽的基名，仅显示用。
    切片后缀与断言 LHS 同口径（_topout_slice_suffix）：显示什么、.sv 就断言什么，不再两套规则。"""
    return "%s%s" % (topo.name, _topout_slice_suffix(topo, out_width))


def _topout_probe_net(r):
    """该 Topout 信号 assert LHS 的网名（探针前缀按它查；与 build_for_topout 的 .sv 同口径）：
    dft 改名根→probe_name；logic/mux 根→源 rtl_base；直连寄存器根/兜底→topo 名。"""
    root = r.root
    if root.renamed and root.probe_name:
        return root.probe_name
    if root.kind in (LOGIC, MUX) and root.obj is not None and not root.renamed:
        return getattr(root.obj, "rtl_base", None) or r.topo.name
    return r.topo.name


def topout_view_models(wb, mode="min", max_tests=256, exhaustive=False, probe_prefixes=None,
                       sig_cov=None, logic_overrides=None):
    """每信号视图模型（公共入口）。logic_overrides(M8)：分析期临时换 wb.logic(应用 RTL 补充)，
    使 GUI 真值表显示补充【后】逻辑（否则静默假绿）；无 → 逐字节不变。详见 _topout_view_models_core。"""
    with _with_logic_overrides(wb, logic_overrides):
        return _topout_view_models_core(wb, mode=mode, max_tests=max_tests,
                                        exhaustive=exhaustive, probe_prefixes=probe_prefixes,
                                        sig_cov=sig_cov)


def _topout_view_models_core(wb, mode="min", max_tests=256, exhaustive=False, probe_prefixes=None,
                             sig_cov=None):
    """每个 Topout 信号一个【视图模型】（GUI / 无头测试消费），按 Topout B 列序。

    干净 cone 默认（Topout 视图不放级联/尾缀/top_output——那些属『排查(旧)』）。
      · logic/mux 根 → 复用 report_for_topout 富格式表（chain/inputs/tests，值已格式化）；
      · 直连寄存器根 → 从 TopoutResult 建平凡表；
      · RO 回读 / 未解析 / error → 只记账（无真值表，note/issues 说明原因，绝不崩）。
    probe_prefixes：force 叶子(RO/iddq) + 探针层级前缀（与 .sv 同口径）。
    sig_cov：单点覆盖度 {Topout名低: min/max/exhaustive}，命中则清单『用例』数与编辑/导出同档（N3）。
    每个模型：{name, owner, width, kind, status, note, issues, matched_name, n_leaves,
              chain:[{out,expr,subst}], inputs:[…], tests:[…], auto_label, exp_label, n_vectors}。"""
    from . import resolver as R
    resolver = R.Resolver(wb, wire_prefixes=probe_prefixes)
    results = analyze_all(wb, resolver, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                          sig_cov=sig_cov)
    rep = report_for_topout(wb, resolver, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                            probe_prefixes=probe_prefixes, sig_cov=sig_cov)
    tbl_by_topout = {}
    for t in rep.get("tables", []):
        nm = t.get("topout_name")
        if nm:
            tbl_by_topout.setdefault(nm.lower(), t)

    models = []
    reg_i = 0                                                # 与 build_for_topout 的 TOP<n> 计数器同口径
    for r in results:
        _shape = result_form(wb, r) if r.status == "ok" else None
        m = {"name": r.topo.name, "disp": _topout_disp_name(r.topo, r.out_width),
             "owner": r.topo.owner, "width": r.out_width,
             "kind": r.root.kind, "status": r.status, "note": r.note,
             "issues": list(r.issues), "matched_name": r.root.matched_name,
             "n_leaves": r.n_leaves, "n_vectors": len(r.vectors),
             # #2 逻辑类型(展开后表达式形态 F0-F4)：信号清单列 + #3 覆盖按它派发
             "form": (_shape.kind if _shape else ""),
             "form_label": form_label(_shape),
             "chain": [], "inputs": [], "tests": [], "auto_label": "", "exp_label": ""}
        pnet = _topout_probe_net(r)                          # assert LHS 探针网 + 配的层级前缀
        m["probe_net"] = pnet
        m["prefix"] = _probe_prefix_for_name(probe_prefixes, pnet)
        # 断言号 = .sv assert 标签的 <R> 部分（仿真 log 报 assert_<R>_T<n>，据此回查信号）：
        #   logic/mux 根 = 源对象 assert_id（logic=Excel R 列、mux=mux<id>，固定、≠清单位置）；
        #   寄存器/dft 改名根 = TOP<n>（build 按 B 列序给可产出块顺序编号，复刻其计数器）。
        if r.status == "ok" and r.root.renamed:
            _emit = r.node is not None and r.bindings is not None and bool(r.vectors)
            m["assert_id"] = ("TOP%d" % reg_i) if _emit else ""
            reg_i += 1 if _emit else 0
        elif r.status == "ok" and r.root.kind == REGISTER:
            m["assert_id"] = ("TOP%d" % reg_i) if r.vectors else ""
            reg_i += 1 if r.vectors else 0
        elif r.status == "ok" and r.root.kind in (LOGIC, MUX) and r.root.obj is not None:
            m["assert_id"] = str(getattr(r.root.obj, "assert_id", "") or "")
        else:
            m["assert_id"] = ""
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


def _account_error_text(row):
    """账目行 → 报告 summary 的『错误/原因』列文案（RO 回读/未解析/error 不空，ok 留空）。"""
    if row["status"] == "ok":
        return "; ".join(row["issues"]) if row["issues"] else ""
    if row["status"] == "skip":
        return "跳过(RO 回读，无 cone)：%s" % (row["note"] or "")
    if row["status"] == "unresolved":
        return "未解析：%s" % (row["note"] or "")
    return "; ".join(row["issues"]) or row["note"] or row["status"]


def topout_report(wb, mode="min", max_tests=256, exhaustive=False, probe_prefixes=None,
                  only=None, sig_cov=None, logic_overrides=None,
                  neg_all=False, neg_signals=None, neg_which="first",
                  neg_mode="invert", neg_value=None):
    """Topout 限定报告（公共入口）。logic_overrides(M8)：分析期换 wb.logic(应用 RTL 补充)，使
    HTML/CSV 报告与 supplement banner 反映补充后逻辑；无 → 逐字节不变。
    neg_*(m1)：全局/批量负向，报告 tables/汇总 n_neg 与 .sv 同口径。详见 _topout_report_core。"""
    with _with_logic_overrides(wb, logic_overrides):
        return _topout_report_core(wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                                   probe_prefixes=probe_prefixes, only=only, sig_cov=sig_cov,
                                   neg_all=neg_all, neg_signals=neg_signals, neg_which=neg_which,
                                   neg_mode=neg_mode, neg_value=neg_value)


def _topout_report_core(wb, mode="min", max_tests=256, exhaustive=False, probe_prefixes=None,
                        only=None, sig_cov=None, neg_all=False, neg_signals=None,
                        neg_which="first", neg_mode="invert", neg_value=None):
    """Topout 限定报告（write_report 兼容：summary/detail/tables/verifiability），堵 3 静默陷阱：
      ① 默认不空：summary 直接来自 compose_topout_account（12 行全分类，不套 top_output_only）；
      ② 不刷 top_out=0 假警告：error 列只放真原因（RO/未解析/冲突），无 bare-probe/needs-prefix 噪声；
      ③ for_test 回填含 mux：tables 来自 report_for_topout（已含 mux）+ register 平凡表。

    summary/detail/verifiability 全限定到 Topout 清单（不像 report_for_topout 只过滤 tables）。
    probe_prefixes：force 叶子 + 探针层级前缀（与 .sv 同口径）。
    only：限定只报这些 Topout 名（GUI 勾选项过滤；None=全部，与 .sv 导出 only 同口径，N6）。"""
    from . import resolver as R
    resolver = R.Resolver(wb, wire_prefixes=probe_prefixes)
    only_low = {str(n).lower() for n in only} if only is not None else None
    rep = report_for_topout(wb, resolver, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                            probe_prefixes=probe_prefixes, only=only, sig_cov=sig_cov,
                            neg_all=neg_all, neg_signals=neg_signals, neg_which=neg_which,
                            neg_mode=neg_mode, neg_value=neg_value)
    acc = compose_topout_account(wb, resolver, mode=mode, max_tests=max_tests, exhaustive=exhaustive)
    if only_low is not None:                # 账目/汇总/可验证性同样限定到勾选信号
        acc = dict(acc); acc["rows"] = [r for r in acc["rows"] if r["name"].lower() in only_low]
    vms = topout_view_models(wb, mode=mode, max_tests=max_tests, exhaustive=exhaustive,
                             probe_prefixes=probe_prefixes, sig_cov=sig_cov)
    if only_low is not None:
        vms = [m for m in vms if m["name"].lower() in only_low]

    # tables：logic/mux 富表 + register 平凡表，均来自 report_for_topout（M7 后 register 也在其中，
    # 不再从 vms 二次拼装 → 杜绝 register 表重复 + 统一构造器）。
    tables = list(rep.get("tables", []))
    # detail：限定到存活 logic/mux 表的源信号（per-test 行；register/RO 无 per-test）
    kept_src = {str(t["signal"]).lower() for t in rep.get("tables", [])}
    detail = [d for d in rep.get("detail", []) if str(d.get("signal", "")).lower() in kept_src]

    # m7：汇总 tab 的 supplement 列此前恒空（真值表 tab suppbar 在、汇总不见）——从 tables 取 RTL 补充串
    # 按 Topout 名回填，让汇总/CSV/Excel 也能筛出补充信号（与真值表 banner 一致）。
    supp_by_name = {str(t.get("topout_name", "")).lower(): t.get("supplement", "")
                    for t in tables if t.get("supplement")}
    # m1：汇总 n_neg 此前硬编码 0——从 tables 数每信号负向 test，与 .sv/报告同口径（neg_all 才非 0）。
    neg_by_name = {}
    for t in tables:
        nm = str(t.get("topout_name", "")).lower()
        if nm:
            neg_by_name[nm] = neg_by_name.get(nm, 0) + sum(1 for x in t.get("tests", [])
                                                           if x.get("neg"))
    # summary + verifiability：以 Topout 账目为准（覆盖全 12，含 RO/未解析）
    summary, verif_signals, counts = [], [], {}
    for row in acc["rows"]:
        summary.append({
            "R": "", "signal": row["name"], "owner": row["owner"], "type": row["kind"],
            "top": "", "expr": "", "n_tests": row["n_vectors"],
            "n_neg": neg_by_name.get(row["name"].lower(), 0),
            "control": "", "data": "", "unresolved": "; ".join(row["issues"]),
            "supplement": supp_by_name.get(row["name"].lower(), ""),
            "error": _account_error_text(row)})
        vst = _VERIF_OF.get(row["status"], row["status"])     # ok→clean / skip 原样(RO 回读)
        counts[vst] = counts.get(vst, 0) + 1
        verif_signals.append({
            "R": "", "signal": row["name"], "owner": row["owner"], "type": row["kind"],
            "top": "", "status": vst, "detail": row["note"] or "; ".join(row["issues"]),
            "out_net": "`ENV_RF.%s" % row["name"]})
    return {"summary": summary, "detail": detail, "tables": tables,
            "verifiability": {"counts": counts, "signals": verif_signals}}
