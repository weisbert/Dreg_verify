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
    out_net       str              该信号 assert LHS 的网名（**不带探针前缀**；GUI v2 §7-1）

    status_detail str              状态细分档（STATUS_DETAILS 之一；GUI v2 §7-2）

GUI v2 C0-a（2026-09-12）新增的键全部 additive（只加不改），老消费方逐字节不受影响：
    out_net       见上。前缀由 provider 另补 an["probe_prefix"]（它才有 probe_prefixes 配置）。
    status_detail 判据只取结构化字段，缺判据的档退回 "clean" 并保留 issues 原文（绝不猜文本）。

搬家说明（2026-09-12，GUI v2 阶段 A4c）：本模块四个函数原本长在 gui.py 里，
是纯函数、不碰 Qt；抽出来后 gui.py 保留同名薄委托，行为逐字节不变。
"""

import re

from . import excel_model
from . import generator
from . import vectors as V

__all__ = ["SV_VAR_RE", "subst_expr", "order_groups_fortest",
           "norm_topout_result", "norm_page_result", "STATUS_DETAILS", "status_detail"]

#: an["status_detail"] 的取值（= ui/terms.STATUS_KEYS 去掉 "pending"——那档是骨架清单专用、
#: 引擎从不产出）。清单状态列/详情徽标/行内原因块按它取文案；老的 an["status"] 四档一字不动。
STATUS_DETAILS = ("clean", "wire-fallback", "needs-prefix", "risky-generated", "bare-probe",
                  "false-green", "spec-collision", "parse-err", "unresolved", "skip", "error")

#: 输入网埋在子模块 / 是上游 mux 衔接网 → 必须配层级前缀才 force 得到（缺前缀 = 默认整组跳过）
_RISKY_FOUND_IN = ("needs-prefix", "mux-output")
#: 表里查无、按命名约定当线网 force（prefixed-wire = 配了前缀的同一类：前缀是用户填的、
#: 网名仍未经任何校验，同属「名字是猜的」，R41）
_GUESSED_FOUND_IN = ("wire", "prefixed-wire")

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


def _leaf_bindings(res):
    """该结果的全部叶子绑定：logic/register 在 res.bindings，mux 在 res.expansion["bindings"]。"""
    out = []
    for b in (getattr(res, "bindings", None) or {}).values():
        if b is not None:
            out.append(b)
    exp = getattr(res, "expansion", None)
    if isinstance(exp, dict):
        for b in (exp.get("bindings") or {}).values():
            if b is not None:
                out.append(b)
    return out


def _src_obj(res):
    """源对象（TopoutResult → root.obj，PageResult → sig）；直连寄存器/RO 回读根为 None。"""
    root = getattr(res, "root", None)
    if root is not None:
        return getattr(root, "obj", None)
    return getattr(res, "sig", None)


def _is_bare_probe(res, probe_prefix):
    """输出侧「名字是猜的」：断言 LHS 贴的不是声明的那个名字，而是按命名约定推出来的内部衔接网。

    判据（结构化、两条流水线同一条）：**assert LHS 网名 ≠ 该信号声明的输出名**，且没配探针前缀。
      · Topout 视图：声明名 = Topout B 列真名；out_net = 源对象 rtl_base——两者不等就说明断言贴的是
        `d_x_to_mux` 这类内部衔接网（裸名 force/probe，探不探得到只有仿真知道）。
      · 页本地视图：声明名 = 本页 D/输出列基名；out_net = 同一行的 rtl_base（带 _to_logic 等尾缀）。
    刻意**不**用 `top_output`/`is_top`（N 列 / I 列）：topout.py 已实证真表里这两列全 0，据它判档
    等于给每个 logic/mux 根都刷一条恒定告警——`_topout_report_core` 早有拍板「不刷 top_out=0 假警告」。
    直连寄存器 / RO 回读根：输出就是 tmm/regmap 里查到的寄存器字段，不是猜的 → 永不命中。
    dft 改名根：探针贴顶层真名（root.probe_name）→ 永不命中。
    """
    if probe_prefix:
        return False
    root = getattr(res, "root", None)
    if root is not None:
        if getattr(root, "kind", "") not in ("logic", "mux") or getattr(root, "renamed", False):
            return False
        declared, net = getattr(res.topo, "name", ""), _topout_out_net(res)
    else:
        obj = _src_obj(res)
        declared = getattr(obj, "out_base", "") or getattr(res, "name", "")
        net = getattr(obj, "rtl_base", "") or declared
    return bool(net) and str(net).strip().lower() != str(declared).strip().lower()


def status_detail(res, include_risky=True, probe_prefix=""):
    """一个分析结果 → `an["status_detail"]`（STATUS_DETAILS 之一）。两条流水线共用。

    判据**只**来自结构化字段（res.status / res.bindings / res.expansion / res.meta），
    绝不在 issues/note 的文本里做字符串猜——缺判据的档一律退回 "clean" 并原样保留 issues。
    优先级（严重度递减，与 generator.analyze_signal / analyze_mux_group 的既有档同口径）：

      spec-collision  expansion["spec_conflicts"] / meta["spec_collision"]：同选择值选不同源
      false-green     meta["value_collision"] / meta["override_collision"]：字段太窄=假的 PASS
      unresolved      任一叶子 binding.resolved=False（照这样生成仿真会报找不到网）
      needs-prefix    任一 binding.found_in ∈ needs-prefix/mux-output 且【没开】强制生成 → 跳过
      risky-generated 同上但【开了】强制生成（include_risky=True，Topout/页视图当前写死 True）
      wire-fallback   任一 binding.found_in ∈ wire/prefixed-wire：输入名是按命名约定猜的
      bare-probe      输出侧名字是猜的：assert LHS ≠ 声明名且没配前缀（见 _is_bare_probe）
      clean           以上都不命中

    include_risky：调用方的『缺前缀是否强制生成』开关（默认 True = 引擎现状：topout 与
    pageviews 都写死 include_risky=True），决定缺前缀落 needs-prefix 还是 risky-generated。
    probe_prefix：该信号 out_net 配的探针前缀（provider 才有这份配置；没有就按裸名判）。
    """
    status = getattr(res, "status", "") or ""
    if status == "unresolved":
        return "unresolved"
    if status == "error":
        return "parse-err"
    if status == "skip":
        return "skip"
    if status != "ok":
        return "error"
    meta = getattr(res, "meta", None) or {}
    exp = getattr(res, "expansion", None)
    exp = exp if isinstance(exp, dict) else {}
    if exp.get("spec_conflicts") or meta.get("spec_collision"):
        return "spec-collision"
    if meta.get("value_collision") or meta.get("override_collision"):
        return "false-green"
    bs = _leaf_bindings(res)
    if any(not getattr(b, "resolved", True) for b in bs):
        return "unresolved"
    if any(getattr(b, "found_in", None) in _RISKY_FOUND_IN for b in bs):
        return "risky-generated" if include_risky else "needs-prefix"
    if any(getattr(b, "found_in", None) in _GUESSED_FOUND_IN for b in bs):
        return "wire-fallback"
    if _is_bare_probe(res, probe_prefix):
        return "bare-probe"
    return "clean"


def norm_topout_result(res, wb=None, include_risky=True, probe_prefix=""):
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
    # §7-1：assert LHS 的网名（与 build_for_topout 的 .sv 同口径，**不带探针前缀**——前缀是配置，
    # 由拿着 probe_prefixes 的 provider 另补 an["probe_prefix"]，免此处再吃一份配置参数）。
    an["out_net"] = _topout_out_net(res)
    # §7-2：状态八档细分（老的 an["status"] 四档不动；判据见 status_detail）
    an["status_detail"] = status_detail(res, include_risky=include_risky,
                                        probe_prefix=probe_prefix)
    return an


def _topout_out_net(res):
    """Topout 结果的 assert LHS 网名；惰性 import topout（topout 也会 import 本模块，避免环）。"""
    try:
        from . import topout as T
        return T._topout_probe_net(res)
    except Exception:      # noqa: BLE001 —— 归一化绝不抛（拿不到就空串，显示层自行兜底）
        return ""


def norm_page_result(res, wb=None, include_risky=True, probe_prefix=""):
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
    # §7-1：页本地视图的 assert LHS = 本页源对象的 RTL 网基名（同样不带探针前缀）。
    an["out_net"] = getattr(res.sig, "rtl_base", None) or res.name
    # §7-2：与 Topout 视图同一判据（两条流水线的状态档口径必须一致）
    an["status_detail"] = status_detail(res, include_risky=include_risky,
                                        probe_prefix=probe_prefix)
    return an
