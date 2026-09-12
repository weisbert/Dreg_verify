# -*- coding: utf-8 -*-
"""analysis_norm.py — 分析结果【归一化】层（不依赖 Qt）。

两条分析流水线产出两种结果对象：

  · `topout.TopoutResult` —— Topout 视图（顶层真名，展到源寄存器）
  · `pageviews.PageResult` —— logic / mux / dft / iddq 页本地子视图

界面层不该认这两种对象。本模块把它们都归一成同一个 dict（下文统称 **an**），
新门面 SignalView、『排查(旧)』、以及 v2 的输入信号表/驱动明细都只吃 an。

an 的字段（两条流水线保证同名同义）::

    kind          logic / register / mux / ro-readback / unresolved
    status        ok / skip / unresolved / error
    issues        [str]            该信号的告警（跳过原因、规格冲突…）
    note          str              人读补充说明
    node          表达式 AST（mux 为 None）
    bindings      {变量字母: Binding}（mux 为 None，绑定在 expansion 里）
    expansion     mux 展开 dict（logic 为 None）
    vectors       [TestVector]     该信号的测试向量
    out_width     int              输出位宽
    chain         [{"out","expr","subst"}]  逐层展开链
    name          str              信号显示名
    sig           源对象（LogicSignal / MuxGroup / None）
    groups        [输入分组 dict]  已按 for_test 行序排好（wb 传入时）
    src_out_name  编辑回流键
    editable      "" / "logic" / "mux"
    renamed       bool             改名根（编辑走寄存器路径按顶层名键）
    dft_gate      None 或 {"key","label","wire_lhs","transp","width","binding"}

搬家说明（2026-09-12，GUI v2 阶段 A4c）：本模块四个函数原本长在 gui.py 里，
是纯函数、不碰 Qt；抽出来后 gui.py 保留同名薄委托，行为逐字节不变。
"""

import re

from . import excel_model
from . import generator
from . import vectors as V

__all__ = ["SV_VAR_RE", "subst_expr", "order_groups_fortest",
           "norm_topout_result", "norm_page_result"]

# 表达式里的单字母变量（A-J）——代入真实信号名时用
SV_VAR_RE = re.compile(r"(?<![A-Za-z0-9_])([A-J])(?![A-Za-z0-9_])")


def subst_expr(expr, name_of):
    """把单字母变量(A-J)替换成真实信号名，给展开链『字母代入』一行用（多级链由分析结果直接给）。"""
    return SV_VAR_RE.sub(lambda m: name_of.get(m.group(1), m.group(1)), expr or "")


def order_groups_fortest(groups, bindings, wb, out_base):
    """输入分组按 for_test 行序排（寄存器地址+bit 位 / for_test 样例组）——与 generator.report/HTML/
    for_test 同一口径(m4)，免 GUI 可编辑真表/CSV 与导出两套行序、人工核对/截图错位。groups 对象不变、
    只换顺序 → 列 vals(按 group['key'] 绑)/真值表求值均不受影响(纯显示重排)。wb=None 或空 → 原序。"""
    if not groups or wb is None:
        return groups

    def _name(g):
        return excel_model._strip_width(g.get("base") or g.get("label") or "")[0].lower()

    def _key(g):
        b = (bindings or {}).get(g.get("rep"))
        return ((b.address, b.reg_lsb) if (b is not None and getattr(b, "address", None) is not None)
                else (None, None))
    return generator.fortest_order_entries(groups, wb, out_base, _name, key_fn=_key)


def _gate_dict(res):
    """res.dft_gate = (Binding, 透传值) → an["dft_gate"]；无门返回 None。

    `binding` 一并带上（A4c）：输入信号表要拿它出『类型(RO/RW)』与『驱动』串，
    只有 base/wire_lhs 拼不出来。纯 additive，老消费方按 key/label/wire_lhs/transp/width 取值不受影响。
    """
    g = getattr(res, "dft_gate", None)
    if not g:
        return None
    return {"key": "__dft_gate__", "label": g[0].base, "wire_lhs": g[0].wire_lhs,
            "transp": int(g[1]), "width": 1, "binding": g[0]}


def norm_topout_result(res, wb=None):
    """topout.TopoutResult → SignalView 统一分析 dict（编辑/导出消费）。wb 传入则 logic 根输入按
    for_test 行序排（m4，与报告/导出一致）。"""
    kind = res.root.kind
    sig = res.root.obj
    an = {"kind": kind, "status": res.status, "issues": list(res.issues),
          "note": res.note, "node": res.node, "bindings": res.bindings,
          "expansion": res.expansion, "vectors": list(res.vectors),
          "out_width": res.out_width, "chain": list(res.chain),
          "name": res.topo.name, "sig": sig}
    an["groups"] = (V.input_groups(res.node, res.bindings)
                    if (kind in ("logic", "register") and res.node is not None
                        and res.bindings is not None) else [])
    if kind == "logic" and an["groups"]:        # m4：logic 根输入按 for_test 行序(register 报告本就原序)
        an["groups"] = order_groups_fortest(an["groups"], res.bindings, wb,
                                            sig.out_base if sig is not None else res.topo.name)
    # 编辑回流键：logic/mux → 源对象 out_name（generator.build 据此选）；register → Topout 名（reg_overrides）
    an["src_out_name"] = (sig.out_name if (kind in ("logic", "mux") and sig is not None)
                          else res.topo.name)
    # ⭐只有 status==ok 才可编辑：error/skip/unresolved 的 mux 没有 expansion(None)，若仍判 editable=='mux'，
    #   点选该行 _load_signal 会对 None 取 ['used_vars'] 崩（违『绝不崩』）。统一非可建=非可编辑。
    an["editable"] = "" if res.status != "ok" else (
        "logic" if kind in ("logic", "register") else ("mux" if kind == "mux" else ""))
    an["renamed"] = bool(getattr(res.root, "renamed", False))   # dft 改名根：编辑走 reg 路按顶层名键
    # 钉上的 iddq DFT 门 → 真值表只读输入行（门在向量 extra_forces 里、不在展开后的输入分组里，
    # 否则 GUI 真值表看不见这根门，与 .sv/报告不一致，2026-06-25 d_en_vco_fc_ls 实证）
    an["dft_gate"] = _gate_dict(res)
    return an


def norm_page_result(res, wb=None):
    """pageviews.PageResult → SignalView 统一分析 dict。wb 传入则 logic 形态输入按 for_test 行序(m4)。"""
    an = {"kind": res.kind, "status": res.status, "issues": list(res.issues),
          "note": res.note, "node": res.node, "bindings": res.bindings,
          "expansion": res.expansion, "vectors": list(res.vectors),
          "out_width": res.out_width, "chain": list(res.chain),
          "name": res.name, "sig": res.sig}
    an["groups"] = (res.groups or []) if res.kind == "logic" else []
    if res.kind == "logic" and an["groups"]:    # m4：页 logic 视图输入按 for_test 行序，与页报告一致
        an["groups"] = order_groups_fortest(an["groups"], res.bindings, wb, res.sig.out_base)
    an["src_out_name"] = res.sig.out_name
    an["editable"] = "" if res.status != "ok" else (
        "logic" if res.kind == "logic" else ("mux" if res.kind == "mux" else ""))
    an["renamed"] = False               # 页本地子视图无 dft 改名根（每行就是本页声明名）
    # M2：钉上的 iddq DFT 门 → 真值表只读输入行（与 Topout 视图 norm_topout_result 同口径，门在向量
    # extra_forces 里、不在输入分组里，否则页子视图真表看不见门、与同页 .sv 不一致）。
    an["dft_gate"] = _gate_dict(res)
    return an
