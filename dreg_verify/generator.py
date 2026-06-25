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
                 append_to_logic=True, logic_mode=None, mux_mode=None, sig_cov=None,
                 mux_dropped=None, mux_cleared=None, mux_user_vecs=None,
                 suffix_override=None, append_to_mux=False,
                 logic_cascade=None, mux_cascade=None, sig_cascade=None,
                 logic_overrides=None, on_missing=None, suppress_mux_bare_probe=False,
                 assert_id_override=None):
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
        # t1：抑制 mux 块顶『top_out=0 用裸名探针』噪声警告——Topout 路径置 True（账目/报告已刻意抑制、
        # 新模型每信号都中会淹没；旧 logic-rooted 路径默认 False=保留，逐字节不变）。
        self.suppress_mux_bare_probe = bool(suppress_mux_bare_probe)
        # #7：断言标号覆盖 {源 out_name(小写): 标号}——Topout 路径按行序 1..N 命名，取代源 Excel R/mux<N>。
        # 经 render_signal_block 的 assert_id 参数注入(不写回源对象、守 R32)；默认 None=用源标号、逐字节不变。
        self.assert_id_override = {str(k).lower(): str(v) for k, v in (assert_id_override or {}).items()}
        # 真·仅负向：每个信号只保留负向向量(保持原 T 编号便于与"全部"导出对照)，
        # 无负向的信号整个跳过。CLI --neg-file separate 的负向文件用——
        # 之前是块级过滤(负向文件里混着正例)，汇总/REAL FAIL 统计会误导。
        self.negative_vectors_only = bool(negative_vectors_only)
        # 级联模式：输入引用"上游计算网"(级联到不自引用的 top 输出)时怎么驱动，见 级联模式说明.md：
        #   "cone"(默认) = 展开上游表达式驱动其源头寄存器（纯 Excel，不需要探针前缀）
        #   "force"      = 直接 force 字面 _to_logic 网（隔离验证每行；需要 scan_rtl 前缀）
        self.cascade_mode = cascade_mode if cascade_mode in ("cone", "force") else "cone"
        # 级联模式 logic/mux 解耦 + 单点（2026-06-11 用户拍板，与覆盖度同款）：logic_cascade/mux_cascade ∈
        # {cone,force}，未显式传(=None)则该侧回退全局 cascade_mode；sig_cascade={信号名(小写):模式} 压过
        # 类型全局。读取统一走 cascade_for(name,is_mux)，build/report/GUI 同口径。未传新参=逐字节不变。
        self.logic_cascade = logic_cascade if logic_cascade in ("cone", "force") else None
        self.mux_cascade = mux_cascade if mux_cascade in ("cone", "force") else None
        self.sig_cascade = {str(k).lower(): v for k, v in (sig_cascade or {}).items()
                            if v in ("cone", "force")}
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
        # 输出【引用尾缀】开关（2026-06-11 Hi1108）：top_output=0 的输出被下游引用时，探针网名补尾缀，
        # 尾缀随 Excel（logic 行引用→_to_logic、mux 页引用→_to_mux，见 excel_model.ref_suffix）。某些
        # 设计里 <名>_to_logic/_to_mux 恰是另一个真实输入网，补了就探错对象——关掉则直接探基名网。
        # 两个【独立】全局尾缀开关(用户拍板 logic/mux 分开)：
        #   append_to_logic 默认 True=被引用的 logic 输出探尾缀网(=RTL 真名，LPBT 实证)；
        #   append_to_mux  默认 False=mux 输出探裸名(端口尾缀设计相关 Excel 推不出，WL 裸名；整设计要补就开)。
        # _ls 与顶层输出不受影响。
        self.append_to_logic = bool(append_to_logic)
        self.append_to_mux = bool(append_to_mux)
        # 单点尾缀覆盖 {信号名/基名(小写): True=探尾缀网 / False=探裸名} —— 压过两个全局类型默认(Resolver 回填)：
        #   logic 撞名信号(如 lo2g5g)置 False 单独探裸名；个别 mux 输出置 True/False 单独定。
        #   空=只跟类型默认(老调用方逐字节不变)。
        self.suffix_override = {str(k).strip().lower(): bool(v)
                                for k, v in (suffix_override or {}).items()}
        # 红区 binder「配了前缀但真名不存在」时的处置策略(甲/乙)，per-signal（goal-redzone-binder M0）：
        #   {信号名/基名(小写): "warn"(甲,默认,只警告不擅改) / "fallback"(乙,binder 退到真名跑通)}。
        #   不在表=warn。纯随 claims.json 进红区给 binder 用，不碰 .sv（默认逐字节不变）。
        self.on_missing = {str(k).strip().lower(): v for k, v in (on_missing or {}).items()
                           if v in ("warn", "fallback")}
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
        # mux 删除/清空（第二十六轮，用户拍板「mux 也要能删、logic+mux 都要一键清空」）：
        #   mux_dropped = {mux信号名(小写): {向量签名,...}} —— 用户在 mux 真值表里删掉的【个别测试列】，
        #     签名 = mux_assign_key(v.assignments)(与手填期望同口径的稳定键)；切覆盖度后对不上签名的
        #     自然失效(那列本就不存在)。
        #   mux_cleared = {mux信号名(小写)} —— 用户「一键清空」的 mux 信号 = 零用例(与覆盖度无关，整组不产出)。
        # 二者只作用于 mux 正向向量(负向由 add_negatives 在过滤后的正向上再生，连带消失)；logic 的清空
        # 走 vector_overrides 空列表(见 build() 空 override 跳过)。
        self.mux_dropped = {str(k).lower(): set(v) for k, v in (mux_dropped or {}).items() if v}
        self.mux_cleared = {str(x).lower() for x in (mux_cleared or [])}
        # {mux信号名(小写): [TestVector,...]} —— 用户在 mux 真值表里【手编/复制】的额外测试列（第二十八轮，
        # 让 mux 与 logic 平级：能加正向列/复制列/重命名列/逐 case 加负向）。在 make_mux_vectors 之后、
        # mux_dropped 过滤之前注入(故也能被删列签名过滤)；正向参与 designer 期望/全局负向追加，负向用户列
        # 原样保留。与全局负向重叠的相同负向由 _dedup_negatives 去重。每条 vec 的 case_index 标它路由的 case。
        self.mux_user_vecs = {str(k).lower(): list(v) for k, v in
                              (mux_user_vecs or {}).items() if v}
        # 「RTL 补充逻辑 / cone patch」(2026-06-12 用户拍板)：Excel 真表丢了某信号顶层口后的 ECO 级
        # (如 d_en_vco_fc：SE 确认顶层口后接 2:1 mux + 二级 iddq，真表只到 DREG，缺的控制网悬空→X)。
        # 允许为该信号【手工补一条等价 logic 表达式】，工具把它当成合成 logic 行塞进 wb.logic、走同一条
        # 解析/扫真值表/审计路径——于是 ECO 新输入(d_vco_fc_sel/faston/二级iddq)自动成为真值表新维度，
        # 并继承 designer 期望/覆盖度/R36 按值筛选/CSV/for_test 回填，零新渲染。这是【偏离纯 Excel 推导】，
        # 故合成信号块顶强制 // ⚠ + 报告 banner + 汇总 + CLI(走 regmap_warnings 同款审计通道)，SE review 必见。
        #   logic_overrides = {信号基名(小写): {
        #       "enabled": bool(默认 True),      # False=不生效(GUI 开关关)，逐字节回退 Excel 原行
        #       "expr":  "ECO_IDDQ ? 1'b0 : (VCO_FC_SEL ? VCO_EN_FASTON : (EN & ~IDDQ))",  # 变量=真实名(大小写无关)
        #       "inputs": [{"var": "EN", "raw": "<原使能寄存器>"}, {"var": "VCO_FC_SEL", "raw": "d_vco_fc_sel_ls[0]"}, ...],
        #       "out_name": "d_en_vco_fc",       # 可选，缺省=信号原名(继承位宽/assert号/owner/top_output/尾缀)
        #       "note": "SE 确认 ECO 接 mux+二级iddq，真表只到 DREG",  # 理由，进块顶 ⚠ 与报告
        #   }} —— var 缺省=raw 的基名(大写)；raw 带 [msb:lsb] 切片照解析。enabled=False 或无 expr 的项忽略。
        # 未传(=None)→ 行为与旧版逐字节一致(build/report swap-and-restore 不动 wb.logic)。
        self.logic_overrides = _norm_logic_overrides(logic_overrides)

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

    def cascade_for(self, name=None, is_mux=False):
        """该信号实际生效的级联模式 {cone,force}：单点 sig_cascade > 类型全局(logic/mux) > 全局
        cascade_mode。build/report/GUI 解析每个信号前据此设 resolver.cascade_mode。"""
        ov = self.sig_cascade.get(name.lower()) if name else None
        if ov in ("cone", "force"):
            return ov
        tier = self.mux_cascade if is_mux else self.logic_cascade
        return tier if tier in ("cone", "force") else self.cascade_mode


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


def _norm_logic_overrides(x):
    """规范 logic_overrides：{信号基名(小写): spec}。丢弃 enabled=False / 无 expr 的项。
    spec 原样保留(splice 时才据 base_sig 构造合成 LogicSignal)。空/None → {}。"""
    if not x:
        return {}
    out = {}
    for k, spec in x.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("enabled", True) is False:
            continue
        if not str(spec.get("expr", "") or "").strip():
            continue
        out[str(k).strip().lower()] = spec
    return out


