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
                 cascade_mode="cone", gen_mux=True, mux_expected=None, mux_data=None,
                 dft_observe=False, logic_mode=None, mux_mode=None, sig_cov=None):
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
        # {mux信号名(小写): {物理基名(小写): int}} —— B2 用户手填的 mux 数据值（第二十轮）。
        # 替换自动互异/标记值；撞值非阻断警告(meta['override_collision'])。按物理基名键
        # （与 by_base/点名法同口径——覆盖度切换/case 重排都稳）。
        self.mux_data = {str(k).lower(): {str(bk).lower(): int(bv) for bk, bv in v.items()}
                         for k, v in (mux_data or {}).items() if v}
        # DFT 观测模式（第二十轮续②）：被验证输出走 _to_dft 且被 iddq 门控时，在产物开头 force 门到
        # 透传值(iddq=0)，使我们断言的基名网反映功能值——= for_test 的做法。默认关（直探内部网为主）。
        self.dft_observe = bool(dft_observe)
        # 覆盖档位 logic/mux 解耦（第二十二轮，用户拍板互不绑定）：logic_mode/mux_mode ∈
        # {min,max,exhaustive}。未显式传(=None)则该侧回退到 (mode, exhaustive) 合成的档位——
        # 所有旧调用方(CLI/GUI/436 测试)不传新参时产物逐字节不变。读取统一走
        # logic_vec_params()/mux_cov_mode()，build()/report()/GUI 同口径。
        self.logic_mode = logic_mode if logic_mode in ("min", "max", "exhaustive") else None
        self.mux_mode = mux_mode if mux_mode in ("min", "max", "exhaustive") else None
        # 单点覆盖度（per-signal，用户拍板「既要全局也要单点」）：{信号名(小写): 档位}，
        # 档位 ∈ {min,max,exhaustive}。某信号在此 → 该信号的覆盖档以此为准、压过全局
        # logic_mode/mux_mode；不在此 → 跟随全局（行为与旧版逐字节一致）。logic 与 mux
        # 共用一张表（一个信号只属一类），build/report/GUI 经 logic_vec_params(name)/
        # mux_cov_mode(name) 同口径读取，name 缺省则纯走全局（保旧调用方不变）。
        self.sig_cov = {str(k).lower(): v for k, v in (sig_cov or {}).items()
                        if v in ("min", "max", "exhaustive")}

    def logic_vec_params(self, name=None):
        """logic 向量生成参数 (mode, exhaustive)，供 V.generate_vectors。
        name 命中单点覆盖 → 用单点档；否则未解耦(logic_mode=None)时 = 原 (self.mode,
        self.exhaustive)，逐字节不变。"""
        ov = self.sig_cov.get(name.lower()) if name else None
        if ov:
            return _decompose_cov(ov)
        if self.logic_mode is None:
            return self.mode, self.exhaustive
        return _decompose_cov(self.logic_mode)

    def mux_cov_mode(self, name=None):
        """mux 覆盖档 {min,max,exhaustive}，供 mux_gen.make_mux_vectors。
        name 命中单点覆盖 → 用单点档；否则未解耦(mux_mode=None)时 =
        coverage_mode(self.mode, self.exhaustive)，与旧行为一致。"""
        ov = self.sig_cov.get(name.lower()) if name else None
        if ov:
            return ov
        if self.mux_mode is None:
            return mux_gen.coverage_mode(self.mode, self.exhaustive)
        return self.mux_mode


def _decompose_cov(collapsed):
    """覆盖档 {min,max,exhaustive} → generate_vectors 的 (mode, exhaustive)。
    与 mux_gen.coverage_mode 互逆：min→(min,F)、max→(max,F)、exhaustive→(max,T)
    （穷举意图下若总位数超 cap，由 vectors 自动退化为'全面'，故底模式取 max）。"""
    if collapsed == "exhaustive":
        return "max", True
    if collapsed == "max":
        return "max", False
    return "min", False


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


def _logic_passes_user_filters(sig, opts, rx, exrx):
    """除 top_output_only 外的用户筛选（owner/名称/正则/排除/类型）是否全通过。"""
    if opts.owners is not None and _ws(sig.owner) not in opts.owners:
        return False
    if not _name_matches(sig, opts.signals):
        return False
    if rx and not (rx.search(sig.out_name) or rx.search(sig.out_base)):
        return False
    # 排除：按名集合 或 正则（匹配 K 全名或去位宽基名）
    if opts.exclude is not None and (sig.out_name.lower() in opts.exclude
                                     or sig.out_base.lower() in opts.exclude):
        return False
    if exrx and (exrx.search(sig.out_name) or exrx.search(sig.out_base)):
        return False
    if opts.types is not None and sig.suffix.lower() not in opts.types:
        return False
    return True


