# -*- coding: utf-8 -*-
"""
generator.py — 编排层：装载 Excel → 筛选信号 → 解析输入 → 生成向量(+负向) → 渲染 .sv。
CLI 与（未来的）GUI 共用此后端。
"""

from . import cone
from . import expr as E
from . import excel_model
from . import mux_gen
from . import resolver as R
from . import vectors as V
from . import sv_writer as W


class GenOptions:
    def __init__(self, owners=None, signals=None, signal_regex=None,
                 mode="min", max_tests=256, exhaustive=False,
                 neg_signals=None, neg_all=False, neg_mode="invert",
                 neg_which="first", neg_value=None,
                 force_overrides=None, rfwrite_overrides=None, default_kind=None,
                 top_output_only=False, types=None, wire_fallback=True,
                 exclude=None, exclude_regex=None, comments=False, include_risky=False,
                 vector_overrides=None, probe_prefixes=None,
                 owner_in_msg=False, sv_summary=False, negative_vectors_only=False,
                 cascade_mode="cone", gen_mux=True, mux_expected=None):
        self.owners = _norm_owner_set(owners)
        self.signals = _norm_set(signals)
        self.signal_regex = signal_regex
        self.exclude = _norm_set(exclude)
        self.exclude_regex = exclude_regex
        self.mode = mode
        self.max_tests = max_tests
        self.exhaustive = exhaustive
        self.neg_signals = _norm_set(neg_signals)
        self.neg_all = neg_all
        self.neg_mode = neg_mode
        self.neg_which = neg_which
        self.neg_value = neg_value
        self.force_overrides = force_overrides or []
        self.rfwrite_overrides = rfwrite_overrides or []
        self.default_kind = default_kind
        self.top_output_only = top_output_only
        self.types = _norm_set(types)
        self.wire_fallback = wire_fallback
        self.comments = comments
        self.include_risky = include_risky
        # {out_name(小写): [TestVector,...]} —— GUI 手工定制/编辑的测试项。某信号有 override 时
        # 用它替代自动向量生成，并跳过自动负向追加与 risky 跳过(用户已显式定制即视为有意为之)。
        self.vector_overrides = vector_overrides or None
        # {信号名(小写): 层级前缀}——输出网不在 ENV_RF 顶层而在子模块里时的探针前缀。
        # 实证(2026-06-02)：pll_n 在 U_BT_LP_PLL_DIG 子模块内部，探针须写
        # `ENV_RF.U_BT_LP_PLL_DIG.pll_n[31:0]，否则 CUVUNF。
        self.probe_prefixes = {k.strip().lower(): v.strip() for k, v in
                               (probe_prefixes or {}).items() if v and v.strip()}
        # 断言消息尾部追加 owner（logic P 列）：log 里直接看出 fail 的是谁的信号。
        # 前半段消息格式不变(追加式)，默认关以保持产物与旧版逐字节一致。
        self.owner_in_msg = bool(owner_in_msg)
        # 末尾测试汇总：产物包进命名 begin/end 块 + 计数器 + 汇总行
        # (signals/asserts/positive/negative + 运行时 REAL FAIL / NEG broken 数)。
        self.sv_summary = bool(sv_summary)
        # 真·仅负向：每个信号只保留负向向量(保持原 T 编号便于与"全部"导出对照)，
        # 无负向的信号整个跳过。CLI --neg-file separate 的负向文件用——
        # 之前是块级过滤(负向文件里混着正例)，汇总/REAL FAIL 统计会误导。
        self.negative_vectors_only = bool(negative_vectors_only)
        # 级联模式：输入引用"上游计算网"(级联到不自引用的 top 输出)时怎么驱动，见 级联模式说明.md：
        #   "cone"(默认) = 展开上游表达式驱动其源头寄存器（纯 Excel，不需要探针前缀）
        #   "force"      = 直接 force 字面 _to_logic 网（隔离验证每行；需要 scan_rtl 前缀）
        self.cascade_mode = cascade_mode if cascade_mode in ("cone", "force") else "cone"
        # mux 页验证（2026-06-03 第九轮）：默认开（用户拍板"logic+mux 都生成"）。
        # Excel 没有 mux 页时自然为空，不影响纯 logic 表。
        self.gen_mux = bool(gen_mux)
        # {mux信号名(小写): {输入取值键: int}} —— mux 测试的 designer 手填期望（第十一轮续）。
        # mux 向量由 case 结构自动生成、不走 vector_overrides，手填期望按 mux_assign_key
        # （输入取值的稳定序列化）对号入座；覆盖度切换后对不上键的期望宁可丢弃也不张冠李戴。
        self.mux_expected = {str(k).lower(): dict(v) for k, v in
                             (mux_expected or {}).items() if v}


def _norm_set(x):
    if not x:
        return None
    return {s.strip().lower() for s in x if s and s.strip()}


def _ws(s):
    """折叠多余空白并小写，用于 owner 等可能含空格的字段比较（如 'Wei  Yu'→'wei yu'）。"""
    return " ".join(str(s or "").split()).lower()


def _norm_owner_set(x):
    if not x:
        return None
    out = {_ws(s) for s in x if s and s.strip()}
    return out or None


def _name_matches(sig, names):
    """信号是否在指定名集合里（支持 K 全名与去位宽基名）。"""
    if names is None:
        return True
    cand = {sig.out_name.lower(), sig.out_base.lower()}
    return bool(cand & names)


def is_top_output(val):
    """logic N 列 top_output：=1 才是 RTL 可见、要验证的输出；=0 是内部信号(探不到)。"""
    return str(val).strip() in ("1", "1.0", "True", "true")


def select_signals(wb, opts):
    """按 owner / 名称 / 正则 / top_output / 类型 过滤 logic 信号；支持排除。"""
    import re
    rx = re.compile(opts.signal_regex, re.I) if opts.signal_regex else None
    exrx = re.compile(opts.exclude_regex, re.I) if opts.exclude_regex else None
    out = []
    for sig in wb.logic:
        if opts.owners is not None and _ws(sig.owner) not in opts.owners:
            continue
        if not _name_matches(sig, opts.signals):
            continue
        if rx and not (rx.search(sig.out_name) or rx.search(sig.out_base)):
            continue
        # 排除：按名集合 或 正则（匹配 K 全名或去位宽基名）
        if opts.exclude is not None and (sig.out_name.lower() in opts.exclude
                                         or sig.out_base.lower() in opts.exclude):
            continue
        if exrx and (exrx.search(sig.out_name) or exrx.search(sig.out_base)):
            continue
        if opts.top_output_only and not is_top_output(sig.top_output):
            continue
        if opts.types is not None and sig.suffix.lower() not in opts.types:
            continue
        out.append(sig)
    return out