def make_supplement_signal(base, spec, base_sig):
    """据 logic_overrides 的一条 spec 构造【合成 LogicSignal】(RTL 补充逻辑)。

    base     — 信号基名(小写)。
    spec     — {"expr","inputs":[{"var","raw"}...],"out_name"?,"note"?,...}，见 GenOptions.logic_overrides。
    base_sig — Excel 原 LogicSignal(同基名)或 None(纯新增信号)。命中时【继承】输出身份：
               out_name/位宽、assert_id、owner、top_output、suffix、ref_suffix、_ls_name —— 补充只改
               【喂给输出的逻辑】(expr+inputs)，不改【在哪儿探这个输出】。

    变量名 = expr 里出现的标识符(parse 时统一 .upper())；inputs 的 var 缺省=raw 的基名(大写)，键须与之一致。
    raw 带 [msb:lsb] 切片照 _strip_width 解析。返回的 LogicSignal 打 _is_supplement / _supplement_note 供审计。
    """
    inputs = {}
    for it in (spec.get("inputs") or []):
        raw = str(it.get("raw", "") or "").strip()
        if not raw:
            continue
        base_with_tl, width, msb, lsb = excel_model._strip_width(raw)
        in_base = excel_model.strip_to_logic(base_with_tl)
        var = str(it.get("var", "") or "").strip() or in_base
        inputs[var.upper()] = {
            "raw": raw, "base": in_base, "width": width, "msb": msb, "lsb": lsb,
        }

    out_name = str(spec.get("out_name", "") or "").strip() \
        or (base_sig.out_name if base_sig else base)
    out_base, out_width, _, _ = excel_model._strip_width(out_name)
    sig = excel_model.LogicSignal(
        row=(base_sig.row if base_sig else -1),
        out_name=out_name,
        out_width=out_width,
        expr=str(spec.get("expr", "")),
        suffix=(base_sig.suffix if base_sig else str(spec.get("suffix", "") or "")),
        top_output=(base_sig.top_output if base_sig else spec.get("top_output", "1")),
        notes=(base_sig.notes if base_sig else str(spec.get("note", "") or "")),
        owner=(base_sig.owner if base_sig else str(spec.get("owner", "") or "")),
        assert_id=(base_sig.assert_id if base_sig else str(spec.get("assert_id", "") or "")),
        inputs=inputs,
    )
    # 继承输出探针身份(补充只改逻辑、不改探点)
    if base_sig is not None:
        sig.ref_suffix = base_sig.ref_suffix
        sig._append_to_logic = base_sig._append_to_logic
        sig._ls_name = base_sig._ls_name
        sig._ls_is_top = base_sig._ls_is_top
    # 自引用后缀(中心不变量，2026-06-16 SUF-2)：从本补充自己输入列引用 <out_base>_to_xxx 推，再重过闸门——
    # 补充可能引入原信号没有的 ECO 自引用输入，避免合成信号绕过自引用抑制。
    # ⚠ 只在 out_base 与原信号一致时才继承原信号的自引用集——_self_ref_suffixes 是【绑 out_base 的事实】，
    #   若补充用 out_name 改了基名，原集合对的是另一身份，继承过来会按错对象抑制/保留(对抗审查实证)。
    if base_sig is not None and sig.out_base.lower() == base_sig.out_base.lower():
        sig._self_ref_suffixes = set(getattr(base_sig, "_self_ref_suffixes", ()))
    else:
        sig._self_ref_suffixes = set()
    _ob = sig.out_base.lower()
    for _info in inputs.values():
        _rb = excel_model._strip_width(_info["raw"])[0].lower()
        if _rb == _ob + "_to_logic":
            sig._self_ref_suffixes.add("_to_logic")
        elif _rb == _ob + "_to_mux":
            sig._self_ref_suffixes.add("_to_mux")
    sig.ref_suffix = excel_model._pick_ref_suffix(sig, sig.ref_suffix)
    sig._is_supplement = True
    sig._supplement_note = str(spec.get("note", "") or "").strip()
    return sig


def _logic_with_overrides(wb, opts):
    """返回 logic 信号列表：命中 logic_overrides 的信号【替换】为合成 LogicSignal、纯新增信号【追加】。
    原 LogicSignal 对象不被改动、wb.logic 不被原地改写(守 R32：build/report 用不同 opts 反复跑同一 wb
    时不互相污染)。无 override → 直接返回 wb.logic 原对象(逐字节不变)。"""
    ov = getattr(opts, "logic_overrides", None)
    if not ov:
        return wb.logic
    have = {s.out_base.lower() for s in wb.logic}
    out, used = [], set()
    for s in wb.logic:
        b = s.out_base.lower()
        if b in ov and b not in used:
            out.append(make_supplement_signal(b, ov[b], s))
            used.add(b)
        else:
            out.append(s)
    for b, spec in ov.items():            # 纯新增：基名不在 Excel logic 页
        if b not in have:
            out.append(make_supplement_signal(b, spec, None))
    return out


def _supplement_warning(sig):
    """合成「RTL 补充」信号的块顶/汇总告警串：表达式 + 理由。"""
    msg = "本信号逻辑为手工补充(Excel 真表缺此级 ECO/缺失)，已用补充式扫真值表: %s" % sig.expr
    note = getattr(sig, "_supplement_note", "") or ""
    if note:
        msg += "　理由: %s" % note
    return msg


# ───────────────────────────── 生成期"自检闸门"（2026-06-16 R42：把假绿做成块顶 ⚠）─────────────────────────────
# 总思路：工具靠命名约定/cone 猜 RTL 网名，假绿(探到真实但语义错的网、仿真静默通过)是最致命失败。
# 这三条在生成期主动查、命中即块顶 ⚠ + 汇总，让"少验一支/读错对象"无声无息变成看得见。红区真 nets 当
# 裁判是治本(本轮不碰)，这是工具侧能独立做的"使假绿可见"。logic/mux 共用 selfaudit_warnings 通道。
def _selfaudit_probe_self_ref(sig):
    """检查1(纵深防御)：输出探针网名 == 本信号自己的某条输入前级网(<基名>_后缀)。
    _pick_ref_suffix 已保证 ref_suffix ∉ _self_ref_suffixes、正常恒不触发(=0)；一旦触发=有新的
    ref_suffix 赋值路径绕过了中心闸门(同 R40/SUF-2 结构)→ 块顶 ⚠ 暴露。比【全名】rtl_base(含尾缀/
    开关/_ls)而非基名——基名比会误伤正常直通(force 寄存器 X、探顶层 pin X 同基名不同网，是对的)。
    logic / mux 通用(鸭子类型，都有 _self_ref_suffixes/rtl_base/out_base)。"""
    srs = getattr(sig, "_self_ref_suffixes", None)
    if not srs:
        return None
    probe = (getattr(sig, "rtl_base", "") or "").lower()
    ob = sig.out_base.lower()
    for s in sorted(srs):
        if probe == (ob + s).lower():
            return ("探针网名 %s == 本信号自己的输入前级网 <%s>%s —— assert 在读回自身输入(假绿/假红)；"
                    "ref_suffix 绕过了自引用闸门，需排查赋值路径" % (sig.rtl_base, sig.out_base, s))
    return None


def _selfaudit_output_width(sig, node, env):
    """检查2：K 列漏标位宽 → out_width 默认 1 → 断言只验最低 1 bit、高位选路/拼接故障静默假绿。
    仅当 K 列【无显式位宽】(_strip_width msb 为 None)且表达式自决宽 > 1 时报；显式写 X[0](msb 非 None、
    宽 1)是有意 1 bit、不报。self_width 已把约简/比较/位选算成 1 bit，故 X=|gain 这类不误伤。
    _ls 顶层口由 level_shift 页给名、宽度不走 K 列推断 → 跳过。"""
    if getattr(sig, "_ls_name", None):
        return None
    _, _, omsb, _olsb = excel_model._strip_width(sig.out_name)
    if omsb is not None:                   # K 列写了 [n] 或 [m:n] → 有意为之、不报
        return None
    try:
        sw = E.self_width(node, env)
    except Exception:                      # noqa: BLE001 — 求宽失败(如 Repeat 缺值)不阻断生成
        return None
    if sw and sw > 1:
        return ("K 列可能漏标位宽：表达式自决宽 %d bit、但输出按 1 bit 断言(K 列无 [msb:lsb])——"
                "高位选路/拼接故障不会被发现(假绿)；如确为 1 bit 输出可忽略，否则补 K 列位宽" % sw)
    return None


def _selfaudit_rw_truncation(bindings):
    """检查3：RW 输入写进寄存器字段时被静默截断——输入占位 slice_lsb+width 超出字段容量
    (reg_msb-reg_lsb+1) → sv_writer 的 & mask(fw) 会丢高位、designer 对照 for_test 也看不出。
    结构判据(与测试值无关)。字段边界未知时与 compute_drives 同口径用默认 16 位、不漏报。返回告警串或 None。"""
    bad = []
    for b in bindings.values():
        if b is None or not getattr(b, "resolved", False) or b.kind != "RW":
            continue
        # 字段宽度必须与生成 .sv 的权威路径 sv_writer.compute_drives 同口径：reg_msb/reg_lsb 已知→真实宽；
        # 【未知→默认 16】(compute_drives:139 在 None 时 fw=16 并 & mask(16) → 真会按 16 位截断)。早先直接
        # continue 跳过 None 路径 = 漏报 compute_drives 的默认-16 截断(对抗审查实证盲区)。
        if b.reg_msb is not None and b.reg_lsb is not None:
            fw = b.reg_msb - b.reg_lsb + 1
        else:
            fw = 16
        need = (b.slice_lsb or 0) + b.width
        if need > fw:
            bad.append("%s(占 %d bit@lsb%d > 字段 %d bit)"
                       % (b.base, b.width, (b.slice_lsb or 0), fw))
    if bad:
        return ("RW 写值可能被截断：%s —— 高位会被字段宽度静默丢弃(假绿)，"
                "请核对输入位宽与 regmap 字段宽" % "、".join(bad))
    return None