def select_signals(wb, opts):
    """按 owner / 名称 / 正则 / top_output / 类型 过滤 logic 信号；支持排除。"""
    import re
    rx = re.compile(opts.signal_regex, re.I) if opts.signal_regex else None
    exrx = re.compile(opts.exclude_regex, re.I) if opts.exclude_regex else None
    out = []
    for sig in wb.logic:
        if not _logic_passes_user_filters(sig, opts, rx, exrx):
            continue
        if opts.top_output_only and not is_top_output(sig.top_output):
            continue
        out.append(sig)
    return out


def filtered_internal_signals(wb, opts):
    """被 top_output_only 默认【静默】过滤掉的 logic 内部节点（top_output=0 但通过了其它所有筛选）。

    这是工具里唯一'默认生效却不进跳过清单'的过滤——单独拎出来，让摘要/账目能把它们也亮出来
    （logic 内部节点不是 RTL 端口、ENV_RF 探不到，所以默认不验；--include-internal 可纳入）。
    """
    if not opts.top_output_only:
        return []
    import re
    rx = re.compile(opts.signal_regex, re.I) if opts.signal_regex else None
    exrx = re.compile(opts.exclude_regex, re.I) if opts.exclude_regex else None
    return [sig for sig in wb.logic
            if _logic_passes_user_filters(sig, opts, rx, exrx)
            and not is_top_output(sig.top_output)]


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


def mux_data_for(opts, grp):
    """该 mux 组的用户手填数据值覆写 {物理基名(小写): int}，没有则 None（按信号名/基名两套键查）。"""
    return (opts.mux_data.get(grp.out_name.lower())
            or opts.mux_data.get(grp.out_base.lower()) or None)


def dft_force_preamble(wb, resolver, gen_bases, opts):
    """DFT 观测模式产物前导：被生成的输出里凡在 dft 页被门控的，把其门(iddq)force 到透传值。

    门通常全表统一(iddq_mode)，按门去重只 force 一次。透传后我们断言的基名网=功能值（= for_test）。
    门是 RO 线 → force；RW → 提示 RF_WRITE；无法解析 → 留 ⚠ 注释（用户手动设）。返回 (lines, warnings)。
    """
    if not opts.dft_observe or not getattr(wb, "dft", None):
        return [], []
    gates = {}      # gate_base_low -> (gate_raw, transparent)
    for ob in gen_bases:
        g = wb.dft.get(ob)
        if g:
            gates.setdefault(g["gate_base"], (g["gate_raw"], g["transparent"]))
    if not gates:
        return [], []
    lines = ["// ⚙ DFT 观测模式：force DFT 门到透传值，"
             "使被门控的 _to_dft 输出反映功能值（= for_test 的 iddq=0）"]
    warns = []
    for gb, (graw, transp) in sorted(gates.items()):
        info = {"raw": gb, "base": gb, "width": 1, "msb": None, "lsb": None}
        b = resolver.resolve("dft_gate_" + gb, info)
        if b.resolved and b.kind == "RO":
            lines.append("force `%s.%s = 1'b%d;" % (W.ENV, b.wire_lhs, transp))
        elif b.resolved and b.kind == "RW" and b.address is not None:
            lines.append("// ⚠ DFT 门 %s 是 RW(addr 0x%X)，请用 RF_WRITE 设字段=%d"
                         % (gb, b.address, transp))
            warns.append((gb, "DFT 门是 RW，需 RF_WRITE 设=%d" % transp))
        else:
            lines.append("// ⚠ DFT 门 %s 无法解析为可 force 的网，请手动设 =%d（%s）"
                         % (gb, transp, b.note or ""))
            warns.append((gb, "DFT 门无法解析，需手动设 =%d" % transp))
    return lines, warns