def select_mux_groups(wb, opts):
    """按 owner / 名称 / 正则 / 排除 过滤 mux 组（2026-06-03 第九轮）。

    复用 select_signals 的过滤语义（MuxGroup 鸭子兼容 out_name/out_base/owner/suffix/top_output）。
    注意 --type 过滤：MuxGroup.suffix 固定为 "mux"，用 --type mux 可只筛 mux 组。
    """
    import re
    if not opts.gen_mux:
        return []
    rx = re.compile(opts.signal_regex, re.I) if opts.signal_regex else None
    exrx = re.compile(opts.exclude_regex, re.I) if opts.exclude_regex else None
    out = []
    for grp in wb.mux:
        if opts.owners is not None and _ws(grp.owner) not in opts.owners:
            continue
        if not _name_matches(grp, opts.signals):
            continue
        if rx and not (rx.search(grp.out_name) or rx.search(grp.out_base)):
            continue
        if opts.exclude is not None and (grp.out_name.lower() in opts.exclude
                                         or grp.out_base.lower() in opts.exclude):
            continue
        if exrx and (exrx.search(grp.out_name) or exrx.search(grp.out_base)):
            continue
        # 注意：top_output_only 不过滤 mux 组。logic 的 top_output=0 是"内部节点、根本不是端口、
        # 探不到"；而 mux 的 top_out=0 只是"喂内部、非芯片顶层输出"（WL 全部如此）——它们正是要
        # 验的信号，只是输出探针可能要前缀（见 mux_output_warning）。按 top_out 滤掉=对 WL 全军覆没。
        if opts.types is not None and grp.suffix.lower() not in opts.types:
            continue
        out.append(grp)
    return out


def _neg_enabled_for(sig, opts):
    if opts.neg_all:
        return True
    if opts.neg_signals is None:
        return False
    return sig.out_name.lower() in opts.neg_signals or sig.out_base.lower() in opts.neg_signals


def mux_assign_key(assignments):
    """mux 测试向量的稳定键：输入取值的有序序列化（"c:A=1;d:0=5;…"）。

    designer 手填的 mux 期望按它对号入座——用输入取值做键(而非 T 编号)，
    覆盖度切换/向量集变化后对不上键的期望自然丢弃(安全)，绝不会张冠李戴到别的测试上。"""
    return ";".join("%s=%d" % (k, v) for k, v in sorted(assignments.items()))


def apply_mux_expected(vecs, exp_map):
    """把 designer 手填期望写到 mux 向量上（按输入取值键匹配；负向不碰）。

    须在 add_negatives 之前调用——make_negative 的错值防撞需要看到 designer_expected。"""
    if not exp_map:
        return
    for v in vecs:
        if v.is_negative:
            continue
        de = exp_map.get(mux_assign_key(v.assignments))
        if de is not None:
            v.designer_expected = int(de) & E.mask(v.exp_width)


def probe_prefix_for(sig, opts):
    """该信号的探针层级前缀，没有则空串。

    映射 key 兼容两套名字：Excel K 列名（d_ndiv_cnt_div_sel）和 RTL 网名（d_ndiv_cnt_div_sel_ls）。
    scan_rtl.py 导出的映射用 RTL 网名，手工配置常用 K 列名——都认。
    """
    p = opts.probe_prefixes or {}
    rtl_base = getattr(sig, "rtl_base", sig.out_base)
    rtl_name = getattr(sig, "rtl_name", sig.out_name)
    return (p.get(sig.out_name.lower()) or p.get(sig.out_base.lower())
            or p.get(rtl_name.lower()) or p.get(rtl_base.lower()) or "")


def _mux_ctrl_desc(grp):
    """mux 组控制信号的人读描述（skipped 诊断用）：单控制=基名，多控制=拼接 {c1,c2}。"""
    if len(grp.ctrls) > 1:
        return "{%s}" % ",".join(c.base for c in grp.ctrls)
    return grp.ctrl_base


def mux_prefix_risks(grp, exp, opts):
    """硬阻断：force 一个【子模块内部网】但没配前缀的输入——没前缀 force 必 CUVUNF，默认跳过。
    （build/report/GUI 共用，口径必须一致；--include-risky 可强制生成。）

    这里只收【有实证依据确定会坏】的两类 force 输入，**不**包含输出探针的 top_out=0：
      ① 级联衔接网（needs-prefix / mux-output）——RTL 里是子模块内 assign 目标，force 基名钉不住；
      ② wire 兜底（tmm/regmap 查无、按裸基名 force）——该名多半 RTL 顶层不存在。
    输出探针的 top_out=0 是【不确定】的（见 mux_output_warning）——那只是数据流去向，不等于
    RTL 层级埋深，所以不当硬阻断，而是照常生成裸名 + 警告（2026-06-03 用户拍板）。

    返回 [(tag, name, reason), ...]；空 = 没有 force 阻断。
    LPBT（无级联 force 输入、数据全 RW）永远返回空——行为不变。
    """
    risks = []
    for k in exp["used_vars"]:
        b = exp["bindings"].get(k)
        if b is None:
            continue
        if b.found_in in ("needs-prefix", "mux-output"):
            risks.append(("mux", b.base,
                          "输入 %s 要 force 衔接网 %s（在子模块内部），需要探针前缀——跑 scan_rtl 后才能生成"
                          % (b.base, b.wire)))
        elif b.found_in == "wire":
            risks.append(("mux", b.base,
                          "输入 %s 表里查无字段，按 wire 兜底 force 裸名 %s——该网在 RTL 顶层多半不存在"
                          "（真网在子模块内），会 elaboration CUVUNF；请核对名称或用 --rfwrite-signals/"
                          "--force-signals 指定" % (b.base, b.wire)))
    return risks