# ───────────────────────── claim 清单导出（2026-06-16 R42：红区 scan_rtl 校验器的输入契约）─────────────────────────
# 治本=红区真 nets 当裁判：把工具【声称】要探/要 force 的每根网名带进红区，scan_rtl 校验"探针是真·RTL
# 输出网(不是某 assign 的 RHS 输入网)、force 网真实存在"。红区只能放出小文本/OCR → 大 claim 清单【进】、
# 只放出小报告(本轮不碰红区)。这里把字段定死 + 能从生成结果忠实导出(纯工具侧)。每条 claim 字段固定：
#   signal/aid    — 归属信号(K 名)与 assert 序号
#   kind          — probe(输出探针) / force(RO 输入强制网) / rfwrite(RW 输入寄存器写，非物理网)
#   identity      — output / input
#   net_base/slice/prefix/full — 网基名 / [msb:lsb] / 层级前缀 / .sv 里的完整字符串
#   found_in      — 来源(probe: level_shift/ref_xxx/bare；input: tmm/regmap/wire/needs-prefix/mux-output…)
#   addr          — rfwrite 的寄存器地址(其余 None)
#   self_ref_suffixes — 该信号自读后缀(校验器交叉核对自引用，应为空)
#   is_mux/top_output — 来自 mux 页 / 是否芯片顶层输出
#   on_missing    — 配了前缀但真名不存在时的策略：warn(甲,默认,只警告不擅改) / fallback(乙,binder 退到真名)
#   input_nets    — 该信号期望输入网基名清单(红区 binder 拿 assign 的 RHS 做指纹、定哪条 assign 才是它)
_CLAIM_KEYS = ("signal", "aid", "kind", "identity", "net_base", "slice", "prefix",
               "full", "found_in", "addr", "self_ref_suffixes", "is_mux", "top_output",
               "on_missing", "input_nets")

# claims.json 契约版本号（2026-06-23 C2，跨空气墙防静默不兼容）。红区 binder/diag 据它判能否消费：
#   schema_version 升一档 = 字段/语义有不向后兼容变化(旧红区工具应拒绝或降级，而非自信给错裁决)。
#   naming_model = 探针网名的【命名模型】：
#     'logic-rooted' = 旧路径，探针名按命名约定+尾缀【猜】出(_to_logic/_to_mux/_ls/前缀)，可能假绿；
#     'topout'       = 新路径，探针名是 Topout 顶层真名(无路由后缀)，cone 展到底→近零前缀。
#   binder 拿到后：logic-rooted → 仍需逐探针核对真名(R41/R42 那套)；topout → 顶层口直接核存在性即可。
CLAIM_SCHEMA_VERSION = 2
NAMING_MODEL_LOGIC = "logic-rooted"
NAMING_MODEL_TOPOUT = "topout"


def _probe_provenance(obj, is_topout=False):
    """探针网名【从哪来】(红区 binder 据此判可信度)：
      'topout'      = Topout 顶层真名(C1,2026-06-23)，无路由后缀、cone 已展到源，理应直接命中；
      'level_shift' = level_shift 页给的 _ls 顶层口；
      'ref_to_xxx'  = 被下游引用补的尾缀(猜的，可能假绿)；
      'bare'        = 裸基名。
    is_topout=True(Topout 路径传入) → 直接标 topout(顶层真名权威，压过尾缀/ls 推断)。"""
    if is_topout:
        return "topout"
    if getattr(obj, "_ls_name", None):
        return "level_shift"
    rs = getattr(obj, "ref_suffix", "")
    base = getattr(obj, "rtl_base", "") or ""
    if rs and base.lower().endswith(rs.lower()):
        return "ref%s" % rs
    return "bare"


def _claim(**kw):
    c = {k: None for k in _CLAIM_KEYS}
    c.update(kw)
    return c


def collect_claims(obj, bindings, prefix, is_mux, on_missing="warn", is_topout=False, aid=None):
    """为一个已生成的 logic 信号 / mux 组导出网名声明(探针 + force/rfwrite 输入)。

    探针网名取 rtl_base/rtl_name(=.sv 里 assert 的真名，已含尾缀/开关/_ls/前缀逻辑)；输入网取 resolver
    解析出的 b.wire(RO) 或 b.base+addr(RW)。同信号内按网名去重。纯导出、不改产物。

    on_missing(甲warn/乙fallback) 与 input_nets(期望输入网基名清单,= 各输入绑定的 b.wire/b.base)
    挂在探针 claim 上，供红区 binder：前者定配错网的处置策略、后者拿 assign 的 RHS 做指纹。

    is_topout(C1,2026-06-23)：本探针来自 Topout 路径——探针名是顶层真名(无路由后缀)，provenance 标
    'topout'(binder 据此知道这名字权威、需前缀=意外埋件)。默认 False=旧 logic-rooted 路径(逐字节不变)。"""
    claims = []
    _aid = aid if aid is not None else (obj.assert_id or "")   # #7 行序标号覆盖；默认源标号
    base = getattr(obj, "rtl_base", obj.out_base)
    name = getattr(obj, "rtl_name", obj.out_name)
    slc = name[len(base):] if name.startswith(base) else ""
    full = ("%s.%s" % (prefix.strip("."), name)) if prefix else name
    claims.append(_claim(
        signal=obj.out_name, aid=_aid, kind="probe", identity="output",
        net_base=base, slice=slc, prefix=prefix or "", full=full,
        found_in=_probe_provenance(obj, is_topout=is_topout),
        self_ref_suffixes=sorted(getattr(obj, "_self_ref_suffixes", ())),
        is_mux=bool(is_mux), top_output=bool(getattr(obj, "is_top", False)),
        on_missing=on_missing if on_missing in ("warn", "fallback") else "warn"))
    seen = set()
    in_nets = set()
    for _ltr, b in (bindings or {}).items():
        if b is None or not getattr(b, "resolved", False):
            continue
        if b.kind == "RO":
            in_nets.add(b.wire)
            # full = .sv 里真正 force 的 LHS(= b.wire_lhs，多位带 [msb:lsb] 切片)，与探针 claim 同口径
            # (探针 full 也含切片)；按 lhs 去重 → 同网不同切片(x[3:1] 与 x[0])算两条 force、不误并。
            lhs = b.wire_lhs
            if ("force", lhs) in seen:
                continue
            seen.add(("force", lhs))
            slc = lhs[len(b.wire):] if lhs.startswith(b.wire) else ""
            claims.append(_claim(
                signal=obj.out_name, aid=_aid, kind="force",
                identity="input", net_base=b.wire, slice=slc, prefix="", full=lhs,
                found_in=b.found_in or "", self_ref_suffixes=[],
                is_mux=bool(is_mux), top_output=False))
        elif b.kind == "RW":
            in_nets.add(b.base)
            if ("rfwrite", b.base, b.address) in seen:
                continue
            seen.add(("rfwrite", b.base, b.address))
            claims.append(_claim(
                signal=obj.out_name, aid=_aid, kind="rfwrite",
                identity="input", net_base=b.base, slice="", prefix="", full=b.base,
                found_in=b.found_in or "", addr=("0x%X" % b.address) if b.address is not None else None,
                self_ref_suffixes=[], is_mux=bool(is_mux), top_output=False))
    claims[0]["input_nets"] = sorted(n for n in in_nets if n)
    return claims


def _on_missing_for(obj, opts):
    """该信号「配了前缀但真名不存在」的策略(甲warn/乙fallback)——按全名/基名查 opts.on_missing，缺省 warn。"""
    om = getattr(opts, "on_missing", None) or {}
    for k in (getattr(obj, "out_name", ""), getattr(obj, "out_base", "")):
        v = om.get(str(k).lower())
        if v in ("warn", "fallback"):
            return v
    return "warn"


def _name_matches(sig, names):
    """信号是否在指定名集合里（支持 K 全名与去位宽基名）。"""
    if names is None:
        return True
    cand = {sig.out_name.lower(), sig.out_base.lower()}
    return bool(cand & names)


def scope_filter_vectors(vecs, scope):
    """按导出范围过滤向量：'pos'=剔负向(给仿真)；'neg'=只留负向(自检)；'all'/其它=原样。"""
    if scope == "pos":
        return [v for v in vecs if not v.is_negative]
    if scope == "neg":
        return [v for v in vecs if v.is_negative]
    return list(vecs)


def scope_filter_overrides(eo, scope):
    """按导出范围(pos/neg)过滤 edit_overrides 的每条向量列表。返回 (新eo, neg_src:set|None)。

    Topout/页本地视图的负向【全部来自编辑(eo)】、自动向量恒正向，故按 scope 过滤 eo 即可：
      · 'pos' → 各列表剔负向（空列表保留=尊重清零=零用例）；
      · 'neg' → 各列表只留负向，并收集【有负向的源名集合】neg_src(logic/register/dft 源 out_name +
                mux 源名)，供调用方把 generator.build 的信号集限到这些源(否则冒出一堆正向自动信号)。
    'all'/其它 → 原样返回 (eo, None)。"""
    if scope not in ("pos", "neg"):
        return eo, None
    eo = eo or {}
    out = dict(eo)
    neg_src = set() if scope == "neg" else None
    keep = (lambda v: v.is_negative) if scope == "neg" else (lambda v: not v.is_negative)

    def _filt_map(key):
        m = {}
        for k, vl in (eo.get(key) or {}).items():
            fv = [v for v in (vl or []) if keep(v)]
            if scope == "neg":
                if fv:
                    m[k] = fv
                    neg_src.add(str(k).lower())
            else:
                m[k] = fv                       # pos: 空列表也留(清零=零用例，不回退自动)
        return m or None

    out["vector_overrides"] = _filt_map("vector_overrides")
    out["reg_overrides"] = _filt_map("reg_overrides")
    out["mux_user_vecs"] = _filt_map("mux_user_vecs")
    if scope == "neg":
        # 正向侧的 mux 编辑(期望/删列/清空)在仅负向导出里无意义 → 清掉
        out["mux_expected"] = None
        out["mux_dropped"] = None
        out["mux_cleared"] = None
    return out, neg_src


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


def _dedup_negatives(vecs):
    """去掉完全相同的负向向量（同 assignments + 同错值）。第二十八轮起 mux 可同时有
    全局负向(add_negatives/which=first)与用户逐 case 加的负向——二者可能对同一条正向各生成
    一条相同负向，去重保留先出现的，避免重复断言。正向不去重（用户复制列=有意的重复测试）。
    无用户负向时(常规路径)负向 assignments 天然互异 → 此函数为恒等，逐字节不变。"""
    seen = set()
    out = []
    for v in vecs:
        if v.is_negative:
            key = (tuple(sorted(v.assignments.items())), v.neg_value)
            if key in seen:
                continue
            seen.add(key)
        out.append(v)
    return out