def _append_dft_vectors(out_base, vecs, wb, resolver, side_mode, dft_observe=False):
    """item③ iddq DFT 态拍（第二十二轮）：被 dft 页门控的输出，在功能向量外追加一条 DFT 态拍——
    force 门(iddq)到选中【常量支】的值、断言输出=该常量(0)，该拍后还原门态（S4：否则 force 的
    iddq=1 会钉死后续所有拍/块的门）。

    side_mode ∈ {min,max,exhaustive}：精简(min)不补，保三档区别（精简对'iddq 门坏死'是假绿，由
    报告/GUI 据 meta['iddq_skipped'] 标注，见 §3.5）。原地追加进 vecs（T 编号由调用方统一重排）。
    dft_observe：开时全局前导已 force 门=透传，本拍不能 release(会连带抹掉前导→污染后续被门控块=评审
    blocker)，须 force 回透传值恢复前导态；关时无前导，release 让门回 RTL 默认(透传)即可。
    返回 None（正常补 / 该输出无 dft 门 → 无需补）或 str（被门控但补不了的原因，供 meta['iddq_skipped']）。
    """
    if side_mode == "min":
        return None                                   # 精简档不补 iddq 拍（保三档区别）
    g = wb.dft.get(out_base) if getattr(wb, "dft", None) else None
    if not g:
        return None                                   # 该输出不被 dft 门控 → 无需 DFT 拍
    # 门网解析（与 dft_force_preamble 同口径）：必须是可 force 的 RO 网
    info = {"raw": g["gate_base"], "base": g["gate_base"], "width": 1, "msb": None, "lsb": None}
    b = resolver.resolve("dft_gate_" + g["gate_base"], info)
    if not (b.resolved and b.kind == "RO"):
        return "iddq 门 %s 非可 force 的 RO 网，未补 DFT 拍（%s）" % (g["gate_base"], b.note or "")
    # 模板：首条功能=1(exp_value!=0) 的正向向量，克隆其输入驱动（点名法窄字段下 marker 非零即可当模板）
    tmpl = next((v for v in vecs if not v.is_negative and v.exp_value != 0), None)
    if tmpl is None:
        return "找不到 exp_value!=0 的功能向量作模板，未补 DFT 拍"
    # 门=1 选中【常量支】(输出=0)：标准 iddq 门 `B?0:A`(transparent=0) → 门值=1；期望取该字面常量 0
    # （不是"透传取反"——S1：read_dft 已把功能透传值算进 exp，DFT 拍要的是非透传支的常量 0）。
    transp = int(g["transparent"])
    gate_val = 1 - transp
    dv = V.TestVector(len(vecs), dict(tmpl.assignments), 0, tmpl.exp_width,
                      designer_expected=0,
                      note="IDDQ 漏电态：门=1 应把输出压到常量支(0)（dft 页 %s）" % g["gate_raw"])
    dv.dft_pitch = True
    dv.extra_forces = [(b.wire_lhs, gate_val, 1)]     # 额外 force iddq 门=常量支值
    if dft_observe:
        dv.restore_forces = [(b.wire_lhs, transp, 1)]  # 还原全局前导态（透传），不 release（评审 blocker）
    else:
        dv.release_nets = [b.wire_lhs]                # 无前导：release 让门回 RTL 默认(透传)（S4）
    vecs.append(dv)
    return None


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
    """mux 组控制信号的人读描述（skipped 诊断用）：单控制=基名，多控制=拼接 {c1,c2}。
    带切片(lsb>0)的控制显出 [msb:lsb]（如 temp_code[3:1]）——非零偏移一眼可见。"""
    if len(grp.ctrls) > 1:
        return "{%s}" % ",".join(c.label for c in grp.ctrls)
    return grp.ctrls[0].label if grp.ctrls else grp.ctrl_base


def _empty_vector_reason(meta):
    """向量为空时给【真实】原因：优先 meta 记录的逐 case 丢弃原因（去重精简），否则才用通用兜底。

    向量为空有两类来源，旧版一律甩"控制信号没有可用的驱动路径"——当真因在数据侧（如同一寄存器
    喂多 case 被 by_base 冲突全丢）时这句话指错了地方。emit 已把每 case 丢弃原因记进
    meta['dropped_reasons']，这里把它去重后呈出来；只有 emit 从没跑过（控制来源 unknown / 无扫描
    路径，确实没驱动路径）时 dropped_reasons 为空，才落到通用兜底。
    """
    reasons = (meta or {}).get("dropped_reasons") or []
    seen, uniq = set(), []
    for r in reasons:
        core = r.split(": ", 1)[-1] if ": " in r else r   # 去掉 "case X: " 前缀再去重
        if core not in seen:
            seen.add(core)
            uniq.append(core)
    if uniq:
        head = "；".join(uniq[:3])
        more = "（…等 %d 类原因）" % len(uniq) if len(uniq) > 3 else ""
        return "所有测试向量都被丢弃：" + head + more
    return "无法生成测试向量（控制信号没有可用的驱动路径）"


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


def expand_signal(wb, resolver, sig, chain_out=None, fallback_notes=None):
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
    internal = cone.find_internal_inputs(node, bindings)
    if internal:
        try:
            node2, bindings2 = cone.expand(sig, wb, resolver, chain_out=chain_out)
            return node2, bindings2, True
        except cone.ConeError:
            # cone 成环/超深 → 回退为 force 基名（for_test 那招）：内部输入若基名在 tmm/regmap
            # 有真实寄存器（如 linectrl_band_sel 撞名 RO 寄存器 d61），改直接 force 顶层基名网，
            # 不再展开。兜不住（内部输入纯逻辑节点、顶层没有该网）→ 仍抛 ConeError 让上层报错。
            fb = _cone_force_fallback(resolver, sig, node, bindings, internal, fallback_notes)
            if fb is not None:
                if chain_out is not None:
                    del chain_out[:]      # cone 半途成环可能已写入残缺展开链 → 回退非 cone，清掉
                return node, fb, False
            raise
    return node, bindings, False