def mux_output_warning(grp, opts):
    """top_out=0 输出没配前缀 → 用裸名探针 `ENV_RF.<名>。这是【警告】不是【阻断】：

    top_out（I 列）= 是不是芯片顶层输出端口（数据流去向）；WL 输出喂 to_logic/to_mux/to_dft
    所以 top_out=0。但"能不能在 ENV_RF.<名> 直接探到"是 RTL 层级问题——两者常相关但不等价，
    工具不替用户假设。照常生成裸名探针（和 LPBT 一样）：探得到就过；真 CUVUNF 了再跑 scan_rtl
    配前缀重生成（2026-06-03 用户拍板）。返回警告文案，或 ''（顶层/已配前缀=无警告）。
    """
    if not grp.is_top and not probe_prefix_for(grp, opts):
        return ("输出 %s top_out=0（喂内部，非芯片顶层输出）→ 当前用裸名探针 `ENV_RF.%s；"
                "若仿真 elaboration 报 CUVUNF 说明它埋在子模块，跑 scan_rtl 配探针前缀后重生成"
                % (grp.out_name, grp.rtl_name))
    return ""


def parse_probe_prefix_lines(text):
    """探针前缀映射文本 → dict。GUI 编辑器与映射文件共用同一格式：

        # 注释行
        pll_n=U_BT_LP_PLL_DIG
        mon_active=U_BT_LP_PLL_DIG.DIG_1

    每行 信号名=ENV_RF 下的层级路径。空行/#注释/无等号/空路径 → 跳过。"""
    out = {}
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        name, prefix = ln.split("=", 1)
        if name.strip() and prefix.strip():
            out[name.strip().lower()] = prefix.strip().strip(".")
    return out


def expand_signal(wb, resolver, sig, chain_out=None):
    """解析表达式并按需做 cone 展开（输入引用内部 logic 信号时递归代入其表达式）。

    返回 (node, bindings, expanded)：
      node     — 表达式 AST（cone 信号 = 复合 AST）
      bindings — 输入绑定（cone 信号 = 叶子寄存器绑定，键为大写基名）
      expanded — 是否做了 cone 展开
    chain_out — 传入 list 时，cone 展开把『展开链』追加进去（非 cone 信号不动它）：
      [{"out": 行基名, "expr": Excel 原式, "subst": 字母代入真实信号名的等价形式}, ...]
      首项=本行，之后按代入(DFS)顺序。GUI 测试项页 / HTML 报告显示用。
    表达式解析失败抛 expr.ExprError；cone 展开失败抛 cone.ConeError。
    """
    node = E.parse(sig.expr)
    bindings = resolver.resolve_signal_inputs(sig)
    if cone.find_internal_inputs(node, bindings):
        node, bindings = cone.expand(sig, wb, resolver, chain_out=chain_out)
        return node, bindings, True
    return node, bindings, False