def apply_mux_expected(vecs, exp_map):
    """把 designer 手填期望写到 mux 向量上（按输入取值键匹配；负向不碰）。

    须在 add_negatives 之前调用——make_negative 的错值防撞需要看到 designer_expected。"""
    if not exp_map:
        return
    for v in vecs:
        if v.is_negative:
            continue
        # 已有 designer_expected 的不覆盖：用户手编 mux 列(第二十八轮)自带期望，其 assignments 可能与
        # 某自动生成列相同(=同 mux_assign_key)；若覆盖会用自动列的期望盖掉用户在自己那列填的值。
        if v.designer_expected is not None:
            continue
        de = exp_map.get(mux_assign_key(v.assignments))
        if de is not None:
            v.designer_expected = int(de) & E.mask(v.exp_width)


def mux_data_for(opts, grp):
    """该 mux 组的用户手填数据值覆写 {物理基名(小写): int}，没有则 None（按信号名/基名两套键查）。"""
    return (opts.mux_data.get(grp.out_name.lower())
            or opts.mux_data.get(grp.out_base.lower()) or None)


def _append_dft_vectors(out_base, vecs, wb, resolver, input_bases=None):
    """item③ iddq DFT 态拍（第二十二轮）：被 dft 页门控的输出，在功能向量外追加一条 DFT 态拍——
    force 门(iddq)到选中【常量支】的值、断言输出=该常量(0)，该拍后还原门态（S4：否则 force 的
    iddq=1 会钉死后续所有拍/块的门）。

    所有覆盖档都补（2026-06-10 Hi1108 实地反馈：精简档原本跳过且无任何标注，用户对照 for_test
    发现 mixer2g_trim 整个少了 iddq 这个源头控制——精简裁的是数据/case 组合数量，不该漏掉一个
    输入源；designer 的 for_test 最小集也带 iddq，且每个被门控输出只多 1 条向量）。
    原地追加进 vecs（T 编号由调用方统一重排）。本拍后 release 门，让门回 RTL 默认(透传)（S4：否则
    force 的 iddq=1 会钉死后续所有拍/块的门）。
    input_bases：本信号【已显式驱动】的输入基名集合(lowercase)。门网已是显式输入时不再补 DFT 拍——
    门已被当作真值表输入扫了 0/1（含 iddq=1 那拍），再补 DFT 拍就重复（2026-06-12：SE 把 iddq 控制
    挪进 dft 页后，与 RTL 补充里显式列的同一 iddq 撞成两行）。
    返回 None（正常补 / 该输出无 dft 门 / 门已是显式输入 → 无需补）或 str（被门控但补不了的原因，供 meta['iddq_skipped']）。
    """
    g = wb.dft.get(out_base) if getattr(wb, "dft", None) else None
    if not g:
        return None                                   # 该输出不被 dft 门控 → 无需 DFT 拍
    if input_bases and g["gate_base"] in input_bases:
        return None                                   # 门网已是显式输入(被扫 0/1) → 不重复补 DFT 拍
    # 门网解析（与 pin_dft_gate 同口径）：必须是可 force 的 RO 网
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
    dv.release_nets = [b.wire_lhs]                    # 本拍后 release，让门回 RTL 默认(透传)（S4）
    vecs.append(dv)
    return None


def pin_dft_gate(out_base, vecs, wb, resolver, input_bases=None):
    """被 dft 页门控的输出：把门(iddq)当作每条测试的【显式输入】，force 到透传值。

    2026-06-10 用户三轮澄清后定稿：designer 的 for_test 输入信号清单里有 iddq
    （功能测试=0 透传），我们生成的测试输入集必须同口径——门是该输出的真实源头控制，
    每条向量都显式驱动它，不靠 RTL 默认值（防上一块 force 残留 + 输入完整、可对照）。
    DFT 拍除外（它本身 force 门=1 验常量支）。门解析不了/非 RO 时不钉（输入表红色
    未解析 + iddq_skipped 已有提示），返回 None。

    input_bases：本信号【已显式驱动】的输入基名集合(lowercase)。门网已是显式输入时返回 None、
    不再当 DFT 门钉一遍——否则真值表同一根 iddq 网出现两行（显式输入 + DFT 门）。2026-06-12：
    SE 把 iddq 控制挪进 dft 页后，与 RTL 补充里显式列的同一 iddq 撞成两行，由此去重；显式输入
    （被扫 0/1）比"钉到透传值"更完整，让它当家。

    幂等（同一门只加一次 extra_forces），GUI/build/report 可重复调用。
    返回 (门绑定, 透传值) 或 None——调用方拿去做显示（GUI 输入行 / 报告 inputs 行）。
    """
    g = wb.dft.get(out_base) if getattr(wb, "dft", None) else None
    if not g:
        return None
    if input_bases and g["gate_base"] in input_bases:
        return None                                   # 门网已是显式输入 → 不重复当 DFT 门
    info = {"raw": g["gate_base"], "base": g["gate_base"], "width": 1, "msb": None, "lsb": None}
    b = resolver.resolve("dft_gate_" + g["gate_base"], info)
    if not (b.resolved and b.kind == "RO"):
        return None
    transp = int(g["transparent"])
    for v in vecs:
        if getattr(v, "dft_pitch", False):
            continue
        ef = list(getattr(v, "extra_forces", None) or [])
        if any(wl == b.wire_lhs for (wl, _x, _w) in ef):
            continue
        v.extra_forces = ef + [(b.wire_lhs, transp, 1)]
    return (b, transp)


def fortest_order_entries(entries, wb, out_base, name_fn, key_fn=None):
    """把输入条目排成 for_test 的次序（2026-06-10 用户两轮澄清定稿）。

    优先级：① 该输出在 for_test 页里有现成组（罕见——for_test 平时是空的，偶有样例）
    → 按其输入行序；② 否则按【寄存器地址 + bit 位】排（key_fn 给 (addr, reg_lsb)）——
    这是 designer for_test 生成工具的规则（实证：mixer2g_trim 样例 = mode/local(0x3C
    bit0/4) → iddq(h47) → tsensor(0xA0) → 数据 t0..t7(0x184 bit0..14) 严格地址递增）；
    ③ 没给 key_fn → 原序。无地址的条目（纯 wire）排在有地址的之后、保持原相对顺序。
    稳定排序，不丢条目。entries 任意对象列表，name_fn(条目)→基名(小写、无位宽)。
    """
    entries = list(entries)
    order = (getattr(wb, "fortest_order", None) or {}).get((out_base or "").lower())
    if order:
        pos = {n: i for i, n in enumerate(order)}
        big = len(order)
        return [e for _k, e in sorted(((pos.get(name_fn(e), big + i), e)
                                       for i, e in enumerate(entries)), key=lambda t: t[0])]
    if key_fn is None:
        return entries
    def _canon(i, e):
        addr, lsb = key_fn(e)
        return (0, addr, lsb or 0, i) if addr is not None else (1, 0, 0, i)
    return [e for _k, e in sorted(((_canon(i, e), e) for i, e in enumerate(entries)),
                                  key=lambda t: t[0])]


def probe_prefix_for(sig, opts):
    """该信号的探针层级前缀，没有则空串。

    映射 key 兼容两套名字：Excel K 列名（d_ndiv_cnt_div_sel）和 RTL 网名（d_ndiv_cnt_div_sel_ls）。
    scan_rtl.py 导出的映射用 RTL 网名，手工配置常用 K 列名——都认。
    """
    p = opts.probe_prefixes or {}
    rtl_base = getattr(sig, "rtl_base", sig.out_base)
    rtl_name = getattr(sig, "rtl_name", sig.out_name)
    # 全名(无视尾缀开关，总带 ref_suffix)：scan_rtl 导出/用户手配的前缀 key 常是带 _to_logic 的全名。
    # 尾缀开关关时 rtl_base 退成裸名，只认 rtl_base 会让那条前缀静默失配 → 裸名直贴 ENV_RF → CUVUNF
    # (2026-06-11 Hi1108 rxiq 实证)。两个名都试：用户按裸名或全名配的前缀都能命中。
    rtl_base_full = getattr(sig, "rtl_base_full", rtl_base)
    rtl_name_full = getattr(sig, "rtl_name_full", rtl_name)
    rtl_keys = (p.get(rtl_name.lower()) or p.get(rtl_base.lower())
                or p.get(rtl_name_full.lower()) or p.get(rtl_base_full.lower()) or "")
    # 走 level_shift 的输出：前缀【只认 _ls 真网名】(rtl_name/rtl_base)，不认 K 列裸名。
    # 按裸名配的前缀指向的是【电平移位前/消费侧】那根网(datapath_clk_en 在 U_WUR_PLL_DATAPATH_0 的
    # input 端口)——对这根 _ls 网是错的，忽略。真名=裸名时(pll_n 经 level_shift 后仍叫 pll_n)，用户按
    # pll_n 配的前缀就是 rtl_base、照常命中。(2026-06-12：续2 曾按 _ls_is_top 一律清前缀→把 pll_n 配的
    # U_BT_LP_PLL_DIG 也误清 CUVUNF；改为按真网名匹配，两种情形都对。)
    if getattr(sig, "_ls_name", None):
        return rtl_keys
    return p.get(sig.out_name.lower()) or p.get(sig.out_base.lower()) or rtl_keys


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
    """探针前缀映射文本 → dict。GUI 编辑器与映射文件共用，**两种格式都认**：

      ① 扁平格式（每行一条）：
            pll_n=U_BT_LP_PLL_DIG
            mon_active=U_BT_LP_PLL_DIG.DIG_1

      ② 合并格式（路径分组，scan_rtl 信号多时省去重复路径，方便填写）。
         『路径:』单独一行，其下的信号名可【每行一个】，也可【一行多个、逗号或空格分隔】：
            U_BT_LP_PLL_DIG:
                pll_n, mon_active      ← 一行多个：逗号 / 空格都能分隔
            U_BT_LP_PLL_DIG.DIG_1:
                xxx                    ← 每行一个也可以

    三种可在同一文件混用。空行/#注释跳过；含『=』按①；以『:』结尾按②的组头；
    其余裸行=当前组头之下的信号名（按逗号+空格拆成多个，无组头在前则跳过）。
    信号名小写、路径去首尾点。（注：位宽切片 sig[3:0] 不含逗号/空格、不以『:』结尾，整体作一个名。）"""
    out = {}
    cur = None                                     # 合并格式当前组头路径（None=还没遇到组头）
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if "=" in ln:                              # ① 扁平 信号名=路径（不影响当前组头）
            name, prefix = ln.split("=", 1)
            if name.strip() and prefix.strip():
                out[name.strip().lower()] = prefix.strip().strip(".")
            continue
        if ln.endswith(":"):                       # ② 组头『路径:』（空路径=顶层无需前缀）
            cur = ln[:-1].strip().strip(".")
            continue
        if cur:                                    # ② 组头之下的信号名（一行可多个，逗号/空格分隔）
            for tok in ln.split("#", 1)[0].replace(",", " ").split():   # 去行尾 # 注释再拆
                out[tok.lower()] = cur
    return out


