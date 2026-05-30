# -*- coding: utf-8 -*-
"""
generator.py — 编排层：装载 Excel → 筛选信号 → 解析输入 → 生成向量(+负向) → 渲染 .sv。
CLI 与（未来的）GUI 共用此后端。
"""

from . import expr as E
from . import excel_model
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
                 vector_overrides=None):
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


def _neg_enabled_for(sig, opts):
    if opts.neg_all:
        return True
    if opts.neg_signals is None:
        return False
    return sig.out_name.lower() in opts.neg_signals or sig.out_base.lower() in opts.neg_signals


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
                          wire_fallback=opts.wire_fallback)
    selected = select_signals(wb, opts)
    blocks = []
    errors = []
    skipped = []        # 含不可驱动输入(wire兜底/未解析)的信号，默认跳过(与 VBA 一致)
    n_total_vectors = 0
    n_total_neg = 0
    n_unresolved_signals = 0
    seen_labels = {}    # assert 标号 -> 首个出现的信号；查全局重复(重复=非法 SV，elaboration 失败)
    dup_labels = []

    for sig in selected:
        try:
            node = E.parse(sig.expr)
        except E.ExprError as ex:
            errors.append((sig.out_name, sig.assert_id, "表达式解析失败: %s" % ex))
            continue
        bindings = resolver.resolve_signal_inputs(sig)

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

        # 全局 assert 标号唯一性检查：标号 = <R>_<test_label>，重复(同信号自定义名撞自动名、
        # 或两信号共用同一 R)在 SV 同一作用域里非法，会 elaboration 失败 → 收集并上报，不静默。
        aid = sig.assert_id or "X"
        for v in vecs:
            lbl = "%s_%s" % (aid, W.test_label(v))
            if lbl in seen_labels:
                dup_labels.append((lbl, seen_labels[lbl], sig.out_name))
            else:
                seen_labels[lbl] = sig.out_name

        lines, stats = W.render_signal_block(sig, bindings, vecs, meta, comments=opts.comments)
        blocks.append((lines, stats))
        n_total_vectors += stats["n_vectors"]
        n_total_neg += stats["n_negative"]
        if stats["unresolved"]:
            n_unresolved_signals += 1

    summary = {
        "n_logic_rows": len(wb.logic),
        "n_selected": len(selected),
        "n_generated": len(blocks),
        "n_skipped": len(skipped),
        "n_vectors": n_total_vectors,
        "n_negative": n_total_neg,
        "n_parse_errors": len(errors),
        "n_unresolved_signals": n_unresolved_signals,
        "n_dup_labels": len(dup_labels),
        "tmm_fields": len(wb.tmm),
        "regmap_signals": len(wb.regmap),
    }
    return {"blocks": blocks, "selected": selected, "errors": errors,
            "skipped": skipped, "dup_labels": dup_labels, "summary": summary}


def render(result, header_info=None, comments=False):
    return W.render_file(result["blocks"], header_info=header_info, comments=comments)


def analyze_signal(resolver, sig):
    """单信号解析画像（GUI debug 用）：返回 status + 每输入的 force/RF_WRITE net + 输出 net。
    status: clean / wire-fallback / unresolved / parse-err —— 用于挑出可能导致 elaboration 失败的信号。
    """
    out_net = "`%s.%s" % (W.ENV, sig.out_name)
    try:
        node = E.parse(sig.expr)
    except E.ExprError as ex:
        return {"status": "parse-err", "inputs": [], "out_net": out_net, "error": str(ex)}
    used = E.collect_vars(node)
    bindings = resolver.resolve_signal_inputs(sig)
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
        elif b.found_in == "wire" and status == "clean":
            status = "wire-fallback"
    return {"status": status, "inputs": rows, "out_net": out_net, "error": ""}


def _fmt_cell(val, width):
    """报告真值表单元格取值显示：1 位→0/1；多位→0xN（与 GUI 编辑器一致）。"""
    if width and width <= 1:
        return str(val & 1)
    return "0x%X" % val


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
                          wire_fallback=opts.wire_fallback)
    sigs = select_signals(wb, opts)
    summary, detail, tables = [], [], []
    for sig in sigs:
        try:
            node = E.parse(sig.expr)
        except E.ExprError as ex:
            summary.append({"R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
                            "type": sig.suffix, "top": sig.top_output, "expr": sig.expr,
                            "n_tests": 0, "n_neg": 0, "control": "", "data": "",
                            "unresolved": "", "error": "表达式解析失败: %s" % ex})
            continue
        bindings = resolver.resolve_signal_inputs(sig)
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
                 # letters = 该输入对应的表达式变量(A/B/C…)，让报告里的真值表能对上表达式
                 "inputs": [{"label": g["label"], "letters": ",".join(g.get("letters") or [])}
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
                "values": [_fmt_cell(bv.get(g["base"].lower(), 0), g["width"]) for g in groups],
                "expected": _fmt_cell(vec.asserted_value, vec.exp_width),
                "correct": _fmt_cell(vec.exp_value, vec.exp_width),
                "force": force_str, "rfwrite": write_str,
            })
            detail.append({
                "R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
                "type": sig.suffix, "expr": sig.expr,
                "test": W.test_label(vec),
                "neg": "是" if vec.is_negative else "",
                "expected": W.fmt_bin(vec.asserted_value, vec.exp_width),
                "correct": W.fmt_bin(vec.exp_value, vec.exp_width) if vec.is_negative else "",
                "force": force_str, "rfwrite": write_str,
                "note": (vec.note if vec.is_negative else
                         ("; ".join("%s:%s" % (b or l, n) for (l, b, n) in unres) if unres else "")),
            })
        out_w = sig.out_width or 1
        table["exp_label"] = "期望(out)%s" % ("[%d:0]" % (out_w - 1) if out_w > 1 else "")
        tables.append(table)
        summary.append({
            "R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
            "type": sig.suffix, "top": sig.top_output, "expr": sig.expr,
            "n_tests": len(vecs), "n_neg": sum(1 for v in vecs if v.is_negative),
            "control": ",".join(meta.get("control", [])), "data": ",".join(meta.get("data", [])),
            "unresolved": ";".join(sorted(unresolved_bases)), "error": "",
        })

    # ── 可验证性（取代旧 GUI"覆盖诊断"按钮）：逐信号给健康度 + 风险输入说明 ──
    verif = {"counts": {"clean": 0, "wire-fallback": 0, "unresolved": 0, "parse-err": 0},
             "signals": []}
    for sig in sigs:
        a = analyze_signal(resolver, sig)
        st = a["status"]
        verif["counts"][st] = verif["counts"].get(st, 0) + 1
        risky = [i for i in a["inputs"] if (not i["resolved"]) or i["found_in"] == "wire"]
        risky_str = "; ".join(
            "%s=%s(%s)" % (i["letter"], i["base"], "未解析" if not i["resolved"] else "wire兜底")
            for i in risky)
        verif["signals"].append({
            "R": sig.assert_id, "signal": sig.out_name, "owner": sig.owner,
            "type": sig.suffix, "top": sig.top_output, "status": st,
            "detail": risky_str or a.get("error", ""), "out_net": a.get("out_net", ""),
        })
    return {"summary": summary, "detail": detail, "tables": tables, "verifiability": verif}


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
                          wire_fallback=opts.wire_fallback)
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
            elif b.kind == "RO" and b.found_in == "logic":
                cats["force_chained"] += 1
            elif b.kind == "RO" and b.found_in == "wire":
                cats["force_wire"] += 1
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