def _cone_force_fallback(resolver, sig, node, bindings, internal_letters, notes=None):
    """cone 失败兜底：把"基名是 tmm/regmap 真实寄存器"的内部输入临时按 force(RO) 基名重解析。

    成功（重解析后不再有无解的内部输入）→ 返回新 bindings；否则 None（兜不住，让上层报 cone 失败）。
    与 for_test 一致：force 顶层基名网（如 d_wl_rf_linectrl_band_sel），不碰撞名的 logic 输出。
    """
    forceable = []
    for ltr in internal_letters:
        b = bindings.get(ltr)
        if b is None:
            continue
        tmm, _ta = resolver._match_tmm(b.base)
        rm, _ra = resolver._match_regmap(b.base)
        if tmm is not None or rm is not None:        # 基名在表里有真寄存器 → 可 force 顶层基名
            forceable.append(b.base.lower())
    if not forceable:
        return None
    saved = resolver.force_overrides
    try:
        resolver.force_overrides = set(saved) | set(forceable)
        fb = resolver.resolve_signal_inputs(sig)
    finally:
        resolver.force_overrides = saved
    if cone.find_internal_inputs(node, fb):           # 还有兜不住的内部输入 → 不算成功
        return None
    for ltr in internal_letters:                      # 在 note 上留痕，GUI/报告可见
        b = fb.get(ltr)
        if b is not None and b.base.lower() in forceable:
            b.note = (b.note + " " if b.note else "") + "[cone成环→回退force基名]"
    if notes is not None:
        notes.append("%s: cone 成环，已回退为 force 基名（%s）"
                     % (sig.out_name, ", ".join(sorted(set(forceable)))))
    return fb


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
    filtered_internal = filtered_internal_signals(wb, opts)   # 默认静默滤掉的内部节点(可见性用)
    blocks = []
    errors = []
    skipped = []        # 含不可驱动输入(wire兜底/未解析)的信号，默认跳过(与 VBA 一致)
    spec_conflicts = []  # mux 规格冲突跳过(同 case 不同源)——单列, 账目/报告/GUI 据此区别于普通跳过
    mux_warnings = []   # 照常生成但有提示的 mux 组（如 top_out=0 用裸名探针，可能要前缀）
    cone_fallbacks = [] # cone 成环 → 回退 force 基名的信号（for_test 那招），可见性用
    n_total_vectors = 0
    n_total_neg = 0
    n_total_designer = 0     # designer 手填期望的用例数（其余正向用 auto_out 兜底）
    n_unresolved_signals = 0
    seen_labels = {}    # assert 标号 -> 首个出现的信号；查全局重复(重复=非法 SV，elaboration 失败)
    dup_labels = []

    for sig in selected:
        try:
            node, bindings, expanded = expand_signal(wb, resolver, sig,
                                                     fallback_notes=cone_fallbacks)
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
                _lmode, _lexh = opts.logic_vec_params(sig.out_name)
                vecs, meta = V.generate_vectors(
                    node, bindings, sig.out_width,
                    mode=_lmode, max_tests=opts.max_tests, exhaustive=_lexh)
            except E.ExprError as ex:
                errors.append((sig.out_name, sig.assert_id, "向量生成失败: %s" % ex))
                continue

            if _neg_enabled_for(sig, opts):
                vecs = V.add_negatives(vecs, mode=opts.neg_mode, which=opts.neg_which,
                                       fixed_value=opts.neg_value)
                for i, v in enumerate(vecs):   # 负向追加后按顺序重排 T 编号，标号不重复
                    v.index = i

        # item③ iddq DFT 态拍（第二十二轮）：被 dft 门控的 logic 输出补一条 DFT 拍（全面/穷举；精简不补）。
        # 仅自动向量路径补（override=用户全定制，不注入工具拍）；放在负向之后 → DFT 拍不被自动负向翻倍。
        if override is None:
            _dft_skip = _append_dft_vectors(sig.out_base.lower(), vecs, wb, resolver,
                                            mux_gen.coverage_mode(*opts.logic_vec_params(sig.out_name)),
                                            opts.dft_observe)
            if _dft_skip:
                meta["iddq_skipped"] = _dft_skip
            for i, v in enumerate(vecs):
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
        # 缺口可见（M2）：logic 输出被 dft 门控却补不上 DFT 拍 → 块顶留 ⚠（与 mux 路同口径，
        # 别让"少验一支 iddq"在 logic 侧无声无息；render_signal_block 不读 meta 故在此补）。
        # 不进 mux_warnings（那是 mux 专用通道，CLI 文案会误标）；报告侧由 summary.warning 透出。
        if meta.get("iddq_skipped"):
            lines = ["// ⚠ %s" % meta["iddq_skipped"]] + lines
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
            if exp.get("spec_conflicts"):
                spec_conflicts.append({"name": grp.out_name, "aid": grp.assert_id,
                                       "owner": grp.owner or "",
                                       "conflicts": exp["spec_conflicts"],
                                       "reason": "; ".join(exp["issues"])})
            continue

        # force 阻断：要 force 子模块内部网（级联衔接网 / wire 兜底）但没配前缀 → 跳过给原因
        # （这类没前缀生成必 CUVUNF，有实证依据；GUI 真值表照常渲染，这只挡 .sv 产出）。
        # 注意：输出探针 top_out=0 不在此列——它照常生成裸名 + 警告（见下 out_warn）。
        blockers = mux_prefix_risks(grp, exp, opts)
        if blockers and not opts.include_risky:
            skipped.append((grp.out_name, grp.assert_id, blockers))
            continue

        # 覆盖度三档（2026-06-03 第十一轮）：精简=每case一值；全面=+x位展开+反码数据轮；
        # 穷举=+另一条控制路径全扫。第二十二轮起 mux 侧档位与 logic 解耦，走 opts.mux_cov_mode()。
        mux_mode = opts.mux_cov_mode(grp.out_name)
        vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode=mux_mode, max_tests=opts.max_tests,
                                              data_overrides=mux_data_for(opts, grp))
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
                            [("mux", _mux_ctrl_desc(grp), _empty_vector_reason(meta))]))
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

        # item③ iddq DFT 态拍（mux 被门控输出，如 mixer2g_en）：全面/穷举补、精简不补；放负向之后。
        _dft_skip = _append_dft_vectors(grp.out_base.lower(), vecs, wb, resolver,
                                        opts.mux_cov_mode(grp.out_name), opts.dft_observe)
        if _dft_skip:
            meta["iddq_skipped"] = _dft_skip
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
        # 缺口可见（M2，第二十二轮）：级联 alt 分支无法判别被跳过 / iddq DFT 拍补不上 → 透出，
        # 别让"少验一支"无声无息（与 cascade/iddq 覆盖的"缺口必须可见"原则一致）。
        for _k in ("cascade_alt_skipped", "iddq_skipped"):
            _gapmsg = meta.get(_k)
            if _gapmsg:
                lines = ["// ⚠ %s" % _gapmsg] + lines
                mux_warnings.append((grp.out_name, grp.assert_id, _gapmsg))
        # 嵌套 mux 自动折叠：.sv 块顶留一句 ⚙ 注释，让看 .sv 的人也能复核合并是否正确
        nnote = getattr(grp, "normalized_note", "")
        if nnote:
            lines = ["// ⚙ %s" % nnote] + lines
        # 死分支去重（A2）：靠后的重复 case 已跳过，块顶留一句 ⚙ 注释让看 .sv 的人知道少了哪些行
        snote = meta.get("shadowed_note")
        if snote:
            lines = ["// ⚙ %s" % snote] + lines
        # 手填数据值撞值（B2，非容量）：两条【手填】数据路取到相同值 → 选错路也测不出(假绿)。
        # 仅在确属手填值之间撞时报（容量太窄的撞已走点名法/跳过，不归咎手填）。
        if meta.get("override_collision"):
            ocoll = ("手填数据值有撞值：≥2 条数据路被你手填成相同值 → 选错路也测不出(假绿)，请核对手填值"
                     "（字段够宽、是手填值本身重复，工具未自动改）")
            lines = ["// ⚠ %s" % ocoll] + lines
            mux_warnings.append((grp.out_name, grp.assert_id, ocoll))
        # 手填值因字段太窄走了点名法保护而未生效（B2，#8）：照实说明，别让用户以为手填生效了
        if meta.get("override_ignored_marker"):
            omk = ("字段太窄(装不下互异值)，已用点名法保护(被测=标记/其余=0)——你手填的数据值在此模式下"
                   "不生效；要让手填值生效需加宽数据字段或拆分 mux 组")
            lines = ["// ⚠ %s" % omk] + lines
            mux_warnings.append((grp.out_name, grp.assert_id, omk))
        # 手填值超字段宽被截断（B2，#5）：导入/编程接口可能传超宽值，截断要让人看见
        if meta.get("override_truncated"):
            ot = "、".join("%s: 0x%X→0x%X" % (b, rm[0], rm[1])
                          for b, rm in sorted(meta["override_truncated"].items()))
            otmsg = "手填数据值超出字段位宽、已按字段截断（%s）" % ot
            lines = ["// ⚠ %s" % otmsg] + lines
            mux_warnings.append((grp.out_name, grp.assert_id, otmsg))
        # 规格矛盾（A2，#3）：被 include_risky 放行时 issues 不挡，块顶仍要留 ⚠ 让假红有据可查
        cnote = meta.get("contradiction_note") or exp.get("contradiction_note")
        if cnote:
            lines = ["// ⚠ %s" % cnote] + lines
            mux_warnings.append((grp.out_name, grp.assert_id, cnote))
        # mode=0 用裸名 force 线控网（第二十四轮）：testcase 已出，仅层级前缀待补——块顶留 ⚠，
        # 仿真 elaboration 若在该 force 报 CUVUNF，跑 scan_rtl 配 --probe-prefix 后重生成即为正确层级。
        abare = meta.get("cascade_alt_bare")
        if abare:
            amsg = ("mode=0 那半张表 force 线控网 %s 用的是裸名(层级前缀待补)——若仿真 CUVUNF，"
                    "跑 scan_rtl 配 --probe-prefix %s=<层级> 后重生成即为正确路径" % (abare, abare))
            lines = ["// ⚠ %s" % amsg] + lines
            mux_warnings.append((grp.out_name, grp.assert_id, amsg))
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
        # 默认静默过滤掉的 logic 内部节点（top_output=0）——拎出来给可见性
        "n_filtered_internal": len(filtered_internal),
    }
    # DFT 观测模式（第二十轮续②，默认关）：被生成的输出里凡在 dft 页被 iddq 门控的，
    # 在产物前导 force 门到透传值，使断言的基名网反映功能值（= for_test 的 iddq=0 做法）。
    gen_bases = {str(st.get("out_name", "")).split("[")[0].lower() for _l, st in blocks}
    dft_preamble, dft_warnings = dft_force_preamble(wb, resolver, gen_bases, opts)
    summary["n_dft_forced"] = sum(1 for x in dft_preamble if x.startswith("force"))
    return {"blocks": blocks, "selected": selected, "errors": errors,
            "skipped": skipped, "spec_conflicts": spec_conflicts,
            "mux_warnings": mux_warnings,
            "cone_fallbacks": cone_fallbacks,
            "filtered_internal": filtered_internal,
            "dft_preamble": dft_preamble, "dft_warnings": dft_warnings,
            "dup_labels": dup_labels, "summary": summary,
            # 计数器++已按 opts.sv_summary 写进 blocks，render 必须配套包裹声明/汇总，
            # 否则产物里是未声明变量 → 把标志带在结果里保证两者一致。
            "sv_summary": opts.sv_summary}