def build(wb, opts):
    """
    返回 dict:
      blocks: list[(lines, stats)]
      selected / skipped / errors: 诊断
      stats: 汇总
    """
    resolver = R.Resolver(wb, force_overrides=opts.force_overrides,
                          rfwrite_overrides=opts.rfwrite_overrides,
                          default_kind=opts.default_kind,
                          wire_fallback=opts.wire_fallback,
                          wire_prefixes=opts.probe_prefixes,
                          cascade_mode=opts.cascade_mode)
    selected = select_signals(wb, opts)
    blocks = []
    errors = []
    skipped = []        # 含不可驱动输入(wire兜底/未解析)的信号，默认跳过(与 VBA 一致)
    mux_warnings = []   # 照常生成但有提示的 mux 组（如 top_out=0 用裸名探针，可能要前缀）
    n_total_vectors = 0
    n_total_neg = 0
    n_total_designer = 0     # designer 手填期望的用例数（其余正向用 auto_out 兜底）
    n_unresolved_signals = 0
    seen_labels = {}    # assert 标号 -> 首个出现的信号；查全局重复(重复=非法 SV，elaboration 失败)
    dup_labels = []

    for sig in selected:
        try:
            node, bindings, expanded = expand_signal(wb, resolver, sig)
        except E.ExprError as ex:
            errors.append((sig.out_name, sig.assert_id, "表达式解析失败: %s" % ex))
            continue
        except cone.ConeError as ex:
            errors.append((sig.out_name, sig.assert_id, "cone 展开失败: %s" % ex))
            continue

        override = (opts.vector_overrides.get(sig.out_name.lower())
                    if opts.vector_overrides else None)

        # 默认跳过含"不可驱动输入"的信号：wire兜底(表里查无,force 不存在的 net→CUVUNF) 或 未解析。
        # 这与 VBA 行为一致(它直接跳过这类信号)。--include-risky 可强制生成。
        # 但用户已显式定制(override)的信号尊重其选择，不跳过(可能正是为了验那类信号而手造的)。
        if override is None and not opts.include_risky:
            risky = []
            for ltr in E.collect_vars(node):
                b = bindings.get(ltr)
                if b is None:
                    continue
                if not b.resolved:
                    risky.append((ltr, b.base, "未解析"))
                elif b.found_in == "wire":
                    risky.append((ltr, b.base, "wire兜底(表里查无,非可驱动 net)"))
                elif b.found_in == "needs-prefix":
                    # 上游 logic 计算出来的 _to_logic 网，在 sig_logic 模块内部，
                    # 没有探针前缀 force 必然 CUVUNF → 跳过并给原因，保证产物能 elaborate
                    risky.append((ltr, b.base,
                                  "需要探针前缀: %s 是上游 logic 计算网(在 sig_logic 模块内)，"
                                  "跑 scan_rtl 或手工配置前缀后才能生成" % b.wire))
                elif b.found_in == "mux-output":
                    # 输入是 mux 组的输出（mux→logic 级联，WL 形态）：force 衔接网需要前缀
                    risky.append((ltr, b.base,
                                  "需要探针前缀: %s 是上游 mux 组输出的衔接网(在子模块内)，"
                                  "跑 scan_rtl 或手工配置前缀后才能生成" % b.wire))
            if risky:
                skipped.append((sig.out_name, sig.assert_id, risky))
                continue

        if override is not None:
            # 用户在 GUI 定制的测试项：照单全收(内联负向已编码在向量内)。
            # 若该信号又在 neg_signals/neg_all 里(左侧表勾了负向)，对其中的正向行再补一组
            # 自动负向——否则用户的负向勾选会被静默忽略。
            vecs = list(override)
            if _neg_enabled_for(sig, opts):
                base_pos = [v for v in vecs if not v.is_negative]
                if base_pos:
                    appended = V.add_negatives(base_pos, mode=opts.neg_mode,
                                               which=opts.neg_which, fixed_value=opts.neg_value)
                    vecs = vecs + appended[len(base_pos):]
            for i, v in enumerate(vecs):
                v.index = i
            meta = {"control": [], "data": [], "truncated": False}
        else:
            try:
                vecs, meta = V.generate_vectors(
                    node, bindings, sig.out_width,
                    mode=opts.mode, max_tests=opts.max_tests, exhaustive=opts.exhaustive)
            except E.ExprError as ex:
                errors.append((sig.out_name, sig.assert_id, "向量生成失败: %s" % ex))
                continue

            if _neg_enabled_for(sig, opts):
                vecs = V.add_negatives(vecs, mode=opts.neg_mode, which=opts.neg_which,
                                       fixed_value=opts.neg_value)
                for i, v in enumerate(vecs):   # 负向追加后按顺序重排 T 编号，标号不重复
                    v.index = i

        # 真·仅负向：只保留负向向量(编号不重排，与"全部"导出的 T 编号一致便于对照)；
        # 没有负向的信号整个不出现在产物里
        if opts.negative_vectors_only:
            vecs = [v for v in vecs if v.is_negative]
            if not vecs:
                continue

        # 全局 assert 标号唯一性检查：标号 = <R>_<test_label>，重复(同信号自定义名撞自动名、
        # 或两信号共用同一 R)在 SV 同一作用域里非法，会 elaboration 失败 → 收集并上报，不静默。
        aid = sig.assert_id or "X"
        for v in vecs:
            lbl = "%s_%s" % (aid, W.test_label(v))
            if lbl in seen_labels:
                dup_labels.append((lbl, seen_labels[lbl], sig.out_name))
            else:
                seen_labels[lbl] = sig.out_name

        lines, stats = W.render_signal_block(sig, bindings, vecs, meta,
                                             comments=opts.comments, node=node,
                                             probe_prefix=probe_prefix_for(sig, opts),
                                             owner_in_msg=opts.owner_in_msg,
                                             counters=opts.sv_summary)
        stats["cone_expanded"] = expanded
        blocks.append((lines, stats))
        n_total_vectors += stats["n_vectors"]
        n_total_neg += stats["n_negative"]
        n_total_designer += stats.get("n_designer", 0)
        if stats["unresolved"]:
            n_unresolved_signals += 1

    # ───────────── mux 页（2026-06-03 第九轮：mux 验证，与 logic 块同文件混排）─────────────
    n_logic_blocks = len(blocks)
    mux_selected = select_mux_groups(wb, opts)
    for grp in mux_selected:
        exp = mux_gen.expand_mux_group(wb, resolver, grp)
        # 有解析问题 → 跳过并给原因（与 logic risky-skip 同理念：保证产物能 elaborate、跳过必有名字+原因）
        if exp["issues"] and not opts.include_risky:
            skipped.append((grp.out_name, grp.assert_id,
                            [("mux", _mux_ctrl_desc(grp), "; ".join(exp["issues"]))]))
            continue

        # force 阻断：要 force 子模块内部网（级联衔接网 / wire 兜底）但没配前缀 → 跳过给原因
        # （这类没前缀生成必 CUVUNF，有实证依据；GUI 真值表照常渲染，这只挡 .sv 产出）。
        # 注意：输出探针 top_out=0 不在此列——它照常生成裸名 + 警告（见下 out_warn）。
        blockers = mux_prefix_risks(grp, exp, opts)
        if blockers and not opts.include_risky:
            skipped.append((grp.out_name, grp.assert_id, blockers))
            continue

        # 覆盖度三档（2026-06-03 第十一轮）：精简=每case一值；全面=+x位展开+反码数据轮；
        # 穷举=+另一条控制路径全扫。映射统一走 mux_gen.coverage_mode（与 GUI/report 同口径）。
        mux_mode = mux_gen.coverage_mode(opts.mode, opts.exhaustive)
        vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode=mux_mode, max_tests=opts.max_tests)
        # ⭐ 互异值碰撞 = 选路不可验证（两条数据路同值 → 选错路也测不出 = 假绿）。
        # 这不是"可能 elaboration 失败"的风险而是"确定验证无效"，include_risky 也不放行。
        if meta.get("value_collision"):
            skipped.append((grp.out_name, grp.assert_id,
                            [("mux", _mux_ctrl_desc(grp),
                              "数据寄存器位宽装不下 %d 个 case 的互异值——选错路也测不出(假绿)，"
                              "需加宽数据字段或拆分 mux 组" % len(grp.cases))]))
            continue
        if not vecs:
            skipped.append((grp.out_name, grp.assert_id,
                            [("mux", _mux_ctrl_desc(grp),
                              "无法生成测试向量（控制信号没有可用的驱动路径）")]))
            continue

        # designer 手填期望（mux，第十一轮续）：按输入取值键对号入座。须在反例之前——
        # make_negative 的错值防撞要看到 designer_expected。
        apply_mux_expected(vecs, opts.mux_expected.get(grp.out_name.lower())
                           or opts.mux_expected.get(grp.out_base.lower()))

        # 反例（自检式==，错值=被选中寄存器值取反——用户拍板与 logic 一致）
        if _neg_enabled_for(grp, opts):
            vecs = V.add_negatives(vecs, mode=opts.neg_mode, which=opts.neg_which,
                                   fixed_value=opts.neg_value)
            for i, v in enumerate(vecs):
                v.index = i
        if opts.negative_vectors_only:
            vecs = [v for v in vecs if v.is_negative]
            if not vecs:
                continue

        # 全局 assert 标号唯一性：assert_mux<N>_T<n> 也纳入同一张查重表（与 logic 跨表查重）
        aid = grp.assert_id
        for v in vecs:
            lbl = "%s_%s" % (aid, W.test_label(v))
            if lbl in seen_labels:
                dup_labels.append((lbl, seen_labels[lbl], grp.out_name))
            else:
                seen_labels[lbl] = grp.out_name

        lines, stats = W.render_signal_block(grp, exp["bindings"], vecs, meta,
                                             comments=opts.comments,
                                             probe_prefix=probe_prefix_for(grp, opts),
                                             owner_in_msg=opts.owner_in_msg,
                                             counters=opts.sv_summary,
                                             used_vars=exp["used_vars"])
        # top_out=0 且没配前缀：照常生成裸名探针，但在块顶留一句警告 + 汇总到 mux_warnings
        out_warn = mux_output_warning(grp, opts)
        if out_warn:
            lines = ["// ⚠ %s" % out_warn] + lines
            mux_warnings.append((grp.out_name, grp.assert_id, out_warn))
        stats["is_mux"] = True
        stats["scan_path"] = meta.get("scan_path")
        blocks.append((lines, stats))
        n_total_vectors += stats["n_vectors"]
        n_total_neg += stats["n_negative"]
        n_total_designer += stats.get("n_designer", 0)
        if stats["unresolved"]:
            n_unresolved_signals += 1

    summary = {
        "n_logic_rows": len(wb.logic),
        "n_selected": len(selected),
        "n_generated": len(blocks),
        "n_skipped": len(skipped),
        "n_vectors": n_total_vectors,
        "n_negative": n_total_neg,
        # designer 手填期望的用例数；正向用例中其余的 = auto_out 兜底(未经 designer 人工审核)
        "n_designer": n_total_designer,
        "n_parse_errors": len(errors),
        "n_unresolved_signals": n_unresolved_signals,
        "n_dup_labels": len(dup_labels),
        "tmm_fields": len(wb.tmm),
        "regmap_signals": len(wb.regmap),
        # mux 统计（2026-06-03 第九轮）
        "n_mux_groups": len(wb.mux),
        "n_mux_selected": len(mux_selected),
        "n_mux_generated": len(blocks) - n_logic_blocks,
        "n_mux_warnings": len(mux_warnings),
    }
    return {"blocks": blocks, "selected": selected, "errors": errors,
            "skipped": skipped, "mux_warnings": mux_warnings,
            "dup_labels": dup_labels, "summary": summary,
            # 计数器++已按 opts.sv_summary 写进 blocks，render 必须配套包裹声明/汇总，
            # 否则产物里是未声明变量 → 把标志带在结果里保证两者一致。
            "sv_summary": opts.sv_summary}


