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
                 top_output_only=False, types=None):
        self.owners = _norm_owner_set(owners)
        self.signals = _norm_set(signals)
        self.signal_regex = signal_regex
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


def select_signals(wb, opts):
    """按 owner / 名称 / 正则 / top_output / 类型 过滤 logic 信号。"""
    import re
    rx = re.compile(opts.signal_regex, re.I) if opts.signal_regex else None
    out = []
    for sig in wb.logic:
        if opts.owners is not None and _ws(sig.owner) not in opts.owners:
            continue
        if not _name_matches(sig, opts.signals):
            continue
        if rx and not (rx.search(sig.out_name) or rx.search(sig.out_base)):
            continue
        if opts.top_output_only and str(sig.top_output).strip() not in ("1", "1.0", "True", "true"):
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
                          default_kind=opts.default_kind)
    selected = select_signals(wb, opts)
    blocks = []
    errors = []
    n_total_vectors = 0
    n_total_neg = 0
    n_unresolved_signals = 0

    for sig in selected:
        try:
            node = E.parse(sig.expr)
        except E.ExprError as ex:
            errors.append((sig.out_name, sig.assert_id, "表达式解析失败: %s" % ex))
            continue
        bindings = resolver.resolve_signal_inputs(sig)
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

        lines, stats = W.render_signal_block(sig, bindings, vecs, meta)
        blocks.append((lines, stats))
        n_total_vectors += stats["n_vectors"]
        n_total_neg += stats["n_negative"]
        if stats["unresolved"]:
            n_unresolved_signals += 1

    summary = {
        "n_logic_rows": len(wb.logic),
        "n_selected": len(selected),
        "n_generated": len(blocks),
        "n_vectors": n_total_vectors,
        "n_negative": n_total_neg,
        "n_parse_errors": len(errors),
        "n_unresolved_signals": n_unresolved_signals,
        "tmm_fields": len(wb.tmm),
        "regmap_signals": len(wb.regmap),
    }
    return {"blocks": blocks, "selected": selected, "errors": errors, "summary": summary}


def render(result, header_info=None):
    return W.render_file(result["blocks"], header_info=header_info)


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
                          default_kind=opts.default_kind)
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

    # 2) 逐输入解析分类
    kinds = {"RO": 0, "RW": 0, "UNKNOWN": 0}
    wide_inputs = []        # >16bit 的输入（驱动会截断）
    unresolved = []         # (信号, 字母, 基名, note)
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
            kinds[b.kind if b.kind in kinds else "UNKNOWN"] += 1
            if b.width > 16:
                wide_inputs.append((sig.out_name, ltr, b.base, b.width, b.kind))
            if not b.resolved:
                unresolved.append((sig.out_name, ltr, b.base, b.note))

    return {
        "n_signals": len(sigs),
        "tmm_type_raw": dict(sorted(tmm_types.items(), key=lambda kv: -kv[1])),
        "regmap_type_raw": dict(sorted(rm_types.items(), key=lambda kv: -kv[1])),
        "tmm_dig_top_pin": dict(sorted(tmm_pins.items(), key=lambda kv: -kv[1])),
        "input_kinds": kinds,
        "wide_inputs": wide_inputs,
        "unresolved": unresolved,
    }
