# -*- coding: utf-8 -*-
"""
pageviews.py — 【页本地·不 cone】子视图模型（2026-06-24，additive 新增）。

Topout 视图 = 以顶层要验信号为根，跨页 cone 展到源寄存器。
子视图(logic / mux / dft / iddq) = 以【某一页自己的每一行】为根，**只看本模块的输入输出、不 cone**：
  · 输入引用『上游算出来的网』(另一 logic/mux 输出)时，**直接 force 那根衔接网**(级联=force 模式)，
    绝不递归代入上游表达式 —— 这就是『不 cone』。寄存器型输入仍按 RF_WRITE 一层解析(非递归)。
  · 每行 → 真值表(行=输入/输出，列=测试)，与 Topout 视图同一渲染口径(inputs/tests/auto_label/exp_label)。

完全复用既有引擎(expr.parse / resolver / vectors.generate_vectors / mux_gen)；不碰旧 generator.build/report，
也不碰 topout.py(cone 路径)。dft/iddq 页由 excel_model.read_logiclike_page 读成 LogicSignal(D=输出/E=式/A-C=输入)。
"""

from . import excel_model as M
from . import expr as E
from . import generator as G
from . import mux_gen
from . import resolver as R
from . import sv_writer as W
from . import vectors as V

# 子视图页名（ISO/level_shift 结构特殊，暂不在此列；按需再扩，additive）
PAGES = ("logic", "mux", "dft", "iddq")
PAGE_LABEL = {"logic": "logic 视图", "mux": "mux 视图", "dft": "dft 视图", "iddq": "iddq 视图"}
PAGE_DESC = {
    "logic": "logic 页每一行一个输出 = 表达式(A?C:B 等)，输入按本页声明直接驱动，不 cone 展上游",
    "mux": "mux 页每组一个 N 选 1 输出，控制/数据按本页声明直接驱动，不 cone 展上游",
    "dft": "dft 页每行一个被观测输出 = 门控表达式(B?0:A 等)，DFT 接线网直接 force，不 cone",
    "iddq": "iddq 页每行一个漏电门控输出，按本页声明直接驱动，不 cone",
}


def page_signals(wb, page):
    """某一页的源对象清单（logic→LogicSignal / mux→MuxGroup / dft|iddq→LogicSignal 合成行）。"""
    if page == "logic":
        return list(wb.logic or [])
    if page == "mux":
        return list(getattr(wb, "mux", None) or [])
    if page == "dft":
        return list(getattr(wb, "dft_rows", None) or [])
    if page == "iddq":
        return list(getattr(wb, "iddq_rows", None) or [])
    return []


def page_available(wb, page):
    """该页是否有可显示的行（GUI 标签禁用/提示用）。"""
    return bool(page_signals(wb, page))


def _page_resolver(wb):
    """页本地用的干净 resolver：级联=force(绝不 cone 上游)、不补尾缀(探页声明的网名)。"""
    return R.Resolver(wb, cascade_mode="force", append_to_logic=False, append_to_mux=False)


# ───────────────────────────── 单行分析结果 ─────────────────────────────
class PageResult:
    """一个页本地信号的分析结果（GUI 编辑器/导出消费，鸭子兼容 topout.TopoutResult 的关键属性）。"""

    def __init__(self, sig, page, kind):
        self.sig = sig                  # LogicSignal / MuxGroup
        self.page = page                # 'logic'/'mux'/'dft'/'iddq'
        self.kind = kind                # 'logic'(AST) / 'mux'(case 枚举)
        self.name = sig.out_name
        self.owner = getattr(sig, "owner", "") or ""
        self.out_width = sig.out_width or 1
        self.node = None                # AST(logic 类)
        self.bindings = None            # 叶子绑定
        self.groups = None              # 输入分组(logic 类)
        self.expansion = None           # mux 展开(mux 类)
        self.vectors = []
        self.meta = {}
        self.chain = []                 # 页本地无 cone → 恒空(显示链=单行原式代入)
        self.status = "ok"              # ok / error
        self.issues = []
        self.note = ""

    @property
    def n_leaves(self):
        if self.kind == "mux" and self.expansion is not None:
            return len(self.expansion.get("bindings", {}) or {})
        return len(self.bindings or {})


def analyze_logiclike(wb, resolver, sig, mode="min", max_tests=256, exhaustive=False,
                      want_vectors=True):
    """logic 形态(logic/dft/iddq)的页本地分析：解析表达式 + 一层解析输入 + 出向量。永不抛。"""
    res = PageResult(sig, sig.suffix if sig.suffix in PAGES else "logic", "logic")
    try:
        node = E.parse(sig.expr)
        bindings = resolver.resolve_signal_inputs(sig)   # 一层解析、不 cone（级联输入=force 衔接网）
        res.node, res.bindings = node, bindings
        res.groups = V.input_groups(node, bindings)
        if want_vectors:
            res.vectors, res.meta = V.generate_vectors(
                node, bindings, sig.out_width, mode=mode, max_tests=max_tests,
                exhaustive=exhaustive)
    except E.ExprError as ex:
        res.status = "error"
        res.issues.append("表达式解析/求值失败: %s" % ex)
    except Exception as ex:    # noqa: BLE001 —— 护栏3：永不抛，意外异常记账成 error
        res.status = "error"
        res.issues.append("分析异常: %r" % ex)
    return res