def render(result, header_info=None, comments=False, block_suffix=""):
    """block_suffix: 汇总命名块的后缀("_pos"/"_neg")——『仅正向』『仅负向』产物各取一个，
    两份贴进同一作用域时块名不重名(同名兄弟命名块 = elaboration 错误)。"""
    return W.render_file(result["blocks"], header_info=header_info, comments=comments,
                         summary=result.get("sv_summary", False),
                         block_suffix=block_suffix)


def analyze_signal(resolver, sig, wb=None, probe_prefix=""):
    """单信号解析画像（GUI debug 用）：返回 status + 每输入的 force/RF_WRITE net + 输出 net。
    status: clean / wire-fallback / unresolved / parse-err —— 用于挑出可能导致 elaboration 失败的信号。
    传入 wb 时对内部信号输入做 cone 展开（展开成功 → 按叶子寄存器评估状态，cone=True）。
    probe_prefix: 探针层级前缀（输出网在 ENV_RF 子模块里时），显示在 out_net。
    """
    rtl = getattr(sig, "rtl_name", sig.out_name)
    if probe_prefix:
        rtl = "%s.%s" % (probe_prefix.strip("."), rtl)
    out_net = "`%s.%s" % (W.ENV, rtl)
    expanded = False
    try:
        if wb is not None:
            node, bindings, expanded = expand_signal(wb, resolver, sig)
        else:
            node = E.parse(sig.expr)
            bindings = resolver.resolve_signal_inputs(sig)
    except E.ExprError as ex:
        return {"status": "parse-err", "inputs": [], "out_net": out_net, "error": str(ex),
                "cone": False}
    except cone.ConeError as ex:
        return {"status": "unresolved", "inputs": [], "out_net": out_net,
                "error": "cone 展开失败: %s" % ex, "cone": True}
    used = E.collect_vars(node)
    rows, status = [], "clean"
    for ltr in used:
        b = bindings.get(ltr)
        if b is None:
            continue
        if b.kind == "RW" and b.address is not None:
            net = "`%s(10'h%X, ...) bit<<%s" % (W.RF_WRITE, b.address, b.reg_lsb)
        elif b.kind == "RO":
            net = "force `%s.%s" % (W.ENV, b.wire_lhs)
        else:
            net = "(UNRESOLVED)"
        rows.append({"letter": ltr, "base": b.base, "kind": b.kind,
                     "found_in": b.found_in, "net": net, "resolved": b.resolved,
                     "note": b.note})
        if not b.resolved:
            status = "unresolved"
        elif b.found_in in ("wire", "needs-prefix") and status == "clean":
            status = "wire-fallback"
    return {"status": status, "inputs": rows, "out_net": out_net, "error": "", "cone": expanded}


def _fmt_cell(val, width):
    """报告真值表单元格取值显示：1 位→0/1；多位→0xN（与 GUI 编辑器一致）。"""
    if width and width <= 1:
        return str(val & 1)
    return "0x%X" % val


def _exp_src(vec):
    """期望来源（报告明细列）：designer 手填 / auto_out 兜底 / 负向(故意填错)。"""
    if vec.is_negative:
        return "负向(故意填错)"
    if vec.designer_filled:
        return "designer手填"
    return "auto_out兜底"