def compose_account(wb, opts, res):
    """完整账目：每个 logic 信号 + 每个 mux 组的去向，一个不漏。

    给"我不喜欢被跳过、怕有问题看不见"——把所有信号/组列一遍，标清 disposition：
      生成 / 生成(裸名探针) / 跳过(原因) / 错误(原因) / 过滤(原因)
    返回 [{kind, name, aid, disposition, reason}, ...]（logic 在前、mux 在后）。
    """
    gen_names = {st.get("out_name") for _l, st in res["blocks"]}
    bare = {n: w for n, _a, w in res.get("mux_warnings", [])}

    def _reason(reasons):
        if isinstance(reasons, list):
            return "; ".join(str(w) for *_h, w in reasons)
        return str(reasons)
    skipped_map = {name: _reason(reasons) for name, _aid, reasons in res.get("skipped", [])}
    error_map = {name: msg for name, _aid, msg in res.get("errors", [])}
    internal_names = {s.out_name for s in res.get("filtered_internal", [])}

    items = []
    for sig in wb.logic:
        n = sig.out_name
        if n in error_map:
            disp, reason = "错误", error_map[n]
        elif n in skipped_map:
            disp, reason = "跳过", skipped_map[n]
        elif n in gen_names:
            disp, reason = "生成", ""
        elif n in internal_names:
            disp, reason = "过滤", "logic 内部节点(top_output=0)，ENV_RF 探不到；--include-internal 可纳入"
        else:
            disp, reason = "过滤", "被 --owner/--signals/--exclude/--type 等筛选条件排除"
        items.append({"kind": "logic", "name": n, "aid": sig.assert_id,
                      "disposition": disp, "reason": reason})

    mux_selected_names = {g.out_name for g in select_mux_groups(wb, opts)}
    spec_conflict_names = {sc["name"] for sc in res.get("spec_conflicts", [])}
    for grp in wb.mux:
        n = grp.out_name
        if n in spec_conflict_names:
            disp, reason = "跳过·规格冲突", skipped_map.get(n, "")
        elif n in skipped_map:
            disp, reason = "跳过", skipped_map[n]
        elif n in gen_names and n in bare:
            disp, reason = "生成(裸名探针)", bare[n]
        elif n in gen_names:
            disp, reason = "生成", ""
        elif n not in mux_selected_names:
            disp, reason = "过滤", "被 --owner/--signals/--exclude/--type/--no-mux 排除"
        else:
            disp, reason = "跳过", "未生成（原因见诊断/报告）"
        items.append({"kind": "mux", "name": n, "aid": grp.assert_id,
                      "disposition": disp, "reason": reason})
    return items