def render_probe_prefix_grouped(mapping):
    """{信号名: 路径} → 合并格式文本（按路径分组，路径只写一次）。

    parse_probe_prefix_lines 可无损还原。空映射 → 空串。GUI 前缀编辑器显示/导出、
    与 scan_rtl 的 render_prefix_file 同一排版（信号名逗号分隔挤在一行），便于「信号太多」时
    阅读和手工填写。parse 端逗号/空格都能拆，故往返无损。"""
    groups = {}
    for name, path in mapping.items():
        groups.setdefault(path, []).append(name)
    lines = []
    for path in sorted(groups):
        lines.append("%s:" % path)
        lines.append("    " + ", ".join(sorted(groups[path])))
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if mapping else "")


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
    internal = cone.find_internal_inputs(node, bindings, resolver)
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
            # mux 跨界展开失败(环/超深，R38) → 回退非展开：mux 输出当叶子 force 衔接网(=force 模式/今天)，
            # 不让整信号崩。仅当确有 mux 输出内部输入时走这条；否则维持原 raise(纯 logic 兜不住)。
            # 用 _is_mux_out_binding：配了探针前缀的 mux 输出 found_in 是 'prefixed-wire' 而非 'mux-output'，
            # 也要算进来（否则配前缀的 mux 输出 cone 失败时不回退、直接崩）。
            if any(cone._is_mux_out_binding(bindings.get(l), resolver) for l in internal):
                if chain_out is not None:
                    del chain_out[:]
                if fallback_notes is not None:
                    fallback_notes.append("%s: mux 跨界展开失败(环/超深)，回退 force 衔接网" % sig.out_name)
                return node, bindings, False
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


def _regmap_dup_warning(used_letters, bindings, wb):
    """信号若用到 regmap 同名重复定义的字段(已按【首个】采纳)→ 返回告警串，否则 None。
    used_letters: 该信号实际用到的变量字母集合(logic 用 E.collect_vars(node)、mux 用 used_vars)。

    背景(2026-06-12 d_bt_lp_pmu_test_en 实证)：表里同一字段名可能在 regmap 出现两次、落在不同
    寄存器/位(如 0x32/bit12 与 0x36/bit1)。read_regmap 现按首个采纳(与表 VLOOKUP 一致)，但首个未必
    一定是设计意图——故凡命中重复就显式告警(块顶 ⚠ + CLI/汇总)，不静默。仅对【实际从 regmap 取值】
    (found_in=='regmap')的输入告警；tmm 命中时 regmap 重复不影响结果，不扰民。"""
    dups = getattr(wb, "regmap_dups", None)
    if not dups:
        return None

    def _fmt(e):
        a = ("0x%X" % e.address) if e.address is not None else "?"
        if e.bit_lsb is None:
            bit = "bit?"
        elif e.bit_msb == e.bit_lsb:
            bit = "bit%d" % e.bit_lsb
        else:
            bit = "bit[%d:%d]" % (e.bit_msb, e.bit_lsb)
        rn = (e.reg_name or "").strip()
        return ("%s@%s %s" % (rn, a, bit)).strip() if rn else ("%s %s" % (a, bit))

    msgs, seen = [], set()
    for ltr in used_letters:
        b = bindings.get(ltr)
        if b is None or not getattr(b, "resolved", False) or b.found_in != "regmap":
            continue
        low = b.base.lower()
        if low in dups and low not in seen:
            seen.add(low)
            ents = dups[low]
            msgs.append("regmap 重名: %s 在 %d 处定义 [%s]，已按首个采纳 (%s)；如不符请核对 regmap 或由 SE 消重"
                        % (b.base, len(ents), ", ".join(_fmt(e) for e in ents), _fmt(ents[0])))
    return " | ".join(msgs) if msgs else None


def _inject_block_warnings(sig, node, bindings, lines, meta, opts, wb, is_topout=False, aid=None):
    """块顶 ⚠ 注入(iddq_skipped/regmap_dup/supplement/selfaudit) + claims 收集——source-agnostic。

    从 build 的 logic 循环抽出(重构 S0b，2026-06-25)，logic/mux(build) 与 register/dft 改名根
    (topout passthrough，S1 接) 共用同一注入圈，杜绝缝B『裸渲染绕过 build 后处理』。
    ⚠ 注入【顺序与 build 原序逐字节一致】(依次 prepend：iddq_skipped→regmap→supplement→selfaudit，
    故输出由上到下 = selfaudit/supplement/regmap/iddq)。node=None(mux 根无单一 AST)时跳过需 node 的检查。
    is_topout(S1)：探针来自 Topout 路径(顶层真名)→ claims provenance 标 'topout'(M4)；
    build 的 logic/mux 循环默认 False(逐字节不变，claims 不进 .sv)。
    aid(#7)：断言标号覆盖(Topout 行序)；None 则用 sig.assert_id。汇总告警/claims 的 aid 字段随之一致。
    返回 (lines, warnings{regmap/supplement/selfaudit:[(out_name,aid,msg)]}, claims)。"""
    _aid = aid if aid is not None else sig.assert_id
    warnings = {"regmap": [], "supplement": [], "selfaudit": []}
    if meta.get("iddq_skipped"):
        lines = ["// ⚠ %s" % meta["iddq_skipped"]] + lines
    _rmdup = _regmap_dup_warning(E.collect_vars(node), bindings, wb) if node is not None else None
    if _rmdup:
        lines = ["// ⚠ %s" % _rmdup] + lines
        warnings["regmap"].append((sig.out_name, _aid, _rmdup))
    if getattr(sig, "_is_supplement", False):
        _smsg = _supplement_warning(sig)
        lines = ["// ⚠ %s" % _smsg] + lines
        warnings["supplement"].append((sig.out_name, _aid, _smsg))
    _sa_env = E.Env({ltr: b.width for ltr, b in bindings.items() if b is not None})
    for _samsg in (_selfaudit_probe_self_ref(sig),
                   (_selfaudit_output_width(sig, node, _sa_env) if node is not None else None),
                   _selfaudit_rw_truncation(bindings)):
        if _samsg:
            lines = ["// ⚠ %s" % _samsg] + lines
            warnings["selfaudit"].append((sig.out_name, _aid, _samsg))
    claims = collect_claims(sig, bindings, probe_prefix_for(sig, opts), False,
                            _on_missing_for(sig, opts), is_topout=is_topout, aid=_aid)
    return lines, warnings, claims


def build(wb, opts):
    """
    返回 dict:
      blocks: list[(lines, stats)]
      selected / skipped / errors: 诊断
      stats: 汇总
    先应用 logic_overrides(合成「RTL 补充」信号替换/追加)，wb.logic swap-and-restore，不污染共享 wb。
    """
    _saved_logic = wb.logic
    wb.logic = _logic_with_overrides(wb, opts)
    try:
        return _build_core(wb, opts)
    finally:
        wb.logic = _saved_logic