def report(wb, opts):
    """
    生成"给人看"的测试用例清单（结构化），CLI 负责写成 CSV/HTML。
    返回 {
      "summary": [每信号一行],
      "detail":  [每条用例一行]（每行带 T编号/_NEG/期望/force/...，便于 Ctrl+F），
      "tables":  [每信号一个纵向真值表]（输入带位宽做行、各测试做列，供 HTML ② 段）,
    }
    """
    resolver = R.Resolver(wb, force_overrides=opts.force_overrides,
                          rfwrite_overrides=opts.rfwrite_overrides,
                          default_kind=opts.default_kind,
                          wire_fallback=opts.wire_fallback,
                          wire_prefixes=opts.probe_prefixes,
                          cascade_mode=opts.cascade_mode)
    sigs = select_signals(wb, opts)
    summary, detail, tables = [], [], []
    for sig in sigs:
        chain = []        # cone 信号的展开链(本行+逐层代入的上游行)，HTML 真值表上方显示
        try:
            node, bindings, _expanded = expand_signal(wb, resolver, sig, chain_out=chain)
        except (E.ExprError, cone.ConeError) as ex:
            summary.append({"R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
                            "type": sig.suffix, "top": sig.top_output, "expr": sig.expr,
                            "n_tests": 0, "n_neg": 0, "control": "", "data": "",
                            "unresolved": "", "error": "表达式解析/展开失败: %s" % ex})
            continue
        used = E.collect_vars(node)
        # 与 build() 对齐：有 GUI 定制 override 就用 override，报告才不会与产出的 .sv 不符。
        override = (opts.vector_overrides.get(sig.out_name.lower())
                    if opts.vector_overrides else None)
        if override is not None:
            vecs = list(override)
            for i, v in enumerate(vecs):
                v.index = i
            meta = {"control": [], "data": []}
        else:
            try:
                vecs, meta = V.generate_vectors(node, bindings, sig.out_width,
                                                mode=opts.mode, max_tests=opts.max_tests,
                                                exhaustive=opts.exhaustive)
            except E.ExprError as ex:
                summary.append({"R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
                                "type": sig.suffix, "top": sig.top_output, "expr": sig.expr,
                                "n_tests": 0, "n_neg": 0, "control": "", "data": "",
                                "unresolved": "", "error": "向量生成失败: %s" % ex})
                continue
            if _neg_enabled_for(sig, opts):
                vecs = V.add_negatives(vecs, mode=opts.neg_mode, which=opts.neg_which,
                                       fixed_value=opts.neg_value)
        groups = V.input_groups(node, bindings)
        table = {"R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
                 "type": sig.suffix, "expr": sig.expr,
                 # chain = cone 展开链：[{"out","expr","subst"},...]，非 cone 信号为空 list
                 "chain": chain,
                 # letters = 该输入的 Excel 来源坐标(普通信号=A/B/C…；cone 展开叶子=
                 # "上游行名.字母"如 pll_n1.A)，让报告里的真值表能对回表达式/Excel
                 "inputs": [{"label": g["label"],
                             "letters": ",".join(g.get("xl_letters") or g.get("letters") or [])}
                            for g in groups], "tests": []}
        unresolved_bases = set()
        for vec in vecs:
            forces, writes, unres = W.compute_drives(vec, bindings, used)
            for (ltr, base, note) in unres:
                unresolved_bases.add(base or ltr)
            force_str = "; ".join("%s=%s" % (f["wire"], f["hex"]) for f in forces)
            write_str = "; ".join("%s=%s" % (w["addr"], w["hex"]) for w in writes)
            bv = V.vector_to_base_values(vec, groups)
            table["tests"].append({
                "name": W.test_label(vec),
                "neg": vec.is_negative,
                "values": [_fmt_cell(bv.get(g["key"], 0), g["width"]) for g in groups],
                # auto_out = 表达式计算值；expected = 进 .sv 的对比值(designer 手填 > auto_out 兜底 > 负向错值)
                "auto_out": _fmt_cell(vec.exp_value, vec.exp_width),
                "expected": _fmt_cell(vec.asserted_value, vec.exp_width),
                "designer_filled": vec.designer_filled,
                "correct": _fmt_cell(vec.exp_value, vec.exp_width),
                # 数值/位宽（HTML「真值表检查」tab 的 JS 比对用）
                "auto_num": vec.exp_value, "width": vec.exp_width,
                "force": force_str, "rfwrite": write_str,
            })
            detail.append({
                "R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
                "type": sig.suffix, "expr": sig.expr,
                "test": W.test_label(vec),
                "neg": "是" if vec.is_negative else "",
                "auto_out": W.fmt_bin(vec.exp_value, vec.exp_width),
                "expected": W.fmt_bin(vec.asserted_value, vec.exp_width),
                "exp_src": _exp_src(vec),
                "correct": W.fmt_bin(vec.exp_value, vec.exp_width) if vec.is_negative else "",
                "force": force_str, "rfwrite": write_str,
                "note": (vec.note if vec.is_negative else
                         ("; ".join("%s:%s" % (b or l, n) for (l, b, n) in unres) if unres else "")),
            })
        out_w = sig.out_width or 1
        slice_txt = "[%d:0]" % (out_w - 1) if out_w > 1 else ""
        table["auto_label"] = "auto_out%s" % slice_txt
        table["exp_label"] = "期望(out)%s" % slice_txt
        tables.append(table)
        summary.append({
            "R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
            "type": sig.suffix, "top": sig.top_output, "expr": sig.expr,
            "n_tests": len(vecs), "n_neg": sum(1 for v in vecs if v.is_negative),
            "control": ",".join(meta.get("control", [])), "data": ",".join(meta.get("data", [])),
            "unresolved": ";".join(sorted(unresolved_bases)), "error": "",
        })

    # ───────────── mux 组（与 build() 双轨同步——报告里必须能看到 .sv 里的每个 mux 块）─────────────
    mux_groups = select_mux_groups(wb, opts)
    mux_verif_rows = []
    for grp in mux_groups:
        exp = mux_gen.expand_mux_group(wb, resolver, grp)
        expr_text = _mux_expr_text(grp)
        base_row = {"R": grp.assert_id, "signal": grp.out_name, "owner": grp.owner,
                    "type": "mux", "top": grp.top_output, "expr": expr_text}

        # 与 build() 同口径的跳过判定（报告里以 error 列呈现原因，不是消失）
        skip_reason = ""
        out_warn = mux_output_warning(grp, opts)   # top_out=0 裸名探针提示（不阻断，照常生成）
        vecs, meta = [], {}
        blockers = mux_prefix_risks(grp, exp, opts)
        if exp["issues"] and not opts.include_risky:
            skip_reason = "; ".join(exp["issues"])
        elif blockers and not opts.include_risky:
            skip_reason = "; ".join(r[2] for r in blockers)
        else:
            mux_mode = mux_gen.coverage_mode(opts.mode, opts.exhaustive)
            vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode=mux_mode,
                                                  max_tests=opts.max_tests)
            if meta.get("value_collision"):
                skip_reason = ("数据寄存器位宽装不下 %d 个 case 的互异值——选错路也测不出(假绿)"
                               % len(grp.cases))
                vecs = []
            elif not vecs:
                skip_reason = "无法生成测试向量（控制信号没有可用的驱动路径）"
            else:
                # designer 手填期望（与 build() 双轨同步——报告必须反映 .sv 真实断言值）
                apply_mux_expected(vecs, opts.mux_expected.get(grp.out_name.lower())
                                   or opts.mux_expected.get(grp.out_base.lower()))
                if _neg_enabled_for(grp, opts):
                    vecs = V.add_negatives(vecs, mode=opts.neg_mode, which=opts.neg_which,
                                           fixed_value=opts.neg_value)
                    for i, v in enumerate(vecs):
                        v.index = i

        # 警告只在【真生成】时显示（被跳过的组用 error 列说明，警告无意义）
        row_warn = out_warn if not skip_reason else ""
        summary.append(dict(base_row, n_tests=len(vecs),
                            n_neg=sum(1 for v in vecs if v.is_negative),
                            control=",".join(meta.get("control", []) if meta else []),
                            data=",".join(meta.get("data", []) if meta else []),
                            unresolved="", error=skip_reason, warning=row_warn))
        # 可验证性行：issues/碰撞 → unresolved；top_out=0 裸名 → bare-probe（生成了，建议配前缀）；
        # 其余 clean。（与 GUI analyze_mux_group 同口径）
        verif_status = "unresolved" if skip_reason else ("bare-probe" if row_warn else "clean")
        mux_verif_rows.append({
            "R": grp.assert_id, "signal": grp.out_name, "owner": grp.owner,
            "type": "mux", "top": grp.top_output,
            "status": verif_status,
            "detail": skip_reason or row_warn, "out_net": "`%s.%s" % (W.ENV, grp.rtl_name),
        })
        if skip_reason:
            continue

        # ── case 选择表（tables 段，kind='mux'）：行=控制+各数据寄存器，列=测试 T ──
        used = exp["used_vars"]
        inp_rows = []
        for key in used:
            b = exp["bindings"][key]
            label = b.base + ("[%d:0]" % (b.width - 1) if b.width > 1 else "")
            # 角色判断统一走 mux_gen.key_role（多控制 c1:/c2:、上游配方 m<N>.* 都认）
            tag = {"ctrl": "ctrl", "data": "data", "upstream": "上游mux"}[mux_gen.key_role(key)]
            inp_rows.append({"label": label, "letters": "%s(%s)" % (key, tag)})
        table = {"R": grp.assert_id, "signal": grp.out_name, "owner": grp.owner,
                 "type": "mux", "expr": expr_text, "kind": "mux",
                 "inputs": inp_rows, "tests": []}
        for vec in vecs:
            forces, writes, _unres = W.compute_drives(vec, exp["bindings"], used)
            table["tests"].append({
                "name": W.test_label(vec),
                "neg": vec.is_negative,
                "values": [_fmt_cell(vec.assignments.get(k, 0), exp["bindings"][k].width)
                           for k in used],
                "auto_out": _fmt_cell(vec.exp_value, vec.exp_width),
                "expected": _fmt_cell(vec.asserted_value, vec.exp_width),
                "designer_filled": vec.designer_filled,
                "correct": _fmt_cell(vec.exp_value, vec.exp_width),
                "auto_num": vec.exp_value, "width": vec.exp_width,
                "force": "; ".join("%s=%s" % (f["wire"], f["hex"]) for f in forces),
                "rfwrite": "; ".join("%s=%s" % (w["addr"], w["hex"]) for w in writes),
            })
            detail.append({
                "R": grp.assert_id, "signal": grp.out_name, "owner": grp.owner,
                "type": "mux", "expr": expr_text,
                "test": W.test_label(vec),
                "neg": "是" if vec.is_negative else "",
                "auto_out": W.fmt_bin(vec.exp_value, vec.exp_width),
                "expected": W.fmt_bin(vec.asserted_value, vec.exp_width),
                "exp_src": _exp_src(vec),
                "correct": W.fmt_bin(vec.exp_value, vec.exp_width) if vec.is_negative else "",
                "force": table["tests"][-1]["force"], "rfwrite": table["tests"][-1]["rfwrite"],
                "note": vec.note,
            })
        out_w = grp.out_width or 1
        slice_txt = "[%d:0]" % (out_w - 1) if out_w > 1 else ""
        table["auto_label"] = "auto_out%s" % slice_txt
        table["exp_label"] = "期望(out)%s" % slice_txt
        tables.append(table)

    # ── 可验证性（取代旧 GUI"覆盖诊断"按钮）：逐信号给健康度 + 风险输入说明 ──
    verif = {"counts": {"clean": 0, "wire-fallback": 0, "unresolved": 0, "parse-err": 0},
             "signals": []}
    for sig in sigs:
        a = analyze_signal(resolver, sig, wb=wb)
        st = a["status"]
        verif["counts"][st] = verif["counts"].get(st, 0) + 1
        risky = [i for i in a["inputs"]
                 if (not i["resolved"]) or i["found_in"] in ("wire", "needs-prefix", "mux-output")]
        risky_str = "; ".join(
            "%s=%s(%s)" % (i["letter"], i["base"],
                           "未解析" if not i["resolved"] else
                           ("需要探针前缀" if i["found_in"] in ("needs-prefix", "mux-output")
                            else "wire兜底"))
            for i in risky)
        verif["signals"].append({
            "R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
            "type": sig.suffix, "top": sig.top_output, "status": st,
            "detail": risky_str or a.get("error", ""), "out_net": a.get("out_net", ""),
        })
    # mux 组的可验证性（与上面 mux 段同口径）
    for row in mux_verif_rows:
        verif["counts"][row["status"]] = verif["counts"].get(row["status"], 0) + 1
        verif["signals"].append(row)
    return {"summary": summary, "detail": detail, "tables": tables, "verifiability": verif}


