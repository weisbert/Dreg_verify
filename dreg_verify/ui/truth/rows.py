# -*- coding: utf-8 -*-
"""truth/rows.py —— 真值表【输入行】的构造（Qt-free）。

`e_inputs` 是真值表上半部分的行模型：一行一个输入信号，列模型（`edits.py` 的 col schema）
按 `key` 存每列在这一行的取值。v1 里它是 `gui.SignalView._load_signal` 里的一段内联代码
（gui.py:1084–1103），v2 把它单独拎出来——`TruthModel` 与 `truth/panel.py` 都要用，
而且它 Qt-free、能直接单测（`tests/test_ui_truth_model.py::test_e_inputs_matches_v1_signalview`
逐字段对着 v1 的 `SignalView.e_inputs` 比）。

行 dict 的九个键（`edits.py` 的列操作只读这些）：
    key            列模型里这一行取值的键（logic=分组 key，mux=expansion 的赋值键，DFT 门=固定键）
    label          冻结列上显示的行标签（`inputs_table.vheader_display`：真名　(角色 · 端口)）
    width          位宽（写入时按它 mask）
    editable       logic 根的 cone 输入 = True（mux/DFT 门 = False；真正可编辑判定在
                   `edits.input_cell_editable`，它另外放行 mux 的数据角色行）
    control        控制/选择位（冻结列加粗）
    mux_data_base  mux 数据角色行的【物理基名】（整表 by_base 同步要它）；其余 None
    is_dft_gate    iddq DFT 门行（只读、紫字）
    wire_lhs       DFT 门的 force 网名；其余 None
    transp         DFT 门的功能拍透传值；其余 None

行序 = C-076：logic 根按 `an["groups"]`（`analysis_norm.order_groups_fortest` 已按 for_test
页行序排好），mux 根按 `expansion["used_vars"]`（控制在前、数据在后），DFT 门行殿后。
**与 `inputs_table.input_rows` 同口径**，两边不许各排各的。
"""

import re

from ... import inputs_table as IT

__all__ = ["e_inputs_from_an", "E_INPUT_KEYS", "key_role"]

#: 行 dict 的键集——C3-b/c/d 与 `edits.py` 都按这一组取值，缺一个就是静默少一格
E_INPUT_KEYS = ("key", "label", "width", "editable", "control",
                "mux_data_base", "is_dft_gate", "wire_lhs", "transp")

_RE_CTRL_KEY = re.compile(r"^c\d*:")


def key_role(key, data_keys=()):
    """mux 赋值键的角色：'ctrl' / 'data' / 'upstream'。

    与 `mux_gen.key_role` **同一判据**（I-19 不许 ui/ import 引擎模块，故这里照抄一份；
    `tests/test_ui_truth_model.py::test_key_role_matches_mux_gen` 拿两张 mirror 上全部 mux
    信号的 used_vars 逐键对着 `mux_gen.key_role` 断言相等，漂了当场红）。

    `data_keys` 给了就先按【结构】认数据键（expansion 的 data_keys 是权威名单），
    命名约定只用来在其余键里区分「本地控制」与「上游 mux 配方」。
    """
    k = str(key or "")
    if k in set(data_keys or ()):
        return "data"
    if "." in k.split(":")[0] and k.startswith("m"):
        return "upstream"
    if _RE_CTRL_KEY.match(k):
        return "ctrl"
    return "data"


def _row(key, label, width, editable=False, control=False, mux_data_base=None,
         is_dft_gate=False, wire_lhs=None, transp=None):
    """统一的行 dict——九个键一个不少（消费方一律 `e[...]` 直接取，不用 `.get` 兜底）。"""
    return {"key": key, "label": label, "width": int(width or 1),
            "editable": bool(editable), "control": bool(control),
            "mux_data_base": mux_data_base, "is_dft_gate": bool(is_dft_gate),
            "wire_lhs": wire_lhs, "transp": transp}


def e_inputs_from_an(an):
    """分析结果 `an` → 真值表输入行列表（`TruthModel.load` 的第三个参数）。

    `an` 为空 / 不可编辑（`an["editable"] == ""`）时输入行仍按结构给得出来就给
    （C-134 只读全表也要看得见行），给不出来就空表——**绝不抛**。
    """
    if not an:
        return []
    out = []
    kind = an.get("editable") or ""
    if kind == "logic":
        for g in (an.get("groups") or []):
            out.append(_row(key=g["key"], label=IT.vheader_display(g, an),
                            width=g["width"], editable=True,
                            control=bool(g.get("is_control"))))
    elif kind == "mux":
        exp = an.get("expansion") or {}
        bindings = exp.get("bindings") or {}
        data_keys = exp.get("data_keys") or []
        for k in (exp.get("used_vars") or []):
            b = bindings.get(k)
            if b is None:
                continue
            role = key_role(k, data_keys)
            lbl = IT.mux_label(b)
            out.append(_row(
                key=k,
                label=IT.vheader_display({"name": lbl, "key": k,
                                          "is_control": role == "ctrl"}, an),
                width=b.width, editable=False, control=(role == "ctrl"),
                mux_data_base=(b.base.lower() if role == "data" and b.base else None)))
    # iddq DFT 门：只读输入行（门在向量 extra_forces 里、不是 cone 输入，不单列一行就与
    # .sv / 报告 / for_test 对不上——2026-06-10 Hi1108 实地反馈，C-128）
    gate = an.get("dft_gate")
    if gate:
        out.append(_row(key=gate["key"],
                        label=IT.vheader_display({"label": gate["label"], "key": gate["key"],
                                                  "is_dft_gate": True}, an),
                        width=gate.get("width", 1), editable=False, control=False,
                        is_dft_gate=True, wire_lhs=gate["wire_lhs"],
                        transp=gate["transp"]))
    return out