def _build_core(wb, opts):
    resolver = R.Resolver(wb, force_overrides=opts.force_overrides,
                          rfwrite_overrides=opts.rfwrite_overrides,
                          default_kind=opts.default_kind,
                          wire_fallback=opts.wire_fallback,
                          wire_prefixes=opts.probe_prefixes,
                          cascade_mode=opts.cascade_mode,
                          append_to_logic=opts.append_to_logic,
                          append_to_mux=opts.append_to_mux,
                          suffix_override=opts.suffix_override)
    selected = select_signals(wb, opts)
    filtered_internal = filtered_internal_signals(wb, opts)   # 默认静默滤掉的内部节点(可见性用)
    blocks = []
    errors = []
    skipped = []        # 含不可驱动输入(wire兜底/未解析)的信号，默认跳过(与 VBA 一致)
    spec_conflicts = []  # mux 规格冲突跳过(同 case 不同源)——单列, 账目/报告/GUI 据此区别于普通跳过
    mux_warnings = []   # 照常生成但有提示的 mux 组（如 top_out=0 用裸名探针，可能要前缀）
    regmap_warnings = [] # 用到 regmap 同名重复字段的信号（已按首个采纳，块顶 ⚠ + 这里汇总）
    supplement_warnings = []  # 用了「RTL 补充逻辑」(Excel 缺失、手工补)的信号——块顶 ⚠ + 汇总，偏离纯 Excel 推导必显式
    selfaudit_warnings = []  # 生成期"自检闸门"命中(假绿可疑)：探针读回自身输入/K列漏标位宽/RW写值截断——块顶 ⚠ + 汇总
    claims = []  # 探针/force 网名声明清单(红区 scan_rtl 校验器输入契约，--export-claims 导出)
    cone_fallbacks = [] # cone 成环 → 回退 force 基名的信号（for_test 那招），可见性用
    n_total_vectors = 0
    n_total_neg = 0
    n_total_designer = 0     # designer 手填期望的用例数（其余正向用 auto_out 兜底）
    n_unresolved_signals = 0
    seen_labels = {}    # assert 标号 -> 首个出现的信号；查全局重复(重复=非法 SV，elaboration 失败)
    dup_labels = []

    for sig in selected:
        resolver.cascade_mode = opts.cascade_for(sig.out_name, is_mux=False)   # 级联模式 logic/单点
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

        # 用户「一键清空」(删空所有列)→ 空 override 列表(非 None) = 零用例：整个信号不产出，
        # 但列名字+原因(尊重清空意图、不静默、也不回退自动)。与 mux_cleared 行为对称。
        if override is not None and len(override) == 0:
            skipped.append((sig.out_name, sig.assert_id,
                            [("clear", sig.out_base, "用户已清空(零用例，本信号不产出测试)")]))
            continue

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

        # 本信号【已显式驱动】的输入基名集合——dft 门若已在其中(如 RTL 补充显式列了该 iddq、或
        # logic 行本就引用它)则不再当 DFT 门重复钉/补，去重（2026-06-12 SE 把 iddq 挪进 dft 页后实证）。
        _ibases = {b.base.lower() for b in bindings.values()
                   if b is not None and getattr(b, "base", None)}
        # item③ iddq DFT 态拍（第二十二轮）：被 dft 门控的 logic 输出补一条 DFT 拍（所有档）。
        # 仅自动向量路径补（override=用户全定制，不注入工具拍）；放在负向之后 → DFT 拍不被自动负向翻倍。
        if override is None:
            _dft_skip = _append_dft_vectors(sig.out_base.lower(), vecs, wb, resolver,
                                            input_bases=_ibases)
            if _dft_skip:
                meta["iddq_skipped"] = _dft_skip
            for i, v in enumerate(vecs):
                v.index = i
        # iddq 门=显式输入（2026-06-10）：被门控输出的每条向量 force 门到透传值（override
        # 路径也钉——门是环境驱动，与用户定制的内容正交；for_test 输入集同口径）
        pin_dft_gate(sig.out_base.lower(), vecs, wb, resolver, input_bases=_ibases)

        # 真·仅负向：只保留负向向量(编号不重排，与"全部"导出的 T 编号一致便于对照)；
        # 没有负向的信号整个不出现在产物里
        if opts.negative_vectors_only:
            vecs = [v for v in vecs if v.is_negative]
            if not vecs:
                continue

        # 全局 assert 标号唯一性检查：标号 = <R>_<test_label>，重复(同信号自定义名撞自动名、
        # 或两信号共用同一 R)在 SV 同一作用域里非法，会 elaboration 失败 → 收集并上报，不静默。
        _aid_ov = opts.assert_id_override.get(sig.out_name.lower()) if opts.assert_id_override else None
        aid = _aid_ov or sig.assert_id or "X"          # #7：Topout 行序命名覆盖源 Excel R
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
                                             counters=opts.sv_summary, assert_id=_aid_ov)
        # 块顶 ⚠ 注入(iddq_skipped/regmap_dup/supplement/selfaudit) + claims —— 抽成 source-agnostic
        # _inject_block_warnings(S0b)，与 topout passthrough 共用、杜绝缝B；注入顺序逐字节同原序。
        lines, _w, _c = _inject_block_warnings(sig, node, bindings, lines, meta, opts, wb,
                                               aid=_aid_ov)
        regmap_warnings.extend(_w["regmap"])
        supplement_warnings.extend(_w["supplement"])
        selfaudit_warnings.extend(_w["selfaudit"])
        stats["cone_expanded"] = expanded
        blocks.append((lines, stats))
        claims.extend(_c)
        n_total_vectors += stats["n_vectors"]
        n_total_neg += stats["n_negative"]
        n_total_designer += stats.get("n_designer", 0)
        if stats["unresolved"]:
            n_unresolved_signals += 1

    # ───────────── mux 页（2026-06-03 第九轮：mux 验证，与 logic 块同文件混排）─────────────
    n_logic_blocks = len(blocks)
    mux_selected = select_mux_groups(wb, opts)
    for grp in mux_selected:
        # 用户「一键清空」该 mux 信号 → 零用例(与覆盖度无关)：整组不产出，但列名字+原因(对称 logic 清空)。
        if grp.out_name.lower() in opts.mux_cleared:
            skipped.append((grp.out_name, grp.assert_id,
                            [("clear", _mux_ctrl_desc(grp), "用户已清空(零用例，本信号不产出测试)")]))
            continue
        resolver.cascade_mode = opts.cascade_for(grp.out_name, is_mux=True)   # 级联模式 mux/单点
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

        # 用户在 mux 真值表里删掉的【个别测试列】：按签名过滤正向向量（负向尚未追加，连带消失）。
        _dropped = opts.mux_dropped.get(grp.out_name.lower())
        if _dropped:
            vecs = [v for v in vecs if mux_assign_key(v.assignments) not in _dropped]
        # 注入用户手编/复制的 mux 测试列（第二十八轮，mux 与 logic 平级）：在删列过滤【之后】——避免
        # "复制某列又删原列"时同签名把用户副本一起误删，也让"删光自动列后仅余用户列"仍能产出。
        # 正向参与下面的 designer 期望/全局负向追加；负向用户列原样保留（与全局负向重叠的由去重处理）。
        _uvecs = opts.mux_user_vecs.get(grp.out_name.lower())
        if _uvecs:
            vecs = list(vecs) + [V.clone_vector(uv) for uv in _uvecs]
        if not vecs:
            skipped.append((grp.out_name, grp.assert_id,
                            [("clear", _mux_ctrl_desc(grp), "用户已删除全部测试列(零用例)")]))
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

        # item③ iddq DFT 态拍（mux 被门控输出，如 mixer2g_trim）：所有档都补；放负向之后。
        _dft_skip = _append_dft_vectors(grp.out_base.lower(), vecs, wb, resolver)
        if _dft_skip:
            meta["iddq_skipped"] = _dft_skip
        pin_dft_gate(grp.out_base.lower(), vecs, wb, resolver)   # iddq 门=显式输入(每条向量驱透传)
        vecs = _dedup_negatives(vecs)        # 全局负向 vs 用户逐 case 负向去重（无用户负向时恒等）
        for i, v in enumerate(vecs):
            v.index = i

        if opts.negative_vectors_only:
            vecs = [v for v in vecs if v.is_negative]
            if not vecs:
                continue

        # 全局 assert 标号唯一性：assert_mux<N>_T<n> 也纳入同一张查重表（与 logic 跨表查重）
        _maid_ov = opts.assert_id_override.get(grp.out_name.lower()) if opts.assert_id_override else None
        aid = _maid_ov or grp.assert_id          # #7：Topout 行序命名覆盖 mux<N>
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
                                             used_vars=exp["used_vars"], assert_id=_maid_ov)
        # top_out=0 且没配前缀：照常生成裸名探针，但在块顶留一句警告 + 汇总到 mux_warnings
        # （t1：Topout 路径 suppress_mux_bare_probe=True 时抑制——账目/报告已抑制此噪声、保持一致）
        out_warn = "" if opts.suppress_mux_bare_probe else mux_output_warning(grp, opts)
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
        # regmap 同名重复(已按首个采纳)：mux 控制/数据字段同样可能撞重名 → 块顶留 ⚠ + 汇总
        _rmdup = _regmap_dup_warning(exp.get("used_vars", ()), exp["bindings"], wb)
        if _rmdup:
            lines = ["// ⚠ %s" % _rmdup] + lines
            regmap_warnings.append((grp.out_name, grp.assert_id, _rmdup))
        # 生成期自检闸门(mux)：探针读回自身输入(检查1) / RW写值截断(检查3)。检查2(输出位宽)mux 不走
        # expr 求值，G 列漏标位宽的同类风险留待后续(需比 G 列宽 vs 各 case 数据宽)，本轮 logic 先做。
        for _samsg in (_selfaudit_probe_self_ref(grp),
                       _selfaudit_rw_truncation(exp["bindings"])):
            if _samsg:
                lines = ["// ⚠ %s" % _samsg] + lines
                selfaudit_warnings.append((grp.out_name, grp.assert_id, _samsg))
        stats["is_mux"] = True
        stats["scan_path"] = meta.get("scan_path")
        blocks.append((lines, stats))
        claims.extend(collect_claims(grp, exp["bindings"], probe_prefix_for(grp, opts), True,
                                     _on_missing_for(grp, opts), aid=_maid_ov))
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
        "n_regmap_warnings": len(regmap_warnings),
        "n_supplement": len(supplement_warnings),
        "n_selfaudit_warnings": len(selfaudit_warnings),
        # 默认静默过滤掉的 logic 内部节点（top_output=0）——拎出来给可见性
        "n_filtered_internal": len(filtered_internal),
    }
    return {"blocks": blocks, "selected": selected, "errors": errors,
            "skipped": skipped, "spec_conflicts": spec_conflicts,
            "mux_warnings": mux_warnings, "regmap_warnings": regmap_warnings,
            "supplement_warnings": supplement_warnings,
            "selfaudit_warnings": selfaudit_warnings,
            "claims": claims,
            "cone_fallbacks": cone_fallbacks,
            "filtered_internal": filtered_internal,
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
    # 与 build/report 同口径：账目也走「应用 RTL 补充后」的 logic 列表，纯新增的合成信号才不会漏账
    # (无补充时 _logic_with_overrides 返回 wb.logic 原对象 → 逐字节不变)。
    for sig in _logic_with_overrides(wb, opts):
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
    """期望来源（报告明细列）：负向 / DFT门 / designer 手填 / auto_out 兜底。"""
    if vec.is_negative:
        return "负向(故意填错)"
    if getattr(vec, "dft_pitch", False):
        return "DFT门(iddq=1→压0)"          # item③：期望来自 dft 门常量支，非 designer 手填(M3)
    if vec.designer_filled:
        return "designer手填"
    return "auto_out兜底"


def _input_meta(g, bindings):
    """report 真值表 inputs[] 的逐输入元数据。除显示用的 label/letters 外，带上回填 for_test
    所需的驱动元数据：kind(RO/RW)、寄存器地址 addr、bit 位 reg_lsb/reg_msb、force 网名 wire、
    位宽 width。纯附加字段——HTML/CSV/Excel 只读 label/letters，不受影响。"""
    b = bindings.get(g.get("rep"))
    return {
        "label": g["label"],
        "letters": ",".join(g.get("xl_letters") or g.get("letters") or []),
        "base": (b.base if (b and b.base) else g.get("base", "")),
        "kind": (b.kind if b else g.get("kind", "")),
        "ro": bool(b and b.kind == "RO"),
        "addr": (b.address if b else None),
        "reg_lsb": (b.reg_lsb if b else None),
        "reg_msb": (b.reg_msb if b else None),
        # slice_*：该输入引用的是字段的哪几位(如 x[15:1])——bit 拆分字段回填 for_test 时
        # K/L/M 要按 reg_lsb+slice 偏移显示真实寄存器 bit 位(否则两片显示成同一整字段)
        "slice_lsb": (b.slice_lsb if b else None),
        "slice_msb": (b.slice_msb if b else None),
        "wire": (b.wire if b else ""),
        "width": g.get("width", 1),
    }


def _eff_aid(opts, obj):
    """报告/产物 R 列断言号：assert_id_override 命中(Topout 行序 #7)则用它，否则源对象 assert_id。
    默认无 override → 源 assert_id、逐字节不变。logic/mux 报告与 .sv 标号据此一致。"""
    ov = getattr(opts, "assert_id_override", None)
    if ov:
        v = ov.get(str(getattr(obj, "out_name", "")).lower())
        if v:
            return v
    return obj.assert_id


def report(wb, opts):
    """
    生成"给人看"的测试用例清单（结构化），CLI 负责写成 CSV/HTML。
    返回 {
      "summary": [每信号一行],
      "detail":  [每条用例一行]（每行带 T编号/_NEG/期望/force/...，便于 Ctrl+F），
      "tables":  [每信号一个纵向真值表]（输入带位宽做行、各测试做列，供 HTML ② 段）,
    }
    与 build() 同口径先应用 logic_overrides，wb.logic swap-and-restore，不污染共享 wb（报告与产出一致）。
    """
    _saved_logic = wb.logic
    wb.logic = _logic_with_overrides(wb, opts)
    try:
        return _report_core(wb, opts)
    finally:
        wb.logic = _saved_logic


def _report_core(wb, opts):
    resolver = R.Resolver(wb, force_overrides=opts.force_overrides,
                          rfwrite_overrides=opts.rfwrite_overrides,
                          default_kind=opts.default_kind,
                          wire_fallback=opts.wire_fallback,
                          wire_prefixes=opts.probe_prefixes,
                          cascade_mode=opts.cascade_mode,
                          append_to_logic=opts.append_to_logic,
                          append_to_mux=opts.append_to_mux,
                          suffix_override=opts.suffix_override)
    sigs = select_signals(wb, opts)
    summary, detail, tables = [], [], []
    for sig in sigs:
        resolver.cascade_mode = opts.cascade_for(sig.out_name, is_mux=False)   # 级联模式 logic/单点
        chain = []        # cone 信号的展开链(本行+逐层代入的上游行)，HTML 真值表上方显示
        try:
            node, bindings, _expanded = expand_signal(wb, resolver, sig, chain_out=chain)
        except (E.ExprError, cone.ConeError) as ex:
            summary.append({"R": _eff_aid(opts, sig), "signal": sig.out_name, "owner": sig.owner,
                            "type": sig.suffix, "top": sig.top_output, "expr": sig.expr,
                            "n_tests": 0, "n_neg": 0, "control": "", "data": "",
                            "unresolved": "", "error": "表达式解析/展开失败: %s" % ex})
            continue
        used = E.collect_vars(node)
        # 本信号已显式驱动的输入基名（dft 门去重用，与 build 同口径）
        _ribases = {b.base.lower() for b in bindings.values()
                    if b is not None and getattr(b, "base", None)}
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
                summary.append({"R": _eff_aid(opts, sig), "signal": sig.out_name, "owner": sig.owner,
                                "type": sig.suffix, "top": sig.top_output, "expr": sig.expr,
                                "n_tests": 0, "n_neg": 0, "control": "", "data": "",
                                "unresolved": "", "error": "向量生成失败: %s" % ex})
                continue
            if _neg_enabled_for(sig, opts):
                vecs = V.add_negatives(vecs, mode=opts.neg_mode, which=opts.neg_which,
                                       fixed_value=opts.neg_value)
            # item③ DFT 拍：报告与 .sv 双轨一致（同 build 的 logic 挂点；override 路径不注入）
            _lskip = _append_dft_vectors(sig.out_base.lower(), vecs, wb, resolver,
                                         input_bases=_ribases)
            if _lskip:
                meta["iddq_skipped"] = _lskip   # 缺口可见(M2)：报告 error 列透出（.sv 已有 // ⚠）
        # iddq 门=显式输入（与 build 同口径）；返回的绑定供报告 inputs 行 + for_test 回填。
        # 门若已是显式输入(RTL 补充列了该 iddq / logic 行引用它)则去重，不重复出门行（与 build 同口径）。
        _lpin = pin_dft_gate(sig.out_base.lower(), vecs, wb, resolver, input_bases=_ribases)
        groups = V.input_groups(node, bindings)
        # 输入行显示顺序 = for_test 行序（有样例组时）/ 寄存器地址+bit 位（默认，同 for_test 生成规则）
        def _lbind(e):
            return bindings.get(e[1].get("rep")) if e[0] == "g" else _lpin[0]
        _lentries = [("g", g) for g in groups] + ([("gate", None)] if _lpin else [])
        _lentries = fortest_order_entries(
            _lentries, wb, sig.out_base,
            lambda e: ((excel_model._strip_width(e[1].get("base") or e[1].get("label") or "")[0]
                        .lower()) if e[0] == "g" else _lpin[0].base.lower()),
            key_fn=lambda e: ((_lbind(e).address, _lbind(e).reg_lsb)
                              if _lbind(e) is not None else (None, None)))
        table = {"R": _eff_aid(opts, sig), "signal": sig.out_name, "owner": sig.owner,
                 "type": sig.suffix, "expr": sig.expr,
                 # RTL 补充逻辑(Excel 缺，手工补)的告警串——非空则 HTML 报告该表挂 banner、CSV/Excel 也带
                 "supplement": (_supplement_warning(sig)
                                if getattr(sig, "_is_supplement", False) else ""),
                 # is_logic：本表是 logic cone 真值表(回填 for_test 只处理它，mux 表结构不同跳过)
                 "is_logic": True, "out_width": sig.out_width or 1,
                 # chain = cone 展开链：[{"out","expr","subst"},...]，非 cone 信号为空 list
                 "chain": chain,
                 # letters = 该输入的 Excel 来源坐标(普通信号=A/B/C…；cone 展开叶子=
                 # "上游行名.字母"如 pll_n1.A)，让报告里的真值表能对回表达式/Excel
                 # 逐输入还带回填 for_test 用的驱动元数据(addr/RO/bit/wire)，见 _input_meta
                 # iddq 门也是输入行（2026-06-10）：HTML 真值表 + for_test 回填都带上（RO 网、
                 # 无地址、宽 1，回填走 B=网名/C=16'h0 路径）；次序已按 for_test 行序排好
                 "inputs": [(_input_meta(e[1], bindings) if e[0] == "g" else {
                     "label": _lpin[0].base, "letters": "dft门", "base": _lpin[0].base,
                     "kind": "RO", "ro": True, "addr": None, "reg_lsb": None, "reg_msb": None,
                     "slice_lsb": None, "slice_msb": None, "wire": _lpin[0].wire, "width": 1})
                            for e in _lentries], "tests": []}
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
            # iddq 门输入值：功能向量=透传值；DFT 拍=force 的常量支值（与 .sv 实际驱动一致）
            _gv = (None if not _lpin else
                   ((1 - _lpin[1]) if getattr(vec, "dft_pitch", False) else _lpin[1]))
            _rawrow = [(bv.get(e[1]["key"], 0) if e[0] == "g" else _gv) for e in _lentries]
            table["tests"].append({
                "name": W.test_label(vec),
                "neg": vec.is_negative,
                "values": [_fmt_cell(_rawrow[i], e[1]["width"] if e[0] == "g" else 1)
                           for i, e in enumerate(_lentries)],
                # raw = 逐输入原始整数值(回填 for_test 的 T 向量列/G/H 计算要用；values 是格式化串)
                "raw": _rawrow,
                # auto_out = 表达式计算值；expected = 进 .sv 的对比值(designer 手填 > auto_out 兜底 > 负向错值)
                "auto_out": _fmt_cell(vec.exp_value, vec.exp_width),
                "expected": _fmt_cell(vec.asserted_value, vec.exp_width),
                "exp_num": vec.asserted_value,      # 进 .sv 的期望整数值(回填 for_test 输出行/E 列用)
                "designer_filled": vec.designer_filled,
                "correct": _fmt_cell(vec.exp_value, vec.exp_width),
                # 数值/位宽（HTML「真值表检查」tab 的 JS 比对用）
                "auto_num": vec.exp_value, "width": vec.exp_width,
                "force": force_str, "rfwrite": write_str,
                # writes = compute_drives 的结构化 RF_WRITE(权威：已处理 bit 拆分字段/位宽裁剪)。
                # 回填 for_test 的 B/C(寄存器写值)/H 直接用它，避免自己重算 regval 出错(与 .sv 一致)。
                "writes": writes,
            })
            detail.append({
                "R": _eff_aid(opts, sig), "signal": sig.out_name, "owner": sig.owner,
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
            "R": _eff_aid(opts, sig), "signal": sig.out_name, "owner": sig.owner,
            "type": sig.suffix, "top": sig.top_output, "expr": sig.expr,
            "n_tests": len(vecs), "n_neg": sum(1 for v in vecs if v.is_negative),
            "control": ",".join(meta.get("control", [])), "data": ",".join(meta.get("data", [])),
            "unresolved": ";".join(sorted(unresolved_bases)),
            "supplement": (_supplement_warning(sig)
                           if getattr(sig, "_is_supplement", False) else ""),
            # 用户一键清空(空 override) → 报告也透出原因(与 build 跳过、mux 清空同口径)
            "error": ("用户已清空(零用例，本信号不产出测试)"
                      if (override is not None and len(override) == 0)
                      else ("⚠覆盖缺口: %s" % meta["iddq_skipped"]) if meta.get("iddq_skipped")
                      else ""),
        })

    # ───────────── mux 组（与 build() 双轨同步——报告里必须能看到 .sv 里的每个 mux 块）─────────────
    mux_groups = select_mux_groups(wb, opts)
    mux_verif_rows = []
    for grp in mux_groups:
        resolver.cascade_mode = opts.cascade_for(grp.out_name, is_mux=True)   # 级联模式 mux/单点
        exp = mux_gen.expand_mux_group(wb, resolver, grp)
        expr_text = _mux_expr_text(grp)
        base_row = {"R": _eff_aid(opts, grp), "signal": grp.out_name, "owner": grp.owner,
                    "type": "mux", "top": grp.top_output, "expr": expr_text}

        # 与 build() 同口径的跳过判定（报告里以 error 列呈现原因，不是消失）
        skip_reason = ""
        out_warn = mux_output_warning(grp, opts)   # top_out=0 裸名探针提示（不阻断，照常生成）
        vecs, meta = [], {}
        blockers = mux_prefix_risks(grp, exp, opts)
        if grp.out_name.lower() in opts.mux_cleared:
            skip_reason = "用户已清空(零用例，本信号不产出测试)"   # 与 build() 对称
        elif exp["issues"] and not opts.include_risky:
            skip_reason = "; ".join(exp["issues"])
        elif blockers and not opts.include_risky:
            skip_reason = "; ".join(r[2] for r in blockers)
        else:
            mux_mode = opts.mux_cov_mode(grp.out_name)
            vecs, meta = mux_gen.make_mux_vectors(grp, exp, mode=mux_mode,
                                                  max_tests=opts.max_tests,
                                                  data_overrides=mux_data_for(opts, grp))
            # 过滤顺序与 build() 一致：先判生成器是否本就没向量(给真实原因)，再按 dropped 过滤——
            # 否则"生成器空 + 残留旧档 dropped"会被误标成"用户删了全部"(自审 Finding 1)。
            if meta.get("value_collision"):
                skip_reason = ("数据寄存器位宽装不下 %d 个 case 的互异值——选错路也测不出(假绿)"
                               % len(grp.cases))
                vecs = []
            elif not vecs:
                skip_reason = _empty_vector_reason(meta)
            else:
                _dropped = opts.mux_dropped.get(grp.out_name.lower())
                if _dropped:                               # 用户删掉的个别测试列(与 build() 同口径过滤)
                    vecs = [v for v in vecs if mux_assign_key(v.assignments) not in _dropped]
                # 注入用户手编/复制的 mux 测试列（与 build() 双轨同步；删列过滤之后，理由同 build）
                _uvecs = opts.mux_user_vecs.get(grp.out_name.lower())
                if _uvecs:
                    vecs = list(vecs) + [V.clone_vector(uv) for uv in _uvecs]
                if not vecs:                               # 删光所有测试列 → 零用例(给原因，与 build 对称)
                    skip_reason = "用户已删除全部测试列(零用例)"
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
                    _rskip = _append_dft_vectors(grp.out_base.lower(), vecs, wb, resolver)
                    if _rskip:
                        meta["iddq_skipped"] = _rskip
                    vecs = _dedup_negatives(vecs)          # 与 build 同口径：全局/逐 case 负向去重
                    for i, v in enumerate(vecs):
                        v.index = i
        # iddq 门=显式输入（与 build 同口径，skip 路径 vecs 为空时无害）
        _mpin = pin_dft_gate(grp.out_base.lower(), vecs, wb, resolver)

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
            "R": _eff_aid(opts, grp), "signal": grp.out_name, "owner": grp.owner,
            "type": "mux", "top": grp.top_output,
            "status": verif_status,
            "detail": skip_reason or warn_full, "out_net": "`%s.%s" % (W.ENV, grp.rtl_name),
        })
        if skip_reason:
            continue

        # ── case 选择表（tables 段，kind='mux'）：行=控制+各数据寄存器(+DFT门)，列=测试 T ──
        used = exp["used_vars"]
        # 输入行显示顺序 = for_test 行序（有样例组时）/ 寄存器地址+bit 位（默认，同 for_test 生成规则）
        _mentries = [("key", k) for k in used] + ([("gate", None)] if _mpin else [])
        _mentries = fortest_order_entries(
            _mentries, wb, grp.out_base,
            lambda e: ((exp["bindings"][e[1]].base or "").lower() if e[0] == "key"
                       else _mpin[0].base.lower()),
            key_fn=lambda e: ((exp["bindings"][e[1]].address, exp["bindings"][e[1]].reg_lsb)
                              if e[0] == "key" else (_mpin[0].address, _mpin[0].reg_lsb)))
        inp_rows = []
        for ent in _mentries:
            if ent[0] == "gate":
                # iddq 门作为输入行（2026-06-10，与 logic 表同口径）：HTML 真值表带上
                inp_rows.append({"label": _mpin[0].base, "letters": "dft门(force)",
                                 "ro": True, "addr": None, "width": 1,
                                 "base": _mpin[0].base, "wire": _mpin[0].wire,
                                 "reg_lsb": None, "reg_msb": None,
                                 "slice_msb": None, "slice_lsb": None})
                continue
            key = ent[1]
            b = exp["bindings"][key]
            label = b.base + ("[%d:0]" % (b.width - 1) if b.width > 1 else "")
            # 角色判断统一走 mux_gen.key_role（多控制 c1:/c2:、上游配方 m<N>.* 都认）
            tag = {"ctrl": "ctrl", "data": "data", "upstream": "上游mux"}[mux_gen.key_role(key)]
            # for_test 回填(块B 陷阱③,2026-06-23)：补输入元数据，让 fortest_writer 像 logic 表一样
            # 渲染 mux 输入行(RW→地址/写值、RO/线控→force 网名)。额外键，HTML 报告不读、逐字节不变。
            inp_rows.append({"label": label, "letters": "%s(%s)" % (key, tag),
                             "ro": (b.kind == "RO"), "addr": b.address,
                             "width": b.width, "base": b.base, "wire": b.wire,
                             "reg_lsb": b.reg_lsb, "reg_msb": b.reg_msb,
                             "slice_msb": b.slice_msb, "slice_lsb": b.slice_lsb})
        table = {"R": _eff_aid(opts, grp), "signal": grp.out_name, "owner": grp.owner,
                 "type": "mux", "expr": expr_text, "kind": "mux",
                 "inputs": inp_rows, "tests": []}
        for vec in vecs:
            forces, writes, _unres = W.compute_drives(vec, exp["bindings"], used)
            _gv = (None if not _mpin else
                   ((1 - _mpin[1]) if getattr(vec, "dft_pitch", False) else _mpin[1]))
            table["tests"].append({
                "name": W.test_label(vec),
                "neg": vec.is_negative,
                "values": [(_fmt_cell(vec.assignments.get(e[1], 0), exp["bindings"][e[1]].width)
                            if e[0] == "key" else _fmt_cell(_gv, 1)) for e in _mentries],
                "auto_out": _fmt_cell(vec.exp_value, vec.exp_width),
                "expected": _fmt_cell(vec.asserted_value, vec.exp_width),
                "designer_filled": vec.designer_filled,
                "correct": _fmt_cell(vec.exp_value, vec.exp_width),
                "auto_num": vec.exp_value, "width": vec.exp_width,
                # for_test 回填(块B 陷阱③)：与 logic 表同键——raw(逐输入整数)/exp_num(期望整数)/
                # writes(compute_drives 权威 RF_WRITE)。让 fortest_writer include_mux=True 渲染 mux 表。
                "raw": [(vec.assignments.get(e[1], 0) if e[0] == "key" else (_gv or 0))
                        for e in _mentries],
                "exp_num": vec.asserted_value,
                "writes": writes,
                # DFT 拍 iddq 门 force 在 extra_forces（同 logic 路），报告 force 列带上以与 .sv 一致
                "force": "; ".join(
                    ["%s=%s" % (f["wire"], f["hex"]) for f in forces]
                    + ["%s=%s" % (wl, W.fmt_bin(wv, ww))
                       for (wl, wv, ww) in (getattr(vec, "extra_forces", None) or [])]),
                "rfwrite": "; ".join("%s=%s" % (w["addr"], w["hex"]) for w in writes),
            })
            detail.append({
                "R": _eff_aid(opts, grp), "signal": grp.out_name, "owner": grp.owner,
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
        resolver.cascade_mode = opts.cascade_for(sig.out_name, is_mux=False)   # 级联模式 logic/单点
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
            "R": _eff_aid(opts, sig), "signal": sig.out_name, "owner": sig.owner,
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
        if eff_opts.include_risky:
            # 强制生成模式（2026-06-10 Hi1108：用户要实测"这设计到底要不要前缀"）：照常生成
            # 裸名 force，状态仍 needs-prefix(橙) 但不阻断——文案如实说"已强制生成，待仿真验证"
            return {"status": "needs-prefix", "inputs": rows, "out_net": out_net,
                    "error": "已强制生成(缺前缀，裸名 force——仿真过=此设计不需前缀；"
                             "CUVUNF 则跑 scan_rtl 配前缀)：" + "; ".join(r[2] for r in blockers),
                    "blocking": False, "cone": False}
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
                          cascade_mode=opts.cascade_mode,
                          append_to_logic=opts.append_to_logic,
                          append_to_mux=opts.append_to_mux,
                          suffix_override=opts.suffix_override)
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
        resolver.cascade_mode = opts.cascade_for(sig.out_name, is_mux=False)   # 级联模式 logic/单点
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