def _mux_expr_text(grp):
    """mux 组的人读"表达式"：case(控制){case值:输入; ...}（报告/GUI 的表达式列用）。"""
    return grp.expr


def analyze_mux_group(resolver, wb, grp, mode="min", probe_prefix="", opts=None):
    """mux 组的解析画像（GUI 状态列/明细面板用），返回与 analyze_signal 同形状的 dict。

    status:
      clean        —— 解析通、不缺任何前缀，照常生成
      unresolved   —— issues 或互异值碰撞 → 不可验证（红）
      needs-prefix —— 两种情况（都能在 GUI 看真值表）：
                       ① force 子模块内部网（级联衔接网/wire 兜底）缺前缀 → 默认【跳过】.sv（硬阻断）
                       ② 输出 top_out=0 缺前缀 → 照常【生成】裸名探针，仅【建议】配前缀（软提示）
                      detail 文案区分两者；GUI 橙色，但 ② 不挡生成。
    inputs: 控制驱动输入 + 数据寄存器，每项与 analyze_signal 的 inputs 行同形状。
    """
    rtl = grp.rtl_name
    if probe_prefix:
        rtl = "%s.%s" % (probe_prefix.strip("."), rtl)
    out_net = "`%s.%s" % (W.ENV, rtl)
    exp = mux_gen.expand_mux_group(wb, resolver, grp)
    rows = []
    for key in exp["used_vars"]:
        b = exp["bindings"][key]
        if b.kind == "RW" and b.address is not None:
            net = "`%s(10'h%X, ...) bit<<%s" % (W.RF_WRITE, b.address, b.reg_lsb)
        elif b.kind == "RO":
            net = "force `%s.%s" % (W.ENV, b.wire_lhs)
        else:
            net = "(UNRESOLVED)"
        rows.append({"letter": key, "base": b.base, "kind": b.kind,
                     "found_in": b.found_in, "net": net, "resolved": b.resolved,
                     "note": b.note})
    if exp["issues"]:
        return {"status": "unresolved", "inputs": rows, "out_net": out_net,
                "error": "; ".join(exp["issues"]), "cone": False}
    vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode=mode)
    if meta.get("value_collision"):
        return {"status": "unresolved", "inputs": rows, "out_net": out_net,
                "error": "数据寄存器位宽装不下 %d 个 case 的互异值——选路不可验证(假绿)" % len(grp.cases),
                "cone": False}
    if not vecs:
        return {"status": "unresolved", "inputs": rows, "out_net": out_net,
                "error": "无法生成测试向量（控制信号没有可用的驱动路径）", "cone": False}
    # 结构都解析通了。再看前缀：① force 子模块网缺前缀=硬阻断（默认跳过）；② 输出 top_out=0
    # 缺前缀=软提示（照常生成裸名）。两者都 needs-prefix，detail 区分；GUI 能据此着色+给文案。
    eff_opts = opts or GenOptions(probe_prefixes={
        grp.out_base.lower(): probe_prefix} if probe_prefix else {})
    blockers = mux_prefix_risks(grp, exp, eff_opts)
    if blockers:
        return {"status": "needs-prefix", "inputs": rows, "out_net": out_net,
                "error": "; ".join(r[2] for r in blockers), "blocking": True, "cone": False}
    out_warn = mux_output_warning(grp, eff_opts)
    if out_warn:
        # 软提示：照常生成裸名探针（不是阻断）——单独一档 'bare-probe'，GUI 用信息色而非告警色
        return {"status": "bare-probe", "inputs": rows, "out_net": out_net,
                "error": out_warn, "blocking": False, "cone": False}
    return {"status": "clean", "inputs": rows, "out_net": out_net, "error": "", "cone": False}