def render(result, header_info=None, comments=False, block_suffix=""):
    """block_suffix: 汇总命名块的后缀("_pos"/"_neg")——『仅正向』『仅负向』产物各取一个，
    两份贴进同一作用域时块名不重名(同名兄弟命名块 = elaboration 错误)。"""
    return W.render_file(result["blocks"], header_info=header_info, comments=comments,
                         summary=result.get("sv_summary", False),
                         block_suffix=block_suffix,
                         preamble=result.get("dft_preamble") or None)


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
    """期望来源（报告明细列）：负向 / DFT门 / designer 手填 / auto_out 兜底。"""
    if vec.is_negative:
        return "负向(故意填错)"
    if getattr(vec, "dft_pitch", False):
        return "DFT门(iddq=1→压0)"          # item③：期望来自 dft 门常量支，非 designer 手填(M3)
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
                _lmode, _lexh = opts.logic_vec_params(sig.out_name)
                vecs, meta = V.generate_vectors(node, bindings, sig.out_width,
                                                mode=_lmode, max_tests=opts.max_tests,
                                                exhaustive=_lexh)
            except E.ExprError as ex:
                summary.append({"R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
                                "type": sig.suffix, "top": sig.top_output, "expr": sig.expr,
                                "n_tests": 0, "n_neg": 0, "control": "", "data": "",
                                "unresolved": "", "error": "向量生成失败: %s" % ex})
                continue
            if _neg_enabled_for(sig, opts):
                vecs = V.add_negatives(vecs, mode=opts.neg_mode, which=opts.neg_which,
                                       fixed_value=opts.neg_value)
            # item③ DFT 拍：报告与 .sv 双轨一致（同 build 的 logic 挂点；override 路径不注入）
            _lskip = _append_dft_vectors(sig.out_base.lower(), vecs, wb, resolver,
                                         mux_gen.coverage_mode(*opts.logic_vec_params(sig.out_name)),
                                         opts.dft_observe)
            if _lskip:
                meta["iddq_skipped"] = _lskip   # 缺口可见(M2)：报告 error 列透出（.sv 已有 // ⚠）
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
            # DFT 拍的 iddq 门 force 在 vec.extra_forces 里（不在 used_vars 绑定中）——报告 force 列
            # 也带上，否则报告与 .sv 不符（恰好漏掉区分 DFT 拍的那条门 force）。
            _fparts = ["%s=%s" % (f["wire"], f["hex"]) for f in forces]
            _fparts += ["%s=%s" % (wl, W.fmt_bin(wv, ww))
                        for (wl, wv, ww) in (getattr(vec, "extra_forces", None) or [])]
            force_str = "; ".join(_fparts)
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
            "unresolved": ";".join(sorted(unresolved_bases)),
            "error": ("⚠覆盖缺口: %s" % meta["iddq_skipped"]) if meta.get("iddq_skipped") else "",
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
            mux_mode = opts.mux_cov_mode(grp.out_name)
            vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode=mux_mode,
                                                  max_tests=opts.max_tests,
                                                  data_overrides=mux_data_for(opts, grp))
            if meta.get("value_collision"):
                skip_reason = ("数据寄存器位宽装不下 %d 个 case 的互异值——选错路也测不出(假绿)"
                               % len(grp.cases))
                vecs = []
            elif not vecs:
                skip_reason = _empty_vector_reason(meta)
            else:
                # designer 手填期望（与 build() 双轨同步——报告必须反映 .sv 真实断言值）
                apply_mux_expected(vecs, opts.mux_expected.get(grp.out_name.lower())
                                   or opts.mux_expected.get(grp.out_base.lower()))
                if _neg_enabled_for(grp, opts):
                    vecs = V.add_negatives(vecs, mode=opts.neg_mode, which=opts.neg_which,
                                           fixed_value=opts.neg_value)
                    for i, v in enumerate(vecs):
                        v.index = i
                # item③ DFT 拍：报告与 .sv 双轨一致（同 build 的 mux 挂点）；skip 原因进 meta 供透出
                _rskip = _append_dft_vectors(grp.out_base.lower(), vecs, wb, resolver,
                                             opts.mux_cov_mode(grp.out_name), opts.dft_observe)
                if _rskip:
                    meta["iddq_skipped"] = _rskip

        # 警告只在【真生成】时显示（被跳过的组用 error 列说明，警告无意义）
        row_warn = out_warn if not skip_reason else ""
        # 嵌套 mux 自动折叠 + 死分支去重(A2) 提示并进 warning/detail（让 --account/报告也能复核），
        # 但不改 verif 状态判定（这俩是"已自动处理+请复核"的软提示，不是阻断问题）
        nnote = getattr(grp, "normalized_note", "")
        snote = meta.get("shadowed_note", "") if meta else ""
        ocoll = ("手填数据值有撞值(≥2 数据路被手填成同值=假绿)，请核对手填值"
                 if (meta and meta.get("override_collision")) else "")
        omk = ("字段太窄已用点名法保护、手填值未生效(要生效需加宽字段)"
               if (meta and meta.get("override_ignored_marker")) else "")
        otr = ("手填值超字段宽已截断"
               if (meta and meta.get("override_truncated")) else "")
        cnote = (meta.get("contradiction_note") if meta else "") or exp.get("contradiction_note", "")
        # 缺口可见（M2）：alt 分支跳过 / iddq DFT 拍补不上 → 报告也透出（与 .sv 的 // ⚠ 同口径）
        gap = "；".join(meta.get(k) for k in ("cascade_alt_skipped", "iddq_skipped")
                        if meta and meta.get(k))
        # mode=0 裸名 force 线控网（第二十四轮）：testcase 已生成，仅层级前缀待补 → 报告也透出
        abare = meta.get("cascade_alt_bare") if meta else ""
        if abare:
            gap = (gap + "；" if gap else "") + ("mode=0 force 线控网 %s 用裸名(待 scan_rtl 配前缀)" % abare)
        warn_full = "；".join(x for x in [("⚙ %s" % nnote) if nnote else "",
                                          ("⚙ %s" % snote) if snote else "",
                                          ("⚠ %s" % ocoll) if ocoll else "",
                                          ("⚠ %s" % omk) if omk else "",
                                          ("⚠ %s" % otr) if otr else "",
                                          ("⚠ %s" % cnote) if cnote else "",
                                          ("⚠ %s" % gap) if gap else "", row_warn] if x)
        summary.append(dict(base_row, n_tests=len(vecs),
                            n_neg=sum(1 for v in vecs if v.is_negative),
                            control=",".join(meta.get("control", []) if meta else []),
                            data=",".join(meta.get("data", []) if meta else []),
                            unresolved="", error=skip_reason, warning=warn_full))
        # 可验证性行：issues/碰撞 → unresolved；top_out=0 裸名 → bare-probe（生成了，建议配前缀）；
        # 其余 clean。（与 GUI analyze_mux_group 同口径；note 只进 detail，不改状态色）
        if skip_reason and exp.get("spec_conflicts"):
            verif_status = "spec-collision"
        elif skip_reason:
            verif_status = "unresolved"
        else:
            verif_status = "bare-probe" if row_warn else "clean"
        mux_verif_rows.append({
            "R": grp.assert_id, "signal": grp.out_name, "owner": grp.owner,
            "type": "mux", "top": grp.top_output,
            "status": verif_status,
            "detail": skip_reason or warn_full, "out_net": "`%s.%s" % (W.ENV, grp.rtl_name),
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
                # DFT 拍 iddq 门 force 在 extra_forces（同 logic 路），报告 force 列带上以与 .sv 一致
                "force": "; ".join(
                    ["%s=%s" % (f["wire"], f["hex"]) for f in forces]
                    + ["%s=%s" % (wl, W.fmt_bin(wv, ww))
                       for (wl, wv, ww) in (getattr(vec, "extra_forces", None) or [])]),
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
        status = "spec-collision" if exp.get("spec_conflicts") else "unresolved"
        return {"status": status, "inputs": rows, "out_net": out_net,
                "error": "; ".join(exp["issues"]), "cone": False}
    vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode=mode,
                                          data_overrides=(mux_data_for(opts, grp) if opts else None))
    if meta.get("value_collision"):
        # 假绿不是"没解析"——结构全解析通了，只是数据字段太窄装不下互异值（硬生成=假的 PASS）。
        # 单列 'false-green' 档（GUI 琥珀，区别于真正的 ✗未解析/红），保护性跳过、非故障。
        return {"status": "false-green", "inputs": rows, "out_net": out_net,
                "error": "数据寄存器位宽装不下 %d 个 case 的互异值——选错路也测不出(假绿)，"
                         "需加宽字段或拆组才能验" % len(grp.cases),
                "cone": False}
    # 手填值撞值（B2，#9）：照常生成但状态标"假绿警告"（橙），让左表/明细也看得见——
    # 与 build/report 同口径（非阻断、用户负责）。
    if meta.get("override_collision"):
        return {"status": "false-green", "inputs": rows, "out_net": out_net,
                "error": "手填数据值有撞值：≥2 条数据路被手填成相同值=选错路也测不出(假绿)，请核对手填值",
                "blocking": False, "cone": False}
    if not vecs:
        return {"status": "unresolved", "inputs": rows, "out_net": out_net,
                "error": _empty_vector_reason(meta), "cone": False}
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