def analyze_mux(wb, resolver, grp, mode="min", max_tests=256, exhaustive=False,
                want_vectors=True):
    """mux 组的页本地分析：force 模式展开(控制不 cone 上游) + 出向量。永不抛。"""
    res = PageResult(grp, "mux", "mux")
    try:
        exp = mux_gen.expand_mux_group(wb, resolver, grp)
        res.expansion = exp
        res.bindings = exp.get("bindings", {})
        res.issues.extend(exp.get("issues", []))
        if exp.get("issues"):
            # 解析有问题(规格冲突/未解析)→ 仍展示输入行、但无真值表；状态 error 记账(不静默丢)
            res.status = "error"
            return res
        if want_vectors:
            mux_mode = mux_gen.coverage_mode(mode, exhaustive)
            res.vectors, res.meta = mux_gen.make_mux_vectors(
                grp, exp, mode=mux_mode, max_tests=max_tests)
    except Exception as ex:    # noqa: BLE001 —— 护栏3：永不抛
        res.status = "error"
        res.issues.append("mux 分析异常: %r" % ex)
    return res


def analyze_page_signal(wb, resolver, sig, page, mode="min", max_tests=256,
                        exhaustive=False, want_vectors=True):
    """页本地分析分发：mux 组走 case 枚举，logic 形态走 AST。"""
    if isinstance(sig, M.MuxGroup):
        return analyze_mux(wb, resolver, sig, mode=mode, max_tests=max_tests,
                           exhaustive=exhaustive, want_vectors=want_vectors)
    return analyze_logiclike(wb, resolver, sig, mode=mode, max_tests=max_tests,
                             exhaustive=exhaustive, want_vectors=want_vectors)


def analyze_all(wb, page, mode="min", max_tests=256, exhaustive=False, want_vectors=True):
    """对某一页全清单逐行分析。返回 list[PageResult]。"""
    resolver = _page_resolver(wb)
    return [analyze_page_signal(wb, resolver, s, page, mode=mode, max_tests=max_tests,
                                exhaustive=exhaustive, want_vectors=want_vectors)
            for s in page_signals(wb, page)]


# ───────────────────────────── 真值表显示模型（与 topout 同口径） ─────────────────────────────
def _logic_table(res):
    """logic 形态结果 → 真值表显示 dict（inputs/tests/auto_label/exp_label）。"""
    groups = res.groups or []
    inputs = [G._input_meta(g, res.bindings) for g in groups]
    out_w = res.out_width or 1
    slc = "[%d:0]" % (out_w - 1) if out_w > 1 else ""
    tests = []
    for vec in res.vectors:
        bv = V.vector_to_base_values(vec, groups)
        tests.append({
            "name": W.test_label(vec), "neg": vec.is_negative,
            "values": [G._fmt_cell(bv.get(g["key"], 0), g["width"]) for g in groups],
            "auto_out": G._fmt_cell(vec.exp_value, vec.exp_width),
            "expected": G._fmt_cell(vec.asserted_value, vec.exp_width),
        })
    return {"inputs": inputs, "tests": tests,
            "auto_label": "auto_out%s" % slc, "exp_label": "期望(out)%s" % slc}


def _mux_table(res):
    """mux 结果 → 真值表显示 dict（行=控制+各数据寄存器，列=测试；镜像 generator 报告 mux 表，去 DFT 门）。"""
    grp, exp = res.sig, res.expansion
    used = exp["used_vars"]
    bindings = exp["bindings"]
    inputs = []
    for key in used:
        b = bindings[key]
        label = b.base + ("[%d:0]" % (b.width - 1) if b.width > 1 else "")
        tag = {"ctrl": "ctrl", "data": "data", "upstream": "上游mux"}[mux_gen.key_role(key)]
        inputs.append({"label": label, "letters": "%s(%s)" % (key, tag),
                       "base": b.base, "width": b.width, "kind": b.kind,
                       "ro": (b.kind == "RO"), "addr": b.address, "wire": b.wire,
                       "reg_lsb": b.reg_lsb, "reg_msb": b.reg_msb,
                       "slice_msb": b.slice_msb, "slice_lsb": b.slice_lsb})
    out_w = grp.out_width or 1
    slc = "[%d:0]" % (out_w - 1) if out_w > 1 else ""
    tests = []
    for vec in res.vectors:
        tests.append({
            "name": W.test_label(vec), "neg": vec.is_negative,
            "values": [G._fmt_cell(vec.assignments.get(k, 0), bindings[k].width) for k in used],
            "auto_out": G._fmt_cell(vec.exp_value, vec.exp_width),
            "expected": G._fmt_cell(vec.asserted_value, vec.exp_width),
        })
    return {"inputs": inputs, "tests": tests,
            "auto_label": "auto_out%s" % slc, "exp_label": "期望(out)%s" % slc}


def result_to_model(res):
    """PageResult → GUI/测试消费的视图模型 dict（与 topout.topout_view_models 同键）。"""
    m = {"name": res.name, "owner": res.owner, "width": res.out_width,
         "kind": res.kind, "status": res.status, "note": res.note,
         "issues": list(res.issues), "matched_name": res.name.lower(),
         "n_leaves": res.n_leaves, "n_vectors": len(res.vectors),
         "expr": getattr(res.sig, "expr", ""), "page": res.page,
         "chain": [], "inputs": [], "tests": [], "auto_label": "", "exp_label": ""}
    if res.status == "ok":
        tbl = _mux_table(res) if res.kind == "mux" else _logic_table(res)
        m.update(tbl)
        m["n_vectors"] = len(tbl["tests"])
    return m


def page_view_models(wb, page, mode="min", max_tests=256, exhaustive=False):
    """某一页的【视图模型清单】（GUI 子视图 / 无头测试消费），按页行序。"""
    return [result_to_model(r)
            for r in analyze_all(wb, page, mode=mode, max_tests=max_tests,
                                 exhaustive=exhaustive)]