def diagnose(wb, opts=None):
    """
    覆盖诊断：在真表上实测"我们到底把哪些输入解析成了 force(RO)/RF_WRITE(RW)/未知"，
    以及类型列里到底有哪些写法、有没有 >16bit 的输入(force/RF_WRITE 固定 16'h 会截断)。
    用于回答"force/RF_WRITE 之外是否还有别的类型、是否验证到位"。
    返回 dict（CLI 负责打印）。
    """
    opts = opts or GenOptions()
    resolver = R.Resolver(wb, force_overrides=opts.force_overrides,
                          rfwrite_overrides=opts.rfwrite_overrides,
                          default_kind=opts.default_kind,
                          wire_fallback=opts.wire_fallback,
                          wire_prefixes=opts.probe_prefixes,
                          cascade_mode=opts.cascade_mode)
    sigs = select_signals(wb, opts)

    # 1) 类型列原文分布（tmm H / regmap F）
    tmm_types = {}
    for f in wb.tmm.values():
        key = f.reg_type_raw or "(空)"
        tmm_types[key] = tmm_types.get(key, 0) + 1
    rm_types = {}
    for e in wb.regmap.values():
        key = (e.reg_type or "(空)")
        rm_types[key] = rm_types.get(key, 0) + 1
    tmm_pins = {}
    for f in wb.tmm.values():
        key = f.dig_top_pin if f.dig_top_pin is not None else "(空/其它)"
        tmm_pins[key] = tmm_pins.get(key, 0) + 1

    # 2) 逐输入解析分类（force 再按来源细分）
    cats = {"rfwrite": 0, "force_ro": 0, "force_chained": 0, "force_wire": 0, "unknown": 0}
    wide_inputs = []        # >16bit 的输入（force 会自适应位宽；RF_WRITE 仍 16'h 受限）
    unknown = []            # (信号, 字母, 基名, note)
    fallback_wires = []     # 表里查无、按 wire force 的（需你确认是否真是 wire）
    seen_inputs = set()
    for sig in sigs:
        bindings = resolver.resolve_signal_inputs(sig)
        try:
            used = E.collect_vars(E.parse(sig.expr))
        except E.ExprError:
            used = list(bindings.keys())
        for ltr in used:
            b = bindings.get(ltr)
            if b is None:
                continue
            key = (sig.out_name, ltr)
            if key in seen_inputs:
                continue
            seen_inputs.add(key)
            if b.kind == "RW" and b.address is not None:
                cats["rfwrite"] += 1
            elif b.kind == "RO" and b.found_in in ("tmm", "regmap"):
                cats["force_ro"] += 1
            elif b.kind == "RO" and b.found_in in ("logic", "self-input"):
                cats["force_chained"] += 1
            elif b.kind == "RO" and b.found_in in ("wire", "needs-prefix", "prefixed-wire"):
                cats["force_wire"] += 1
                if b.found_in != "prefixed-wire":    # 已配前缀确认存在的不算"需你确认"
                    fallback_wires.append((sig.out_name, ltr, b.base, b.width))
            else:
                cats["unknown"] += 1
                unknown.append((sig.out_name, ltr, b.base, b.note))
            if b.width > 16:
                wide_inputs.append((sig.out_name, ltr, b.base, b.width, b.kind, b.found_in))

    return {
        "n_signals": len(sigs),
        "tmm_type_raw": dict(sorted(tmm_types.items(), key=lambda kv: -kv[1])),
        "regmap_type_raw": dict(sorted(rm_types.items(), key=lambda kv: -kv[1])),
        "tmm_dig_top_pin": dict(sorted(tmm_pins.items(), key=lambda kv: -kv[1])),
        "cats": cats,
        "wide_inputs": wide_inputs,
        "fallback_wires": fallback_wires,
        "unknown": unknown,
    }
